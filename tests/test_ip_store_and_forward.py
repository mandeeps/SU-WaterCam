"""Tests for IPTransmitter store-and-forward disk queue.

All tests are network-free.  IPTransmitter is constructed with
``queue_dir=str(tmp_path / "queue")`` so no real filesystem paths are touched.
``send_uplink`` is patched via ``unittest.mock.patch.object``.

Integration tests call ``ip_uplink_transmit.__wrapped__`` to bypass SQify,
with all hardware imports patched (same pattern as test_bitmap_sf_adaptive.py).
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from tools.transmit_ip import IPTransmitter


# ── helpers ───────────────────────────────────────────────────────────────────

SAMPLE_CHANNELS = [
    {"code": "00 01", "payload_hex": "0000000000000001"},
    {"code": "07 17", "payload_hex": "00000000"},
]


def _make_tx(tmp_path, **kwargs):
    """Build an IPTransmitter with an isolated queue dir and no real config."""
    qdir = str(tmp_path / "queue")
    return IPTransmitter(
        config_path=str(tmp_path / "nonexistent_config.json"),
        queue_dir=qdir,
        **kwargs,
    )


def _success():
    return {"success": True, "status_code": 200, "attempts": 1, "error": None}


def _failure():
    return {"success": False, "status_code": None, "attempts": 3, "error": "timeout"}


# ── queue file creation ────────────────────────────────────────────────────────

class TestEnqueue:
    def test_creates_queue_dir(self, tmp_path):
        tx = _make_tx(tmp_path)
        assert not os.path.exists(tx._queue_dir)
        tx._enqueue(SAMPLE_CHANNELS, int(time.time()))
        assert os.path.isdir(tx._queue_dir)

    def test_single_json_file_no_tmp(self, tmp_path):
        tx = _make_tx(tmp_path)
        ts = int(time.time())
        tx._enqueue(SAMPLE_CHANNELS, ts)
        files = os.listdir(tx._queue_dir)
        assert len(files) == 1
        assert files[0].endswith(".json")
        assert ".tmp" not in files[0]

    def test_no_tmp_files_remain(self, tmp_path):
        tx = _make_tx(tmp_path)
        tx._enqueue(SAMPLE_CHANNELS, int(time.time()))
        tmps = [f for f in os.listdir(tx._queue_dir) if f.endswith(".tmp")]
        assert tmps == []

    def test_file_schema(self, tmp_path):
        tx = _make_tx(tmp_path)
        ts = int(time.time())
        tx._enqueue(SAMPLE_CHANNELS, ts)
        fname = os.listdir(tx._queue_dir)[0]
        with open(os.path.join(tx._queue_dir, fname)) as f:
            record = json.load(f)
        assert record["channels"] == SAMPLE_CHANNELS
        assert record["device_ts"] == ts
        assert isinstance(record["enqueued_at"], float)

    def test_two_enqueues_sort_chronologically(self, tmp_path):
        tx = _make_tx(tmp_path)
        tx._enqueue(SAMPLE_CHANNELS, 1000)
        tx._enqueue(SAMPLE_CHANNELS, 2000)
        files = sorted(os.listdir(tx._queue_dir))
        assert len(files) == 2
        ts0 = int(files[0].split("_")[0])
        ts1 = int(files[1].split("_")[0])
        assert ts0 <= ts1

    def test_returns_true_on_success(self, tmp_path):
        tx = _make_tx(tmp_path)
        result = tx._enqueue(SAMPLE_CHANNELS, int(time.time()))
        assert result is True


# ── max-depth eviction ─────────────────────────────────────────────────────────

class TestMaxDepthEviction:
    def test_fourth_enqueue_evicts_oldest(self, tmp_path):
        tx = _make_tx(tmp_path)
        tx.max_queue_depth = 3
        for i in range(4):
            tx._enqueue(SAMPLE_CHANNELS, 1000 + i)
            time.sleep(0.01)
        files = sorted(os.listdir(tx._queue_dir))
        assert len(files) == 3
        # Oldest (ts=1000) should be gone; youngest three remain
        ts_values = [int(f.split("_")[0]) for f in files]
        assert 1000 not in ts_values

    def test_depth_one_always_single_entry(self, tmp_path):
        tx = _make_tx(tmp_path)
        tx.max_queue_depth = 1
        for i in range(5):
            tx._enqueue(SAMPLE_CHANNELS, 2000 + i)
            time.sleep(0.01)
        assert len(os.listdir(tx._queue_dir)) == 1


# ── drain: empty / missing queue ──────────────────────────────────────────────

class TestDrainEmpty:
    def test_missing_dir_returns_zero(self, tmp_path):
        tx = _make_tx(tmp_path)
        result = tx._drain_queue()
        assert result == {"drained": 0, "failed": False, "failed_file": None}

    def test_empty_dir_returns_zero(self, tmp_path):
        tx = _make_tx(tmp_path)
        os.makedirs(tx._queue_dir)
        result = tx._drain_queue()
        assert result == {"drained": 0, "failed": False, "failed_file": None}


# ── drain: age eviction ────────────────────────────────────────────────────────

class TestDrainAgeEviction:
    def _write_entry(self, queue_dir, ts, age_days):
        os.makedirs(queue_dir, exist_ok=True)
        enqueued_at = time.time() - age_days * 86400
        record = {"channels": SAMPLE_CHANNELS, "device_ts": ts, "enqueued_at": enqueued_at}
        fname = f"{ts:010d}_000000.json"
        path = os.path.join(queue_dir, fname)
        with open(path, "w") as f:
            json.dump(record, f)
        return fname

    def test_old_entry_deleted_not_counted(self, tmp_path):
        tx = _make_tx(tmp_path)
        tx.max_queue_age_days = 7.0
        self._write_entry(tx._queue_dir, 1000, age_days=8)
        with patch.object(tx, "send_uplink", return_value=_success()) as mock_send:
            result = tx._drain_queue()
        mock_send.assert_not_called()
        assert result["drained"] == 0
        assert result["failed"] is False
        assert len(os.listdir(tx._queue_dir)) == 0

    def test_recent_entry_sent(self, tmp_path):
        tx = _make_tx(tmp_path)
        tx.max_queue_age_days = 7.0
        self._write_entry(tx._queue_dir, 2000, age_days=6)
        with patch.object(tx, "send_uplink", return_value=_success()):
            result = tx._drain_queue()
        assert result["drained"] == 1
        assert len(os.listdir(tx._queue_dir)) == 0


# ── drain: ordering and stop-on-failure ───────────────────────────────────────

class TestDrainBehaviour:
    def _write_entry(self, queue_dir, ts, age_days=0):
        os.makedirs(queue_dir, exist_ok=True)
        enqueued_at = time.time() - age_days * 86400
        record = {"channels": SAMPLE_CHANNELS, "device_ts": ts, "enqueued_at": enqueued_at}
        fname = f"{ts:010d}_000000.json"
        path = os.path.join(queue_dir, fname)
        with open(path, "w") as f:
            json.dump(record, f)
        return fname

    def test_sent_in_chronological_order(self, tmp_path):
        tx = _make_tx(tmp_path)
        self._write_entry(tx._queue_dir, 1001)
        self._write_entry(tx._queue_dir, 1002)
        call_ts = []
        def mock_send(channels, device_ts=None):
            call_ts.append(device_ts)
            return _success()
        with patch.object(tx, "send_uplink", side_effect=mock_send):
            result = tx._drain_queue()
        assert result["drained"] == 2
        assert call_ts == [1001, 1002]

    def test_first_failure_stops_drain(self, tmp_path):
        tx = _make_tx(tmp_path)
        self._write_entry(tx._queue_dir, 1001)
        self._write_entry(tx._queue_dir, 1002)
        with patch.object(tx, "send_uplink", return_value=_failure()):
            result = tx._drain_queue()
        assert result["failed"] is True
        assert result["drained"] == 0
        assert "1001_000000.json" in result["failed_file"]
        # Second file untouched
        assert len(os.listdir(tx._queue_dir)) == 2

    def test_success_removes_file(self, tmp_path):
        tx = _make_tx(tmp_path)
        self._write_entry(tx._queue_dir, 1001)
        with patch.object(tx, "send_uplink", return_value=_success()):
            tx._drain_queue()
        assert len(os.listdir(tx._queue_dir)) == 0

    def test_corrupt_file_deleted_drain_continues(self, tmp_path):
        tx = _make_tx(tmp_path)
        # Write corrupt JSON
        os.makedirs(tx._queue_dir, exist_ok=True)
        bad_path = os.path.join(tx._queue_dir, "0000001000_000000.json")
        with open(bad_path, "w") as f:
            f.write("not json {{{{")
        self._write_entry(tx._queue_dir, 1001)
        with patch.object(tx, "send_uplink", return_value=_success()) as mock_send:
            result = tx._drain_queue()
        mock_send.assert_called_once()
        assert result["drained"] == 1
        assert not os.path.exists(bad_path)

    def test_partial_success_before_failure(self, tmp_path):
        tx = _make_tx(tmp_path)
        for ts in [1001, 1002, 1003]:
            self._write_entry(tx._queue_dir, ts)
        responses = [_success(), _failure(), _success()]
        with patch.object(tx, "send_uplink", side_effect=responses):
            result = tx._drain_queue()
        assert result["drained"] == 1
        assert result["failed"] is True
        # 1001 deleted, 1002 + 1003 remain
        assert len(os.listdir(tx._queue_dir)) == 2


# ── config loading ─────────────────────────────────────────────────────────────

class TestConfigLoading:
    def _write_config(self, tmp_path, **ip_fields):
        cfg = {"ip_upload": {"enabled": False, "server_url": "http://x", **ip_fields}}
        p = tmp_path / "runtime_config.json"
        p.write_text(json.dumps(cfg))
        return str(p)

    def test_max_queue_depth_from_config(self, tmp_path):
        cfg_path = self._write_config(tmp_path, max_queue_depth=12)
        tx = IPTransmitter(config_path=cfg_path, queue_dir=str(tmp_path / "q"))
        assert tx.max_queue_depth == 12

    def test_max_queue_age_days_from_config(self, tmp_path):
        cfg_path = self._write_config(tmp_path, max_queue_age_days=3.5)
        tx = IPTransmitter(config_path=cfg_path, queue_dir=str(tmp_path / "q"))
        assert tx.max_queue_age_days == 3.5

    def test_max_queue_depth_default(self, tmp_path):
        cfg_path = self._write_config(tmp_path)
        tx = IPTransmitter(config_path=cfg_path, queue_dir=str(tmp_path / "q"))
        assert tx.max_queue_depth == 48

    def test_max_queue_age_days_default(self, tmp_path):
        cfg_path = self._write_config(tmp_path)
        tx = IPTransmitter(config_path=cfg_path, queue_dir=str(tmp_path / "q"))
        assert tx.max_queue_age_days == 7.0

    def test_queue_dir_param_overrides_default(self, tmp_path):
        custom = str(tmp_path / "custom_queue")
        tx = IPTransmitter(
            config_path=str(tmp_path / "nope.json"),
            queue_dir=custom,
        )
        assert tx._queue_dir == os.path.abspath(custom)


# ── robustness ────────────────────────────────────────────────────────────────

class TestRobustness:
    def test_enqueue_returns_false_on_os_error(self, tmp_path):
        tx = _make_tx(tmp_path)
        with patch("os.replace", side_effect=OSError("disk full")):
            with patch("os.makedirs"):
                result = tx._enqueue(SAMPLE_CHANNELS, int(time.time()))
        assert result is False

    def test_enqueue_does_not_raise(self, tmp_path):
        tx = _make_tx(tmp_path)
        with patch("os.replace", side_effect=OSError("disk full")):
            with patch("os.makedirs"):
                try:
                    tx._enqueue(SAMPLE_CHANNELS, int(time.time()))
                except Exception as exc:
                    pytest.fail(f"_enqueue raised unexpectedly: {exc}")


# ── integration: ip_uplink_transmit.__wrapped__ ───────────────────────────────

class TestIPUplinkTransmitIntegration:
    """Call ip_uplink_transmit.__wrapped__ directly to bypass SQify."""

    def _patch_hardware(self):
        """Return a dict of patches for all hardware imports used by the function."""
        return {
            "aht20": patch(
                "tools.aht20_temperature.get_aht20",
                return_value={"temperature_celsius": 20.0, "relative_humidity": 50.0},
            ),
            "gps": patch(
                "tools.get_gps.get_location_with_retry",
                return_value=({"gps_lat": 43.0, "gps_lon": -76.0}, None),
            ),
            "battery": patch(
                "tools.battery_manager.get_battery_status",
                return_value={"battery_pct": 80, "battery_source": "ads1115"},
            ),
            "get_param": patch(
                "tools.lora_runtime_integration.get_parameter",
                side_effect=lambda k, d: d,
            ),
        }

    def _run(self, tmp_path, queue_dir, extra_tx_kwargs=None, bitmap=None):
        """Run ip_uplink_transmit.__wrapped__ with hardware patched."""
        import ticktalk_main
        patches = self._patch_hardware()
        ctx = [p.__enter__() for p in patches.values()]
        try:
            result = ticktalk_main.ip_uplink_transmit.__wrapped__(
                bitmap=bitmap or [], _sensor_tracker=None
            )
        finally:
            for i, p in enumerate(patches.values()):
                p.__exit__(None, None, None)
        return result

    def test_server_unreachable_queues_reading(self, tmp_path):
        import ticktalk_main
        qdir = str(tmp_path / "queue")
        with patch("tools.transmit_ip.IPTransmitter") as MockTx:
            instance = MockTx.return_value
            instance.enabled = True
            instance.is_reachable.return_value = False
            instance._enqueue.return_value = True
            instance._queue_dir = qdir
            instance.fallback_to_lora = False

            patches = self._patch_hardware()
            for p in patches.values():
                p.__enter__()
            try:
                result = ticktalk_main.ip_uplink_transmit.__wrapped__(
                    bitmap=[], _sensor_tracker=None
                )
            finally:
                for p in patches.values():
                    p.__exit__(None, None, None)

        assert result["status"] == "queued"
        assert result["success"] is False
        instance._enqueue.assert_called_once()

    def test_send_failure_queues_live_reading(self, tmp_path):
        import ticktalk_main
        with patch("tools.transmit_ip.IPTransmitter") as MockTx:
            instance = MockTx.return_value
            instance.enabled = True
            instance.is_reachable.return_value = True
            instance._drain_queue.return_value = {"drained": 0, "failed": False, "failed_file": None}
            instance.send_uplink.return_value = _failure()
            instance._enqueue.return_value = True
            instance.fallback_to_lora = False

            patches = self._patch_hardware()
            for p in patches.values():
                p.__enter__()
            try:
                result = ticktalk_main.ip_uplink_transmit.__wrapped__(
                    bitmap=[], _sensor_tracker=None
                )
            finally:
                for p in patches.values():
                    p.__exit__(None, None, None)

        instance._enqueue.assert_called_once()
        assert result["success"] is False

    def test_drain_fails_live_reading_also_queued(self, tmp_path):
        import ticktalk_main
        with patch("tools.transmit_ip.IPTransmitter") as MockTx:
            instance = MockTx.return_value
            instance.enabled = True
            instance.is_reachable.return_value = True
            instance._drain_queue.return_value = {
                "drained": 0, "failed": True, "failed_file": "0000001000_000000.json"
            }
            instance._enqueue.return_value = True

            patches = self._patch_hardware()
            for p in patches.values():
                p.__enter__()
            try:
                result = ticktalk_main.ip_uplink_transmit.__wrapped__(
                    bitmap=[], _sensor_tracker=None
                )
            finally:
                for p in patches.values():
                    p.__exit__(None, None, None)

        assert result["status"] == "queued_drain_failed"
        instance._enqueue.assert_called_once()
        instance.send_uplink.assert_not_called()

    def test_successful_send_after_drain(self, tmp_path):
        import ticktalk_main
        with patch("tools.transmit_ip.IPTransmitter") as MockTx:
            instance = MockTx.return_value
            instance.enabled = True
            instance.is_reachable.return_value = True
            instance._drain_queue.return_value = {"drained": 2, "failed": False, "failed_file": None}
            instance.send_uplink.return_value = _success()
            instance._enqueue.return_value = True

            patches = self._patch_hardware()
            for p in patches.values():
                p.__enter__()
            try:
                result = ticktalk_main.ip_uplink_transmit.__wrapped__(
                    bitmap=[], _sensor_tracker=None
                )
            finally:
                for p in patches.values():
                    p.__exit__(None, None, None)

        assert result["status"] == "ok"
        assert result["success"] is True
        assert result["drained"] == 2
        instance._enqueue.assert_not_called()

    def test_disabled_returns_disabled(self, tmp_path):
        import ticktalk_main
        with patch("tools.transmit_ip.IPTransmitter") as MockTx:
            instance = MockTx.return_value
            instance.enabled = False

            result = ticktalk_main.ip_uplink_transmit.__wrapped__(
                bitmap=[], _sensor_tracker=None
            )

        assert result["status"] == "disabled"
        assert result["success"] is False
