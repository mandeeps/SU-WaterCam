"""LoRa sends one sensor packet: the capture time plus the fields that changed
past the threshold since they were last sent. No second, full sensor packet."""

import os
import sys

import pytest
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


def _run_capture(tmp_path, temperature, joined=True, bitmap=b"\x01\x00\x40\x00\x30\x00", orientation=None,
                 size_limit=242, transmit_ok=True, daemon=True, bitmap_send=None):
    """Run lora_token_with_tracker once; return the handler mock.

    daemon=False: get_lora_handler() returns None (LoRa daemon down);
    "raise": it raises. bitmap_send forces the bitmap policy's decision."""
    import ticktalk_main
    handler = MagicMock()
    handler.is_joined.return_value = joined
    handler.size_limit_now.return_value = size_limit
    handler.transmit.return_value = transmit_ok
    from tools.lora_handler_concurrent import _encode_compressed_packet
    handler.compressed_encoding.side_effect = _encode_compressed_packet
    cap = tmp_path / "20261007-120000"
    cap.mkdir(exist_ok=True)
    (cap / "capture_time").write_text("1791400000\n")
    with ExitStack() as st:
        st.enter_context(patch.object(sc, "DEFAULT_STATE_PATH", str(tmp_path / "last_sent.json")))
        if daemon is True:
            st.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=handler))
        elif daemon == "raise":
            st.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler",
                                   side_effect=OSError("socket gone")))
        else:
            st.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=None))
        if bitmap_send is not None:
            st.enter_context(patch("tools.bitmap_policy.lora_decision", return_value=(bitmap_send, "test")))
        st.enter_context(patch("tools.drop_counter.DEFAULT_PATH", str(tmp_path / "sf_dropped.json")))
        st.enter_context(patch("tools.lora_handler_concurrent.get_config_value", side_effect=lambda k, d=None: d))
        st.enter_context(patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d=None: d))
        st.enter_context(patch("tools.bno055_imu.get_orientation",
                               return_value={"tilt_roll_yaw": orientation} if orientation else {}))
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


def _daemon_events(tmp_path):
    from tools import drop_counter
    return drop_counter.events(str(tmp_path / "sf_dropped.json"))["lora_daemon_unavailable"]


class TestLoRaDaemonDown:
    @pytest.mark.parametrize("daemon", [False, "raise"])
    def test_cycle_is_queued_and_counted(self, tmp_path, daemon):
        h = _run_capture(tmp_path, 20.0, daemon=daemon, bitmap_send=True)
        h.enqueue.assert_called_once()
        sensor_hex, bitmap_hex = h.enqueue.call_args.args[:2]
        assert sensor_hex.startswith("0001") and bitmap_hex.startswith("0818")
        h.transmit.assert_not_called()
        assert _daemon_events(tmp_path) == 1
        from tools import drop_counter
        assert drop_counter.total(str(tmp_path / "sf_dropped.json")) == 0    # queued is not dropped

    def test_bitmap_policy_is_respected(self, tmp_path):
        h = _run_capture(tmp_path, 20.0, daemon=False, bitmap_send=False)
        assert h.enqueue.call_args.args[1] is None

    def test_queued_fields_count_as_sent(self, tmp_path):
        _run_capture(tmp_path, 20.0, daemon=False, bitmap_send=False)
        h = _run_capture(tmp_path, 20.0, daemon=False, bitmap_send=False)
        h.enqueue.assert_not_called()           # nothing changed and no bitmap: nothing to queue
        assert _daemon_events(tmp_path) == 2

    def test_debug_status_reports_it(self, tmp_path):
        from tools import drop_counter, lora_debug_integration as ldi
        path = str(tmp_path / "sf_dropped.json")
        with patch.object(drop_counter, "DEFAULT_PATH", path):
            assert "du" not in dict(ldi.collect_compact_status())
            drop_counter.record("lora", path=path)
            drop_counter.record_event("lora_daemon_unavailable", path=path)
            drop_counter.record("ip", path=path)        # keeps the event count
            fields = dict(ldi.collect_compact_status())
        assert fields["du"] == 1 and fields["sd"] == 2


