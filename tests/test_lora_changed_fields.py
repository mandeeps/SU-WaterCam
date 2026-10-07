"""LoRa sends one sensor packet: the capture time plus the fields that changed
past the threshold since they were last sent. No second, full sensor packet."""

import os
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import sensor_changes as sc


class TestChangedFields:
    def test_first_reading_always_qualifies(self, tmp_path):
        out = sc.changed_fields({"temperature_celsius": 20.0, "timestamp": 1}, state_path=str(tmp_path / "s.json"))
        assert list(out) == ["temperature_celsius"]

    def test_compared_with_last_sent_not_last_reading(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"battery_pct": 100}, state_path=path)
        # 3 % then 6 % below the value last sent: the second qualifies even though
        # it is only 3 % below the previous reading
        assert sc.changed_fields({"battery_pct": 97}, state_path=path) == {}
        assert "battery_pct" in sc.changed_fields({"battery_pct": 94}, state_path=path)

    def test_threshold_and_zero(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"a": 10.0, "z": 0}, state_path=path)
        out = sc.changed_fields({"a": 10.4, "z": 0}, threshold=0.05, state_path=path)
        assert out == {}
        assert set(sc.changed_fields({"a": 10.5, "z": 1}, state_path=path)) == {"a", "z"}

    def test_non_numeric_qualifies_when_different(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"mode": "normal"}, state_path=path)
        assert sc.changed_fields({"mode": "normal"}, state_path=path) == {}
        assert "mode" in sc.changed_fields({"mode": "emergency"}, state_path=path)

    def test_timestamp_never_stored(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"timestamp": 5, "a": 1}, state_path=path)
        assert sc._load(path) == {"a": 1}

    def test_unreadable_state_means_send_everything(self, tmp_path):
        path = tmp_path / "s.json"
        path.write_text("not json")
        assert "a" in sc.changed_fields({"a": 1}, state_path=str(path))


def _run_capture(tmp_path, temperature, joined=True, bitmap=b"\x01\x00\x40\x00\x30\x00"):
    """Run lora_token_with_tracker once; return the handler mock."""
    import ticktalk_main
    handler = MagicMock()
    handler.is_joined.return_value = joined
    cap = tmp_path / "20261007-120000"
    cap.mkdir(exist_ok=True)
    (cap / "capture_time").write_text("1791400000\n")
    with ExitStack() as st:
        st.enter_context(patch.object(sc, "DEFAULT_STATE_PATH", str(tmp_path / "last_sent.json")))
        st.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=handler))
        st.enter_context(patch("tools.lora_handler_concurrent.get_config_value", side_effect=lambda k, d=None: d))
        st.enter_context(patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d=None: d))
        st.enter_context(patch("tools.bno055_imu.get_orientation", return_value={}))
        st.enter_context(patch("tools.aht20_temperature.get_aht20",
                               return_value={"temperature_celsius": temperature, "relative_humidity": 50}))
        st.enter_context(patch("tools.get_gps.get_location_with_retry", return_value=(None, None)))
        st.enter_context(patch("tools.wittypi_control.get_wittypi_status", return_value={"status": "none"}))
        st.enter_context(patch("tools.battery_manager.get_battery_status",
                               return_value={"battery_pct": 80, "battery_source": "test"}))
        st.enter_context(patch("tools.pi_power.get_throttled", return_value=0))
        st.enter_context(patch("tools.lora_store_forward.drain", return_value={"drained": 0, "failed": False}))
        enq = st.enter_context(patch("tools.lora_store_forward.enqueue", return_value=True))
        ticktalk_main.lora_token_with_tracker.__wrapped__(
            bitmap, {"change_threshold": 0.05, "previous_values": {}}, str(cap))
    handler.enqueue = enq
    return handler


def _sensor_packets(handler):
    return [c.args[0] for c in handler.queue_transmit.call_args_list
            if "flood_bitmap_compressed" not in c.args[0]]


class TestLoRaSend:
    def test_one_sensor_packet_with_capture_time_and_no_full_packet(self, tmp_path):
        h = _run_capture(tmp_path, 20.0)
        pkts = _sensor_packets(h)
        assert len(pkts) == 1
        assert pkts[0]["timestamp"] == 1791400000
        assert pkts[0]["temperature_celsius"] == 20.0
        h.queue_binary_transmit.assert_not_called()

    def test_unchanged_fields_are_not_sent_again(self, tmp_path):
        _run_capture(tmp_path, 20.0)
        h = _run_capture(tmp_path, 20.5)          # 2.5 %: below the threshold
        assert _sensor_packets(h) == []
        h = _run_capture(tmp_path, 21.5)          # 7.5 % above the value last sent
        pkts = _sensor_packets(h)
        assert len(pkts) == 1 and set(pkts[0]) == {"timestamp", "temperature_celsius"}

    def test_bitmap_packet_still_sent_without_capture_time(self, tmp_path):
        h = _run_capture(tmp_path, 20.0)
        bitmaps = [c.args[0] for c in h.queue_transmit.call_args_list if "flood_bitmap_compressed" in c.args[0]]
        assert bitmaps and "timestamp" not in bitmaps[0]

    def test_queued_fields_count_as_sent(self, tmp_path):
        h = _run_capture(tmp_path, 20.0, joined=False)
        h.enqueue.assert_called_once()
        h = _run_capture(tmp_path, 20.0, joined=False)
        sensor_hex = h.enqueue.call_args.args[0]
        assert sensor_hex is None                 # nothing changed: no sensor payload queued


class TestPeriodicFullSend:
    def test_due_when_never_sent_or_older_than_period(self, tmp_path):
        path = str(tmp_path / "s.json")
        assert sc.full_send_due(24, state_path=path, now=1_000_000)
        sc.mark_sent({"a": 1}, state_path=path, full=True, now=1_000_000)
        assert not sc.full_send_due(24, state_path=path, now=1_000_000 + 23 * 3600)
        assert sc.full_send_due(24, state_path=path, now=1_000_000 + 24 * 3600)

    def test_partial_send_does_not_reset_the_period(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"a": 1}, state_path=path, full=True, now=1_000_000)
        sc.mark_sent({"a": 2}, state_path=path, now=1_000_000 + 20 * 3600)
        assert sc.full_send_due(24, state_path=path, now=1_000_000 + 24 * 3600)

    def test_everything_returns_unchanged_fields_too(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"a": 1, "b": "x"}, state_path=path)
        assert set(sc.changed_fields({"a": 1, "b": "x", "timestamp": 9}, state_path=path, everything=True)) == {"a", "b"}

    def test_unchanged_fields_go_out_once_the_period_has_passed(self, tmp_path):
        import json
        _run_capture(tmp_path, 20.0)                       # first capture: full send
        assert _sensor_packets(_run_capture(tmp_path, 20.0)) == []
        state_file = tmp_path / "last_sent.json"
        state = json.loads(state_file.read_text())
        state[sc.LAST_FULL_KEY] -= 25 * 3600              # a day has passed
        state_file.write_text(json.dumps(state))
        pkts = _sensor_packets(_run_capture(tmp_path, 20.0))
        assert len(pkts) == 1
        assert {"timestamp", "temperature_celsius", "relative_humidity"} <= set(pkts[0])
        assert _sensor_packets(_run_capture(tmp_path, 20.0)) == []   # and not again until the next period