def _sensor_packets(handler):
    return [c.args[0] for c in handler.compressed_encoding.call_args_list
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
        bitmaps = [c.args[0] for c in h.compressed_encoding.call_args_list if "flood_bitmap_compressed" in c.args[0]]
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


class TestOrientationThreshold:
    """BNO055 Euler angles (heading, roll, pitch): an absolute threshold in degrees."""

    def test_tuple_matches_what_json_stored(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"tilt_roll_yaw": (359.9, 7.06, 85.0)}, state_path=path)   # stored as a list
        assert sc.changed_fields({"tilt_roll_yaw": (359.9, 7.06, 85.0)}, state_path=path) == {}

    def test_jitter_below_threshold_not_sent(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"tilt_roll_yaw": (120.0, 7.0, 85.0)}, state_path=path)
        assert sc.changed_fields({"tilt_roll_yaw": (121.5, 6.2, 85.9)}, state_path=path) == {}
        assert "tilt_roll_yaw" in sc.changed_fields({"tilt_roll_yaw": (122.0, 7.0, 85.0)}, state_path=path)

    def test_heading_wraps_at_360(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"tilt_roll_yaw": (359.5, 0.0, 0.0)}, state_path=path)
        assert sc.changed_fields({"tilt_roll_yaw": (0.5, 0.0, 0.0)}, state_path=path) == {}      # 1 degree
        out = sc.changed_fields({"tilt_roll_yaw": (3.0, 0.0, 0.0)}, state_path=path)              # 3.5 degrees
        assert out["tilt_roll_yaw"]["change_degrees"] == 3.5

    def test_threshold_is_configurable(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"tilt_roll_yaw": (10.0, 0.0, 0.0)}, state_path=path)
        assert sc.changed_fields({"tilt_roll_yaw": (11.0, 0.0, 0.0)}, state_path=path,
                                 angle_threshold_deg=0.5)

    def test_missing_angle_counts_as_changed(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"tilt_roll_yaw": (10.0, 0.0, 0.0)}, state_path=path)
        assert sc.changed_fields({"tilt_roll_yaw": (None, 0.0, 0.0)}, state_path=path)

    def test_lora_capture_with_steady_orientation_sends_nothing(self, tmp_path):
        first = _run_capture(tmp_path, 20.0, orientation=(359.9, 7.06, 85.0))
        assert "tilt_roll_yaw" in _sensor_packets(first)[0]
        # heading across 0, roll and pitch jitter: all under 2 degrees
        assert _sensor_packets(_run_capture(tmp_path, 20.0, orientation=(0.4, 7.3, 84.6))) == []
        moved = _sensor_packets(_run_capture(tmp_path, 20.0, orientation=(5.0, 7.0, 85.0)))
        assert len(moved) == 1 and set(moved[0]) == {"timestamp", "tilt_roll_yaw"}


class TestMissingReadings:
    def test_none_is_never_sent_or_stored(self, tmp_path):
        path = str(tmp_path / "s.json")
        sc.mark_sent({"battery_percent": 39}, state_path=path)
        assert sc.changed_fields({"battery_percent": None}, state_path=path) == {}
        assert sc.changed_fields({"battery_percent": None}, state_path=path, everything=True) == {}


class TestPosition:
    def test_position_sent_first_then_only_after_25_m(self, tmp_path):
        path = str(tmp_path / "s.json")
        assert "lat_lon_z" in sc.changed_fields({"lat_lon_z": [10.0, 20.0, 5.0]}, state_path=path)
        sc.mark_sent({"lat_lon_z": [10.0, 20.0, 5.0]}, state_path=path)
        # GPS jitter (about 11 m) isn't sent; 33 m is
        assert sc.changed_fields({"lat_lon_z": [10.0001, 20.0, 5.0]}, state_path=path) == {}
        out = sc.changed_fields({"lat_lon_z": [10.0003, 20.0, 5.0]}, state_path=path)
        assert out["lat_lon_z"]["change_m"] == __import__("pytest").approx(33.4, abs=1)


class TestSmallPayloadLimit:
    """US915 DR0 (11 B): queue what doesn't fit, send the compact packet instead."""

    def test_dr0_queues_full_packets_and_sends_compact(self, tmp_path):
        big_bitmap = bytes(60)
        h = _run_capture(tmp_path, 20.0, size_limit=11, bitmap=big_bitmap)
        sent = [c.args[0] for c in h.transmit.call_args_list]
        assert len(sent) == 1 and sent[0].startswith("0c01") and len(sent[0]) == 22
        h.enqueue.assert_called_once()
        sensor_hex, bitmap_hex = h.enqueue.call_args.args[:2]
        assert sensor_hex.startswith("0001") and bitmap_hex.startswith("0818")

    def test_small_bitmap_still_goes_at_dr0(self, tmp_path):
        h = _run_capture(tmp_path, 20.0, size_limit=11)     # 10-byte bitmap packet fits
        sent = [c.args[0] for c in h.transmit.call_args_list]
        assert sent[0].startswith("0c01") and sent[1].startswith("0818")   # compact first: bitmap pairing
        assert h.enqueue.call_args.args[1] is None

    def test_failed_send_is_queued(self, tmp_path):
        h = _run_capture(tmp_path, 20.0, size_limit=242, transmit_ok=False)
        h.enqueue.assert_called_once()
        assert h.enqueue.call_args.args[0].startswith("0001")
        # the key readings still get a try in 11 bytes
        assert any(c.args[0].startswith("0c01") for c in h.transmit.call_args_list)

    def test_fits_sends_no_compact_and_queues_nothing(self, tmp_path):
        h = _run_capture(tmp_path, 20.0, size_limit=242)
        assert not any(c.args[0].startswith("0c01") for c in h.transmit.call_args_list)
        h.enqueue.assert_not_called()


class TestWaterPercent:
    def test_capture_with_a_mask_sends_its_water_pct(self, tmp_path):
        np = __import__("pytest").importorskip("numpy")
        from PIL import Image
        from tools import flood_baseline as fb
        cap = tmp_path / "20261007-120000"
        cap.mkdir()
        arr = np.zeros((10, 10), np.uint8)
        arr[:4] = 255
        Image.fromarray(arr, mode="L").save(str(cap / fb.MASK_NAMES[0]))
        with patch.object(fb, "DEFAULT_STATE_PATH", str(tmp_path / "flood.json")):
            h = _run_capture(tmp_path, 20.0)
        pkts = _sensor_packets(h)
        assert pkts[0]["camera_water_pct"] == 40
        # detection is off by default: no flags
        assert "camera_flood_detected" not in pkts[0]
