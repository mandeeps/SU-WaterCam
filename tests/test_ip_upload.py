"""Integration tests for IP uplink/downlink transport.

Runs against a live WaterCam FastAPI server.  The server URL and device ID
are read from runtime_config.json (ip_upload section) and can be overridden
via environment variables:

    WATERCAM_SERVER_URL=http://localhost:8000
    WATERCAM_DEVICE_ID=watercam-test-001

These tests are NOT unit tests — they require the API to be running.
They will skip cleanly if the server is unreachable rather than failing.

Run with:
    python tests/test_ip_upload.py
or (if pytest is available):
    pytest tests/test_ip_upload.py -v
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time
import unittest

# Allow running from project root or from tests/
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.transmit_ip import IPTransmitter, _DEFAULT_CONFIG_PATH

# ---------------------------------------------------------------------------
# Config — override via env vars for CI / deployment testing
# ---------------------------------------------------------------------------

_SERVER_URL = os.environ.get("WATERCAM_SERVER_URL") or None
_DEVICE_ID = os.environ.get("WATERCAM_DEVICE_ID") or None


def _make_transmitter() -> IPTransmitter:
    """Build a transmitter, applying env-var overrides if set."""
    return IPTransmitter(
        config_path=_DEFAULT_CONFIG_PATH,
        override_url=_SERVER_URL,
        override_device_id=_DEVICE_ID,
    )


# ---------------------------------------------------------------------------
# Channel encoding helpers (mirrors what the main application will produce)
# ---------------------------------------------------------------------------

def encode_device_ts(ts: int) -> dict:
    """00 01 — UNIX seconds as unsigned 64-bit big-endian."""
    return {"code": "00 01", "payload_hex": struct.pack(">Q", ts).hex()}


def encode_battery(pct: int) -> dict:
    """02 01 — battery percent as uint32 big-endian."""
    return {"code": "02 01", "payload_hex": struct.pack(">I", pct).hex()}


def encode_temperature(temp_c: float) -> dict:
    """05 01 — temperature as int16 (value * 100), big-endian."""
    raw = int(round(temp_c * 100))
    return {"code": "05 01", "payload_hex": struct.pack(">h", raw).hex()}


def encode_humidity(pct: int) -> dict:
    """06 01 — humidity as uint8."""
    return {"code": "06 01", "payload_hex": struct.pack(">B", pct).hex()}


def encode_gps(lat: float, lon: float) -> dict:
    """04 01 — GPS as two int32 values (degrees * 1e6), big-endian."""
    lat_raw = int(round(lat * 1_000_000))
    lon_raw = int(round(lon * 1_000_000))
    return {"code": "04 01", "payload_hex": struct.pack(">ii", lat_raw, lon_raw).hex()}


def encode_flood_detect(detected: bool) -> dict:
    """07 17 — camera flood detect as uint32 bool."""
    return {"code": "07 17", "payload_hex": struct.pack(">I", int(detected)).hex()}


def encode_flood_bitmap(bitmap_bytes: bytes) -> dict:
    """08 18 — variable-length flood bitmap."""
    return {"code": "08 18", "payload_hex": bitmap_bytes.hex()}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestIPUplink(unittest.TestCase):
    """Tests for POST /ip/uplink."""

    @classmethod
    def setUpClass(cls):
        cls.tx = _make_transmitter()
        # Use a short timeout for the health check so CI skips quickly when the
        # server is not running — the full timeout_s (default 15 s) would add
        # significant delay per test class before the skip is issued.
        if not cls.tx.is_reachable(timeout_s=3):
            server_url = cls.tx.server_url
            cls.tx.close()
            cls.tx = None
            raise unittest.SkipTest(
                f"Server not reachable at {server_url} — start the API first "
                "or set WATERCAM_SERVER_URL to point at a running instance."
            )
        print(f"\nServer: {cls.tx.server_url}  Device: {cls.tx.device_id}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "tx", None) is not None:
            cls.tx.close()
            cls.tx = None

    def test_01_minimal_uplink_battery_and_temperature(self):
        """Simplest valid uplink: battery + temperature."""
        ts = int(time.time())
        channels = [
            encode_device_ts(ts),
            encode_battery(80),
            encode_temperature(21.5),
        ]
        result = self.tx.send_uplink(channels, device_ts=ts)

        self.assertTrue(result["success"], f"Upload failed: {result.get('error')}")
        self.assertIn(result["status_code"], (200, 201))
        resp = result["response"]
        self.assertIn("device_id", resp)
        self.assertIn("decoded", resp)
        decoded = resp["decoded"]
        self.assertIn("battery_pct", decoded)
        self.assertIn("temperature_c", decoded)
        print(f"  decoded: {json.dumps(decoded, indent=4)}")

    def test_02_full_sensor_uplink(self):
        """Uplink with all standard sensor channels."""
        ts = int(time.time())
        # Syracuse University coordinates (test fixture)
        channels = [
            encode_device_ts(ts),
            encode_battery(65),
            encode_temperature(18.75),
            encode_humidity(62),
            encode_gps(43.0389, -76.1322),
        ]
        result = self.tx.send_uplink(channels, device_ts=ts)

        self.assertTrue(result["success"], f"Upload failed: {result.get('error')}")
        decoded = result["response"]["decoded"]
        self.assertIn("battery_pct", decoded)
        self.assertIn("temperature_c", decoded)
        self.assertIn("humidity_pct", decoded)
        self.assertIn("gps_block", decoded)
        print(f"  GPS decoded: {decoded.get('gps_block')}")

    def test_03_uplink_with_flood_detect(self):
        """Uplink including camera flood detect flag."""
        ts = int(time.time())
        channels = [
            encode_device_ts(ts),
            encode_battery(55),
            encode_flood_detect(True),
        ]
        result = self.tx.send_uplink(channels, device_ts=ts)

        self.assertTrue(result["success"], f"Upload failed: {result.get('error')}")
        decoded = result["response"]["decoded"]
        self.assertIn("camera_flood_detect", decoded)
        print(f"  flood_detect: {decoded.get('camera_flood_detect')}")

    def test_04_uplink_with_bitmap(self):
        """Uplink with a small synthetic flood bitmap (variable-length channel)."""
        ts = int(time.time())
        # 8x8 bitmap, alternating bytes as a synthetic flood mask
        bitmap = bytes([0xFF, 0x00] * 4)
        channels = [
            encode_device_ts(ts),
            encode_battery(50),
            encode_flood_bitmap(bitmap),
        ]
        result = self.tx.send_uplink(channels, device_ts=ts)

        self.assertTrue(result["success"], f"Upload failed: {result.get('error')}")
        decoded = result["response"]["decoded"]
        self.assertIn("camera_flood_bitmap", decoded)
        print(f"  bitmap decoded: {decoded.get('camera_flood_bitmap')}")

    def test_05_uplink_device_ts_precision(self):
        """Verify device timestamp is respected by the server."""
        # Use a fixed past timestamp so we can confirm it is echoed back
        fixed_ts = 1_700_000_000  # Nov 14 2023
        channels = [
            encode_device_ts(fixed_ts),
            encode_battery(90),
        ]
        result = self.tx.send_uplink(channels, device_ts=fixed_ts)

        self.assertTrue(result["success"], f"Upload failed: {result.get('error')}")
        resp_ts = result["response"].get("ts", "")
        # Server should echo back a timestamp near the device timestamp
        print(f"  response ts: {resp_ts}")
        self.assertIn("2023", resp_ts, "Server should reflect the device timestamp year")

    def test_06_uplink_empty_channels_rejected(self):
        """Empty channels list should fail locally before hitting the server."""
        result = self.tx.send_uplink([])
        self.assertFalse(result["success"])
        self.assertIsNotNone(result["error"])

    def test_07_multiple_uplinks_idempotency(self):
        """Sending the same data twice should succeed both times."""
        ts = int(time.time())
        channels = [encode_battery(70), encode_temperature(20.0)]
        r1 = self.tx.send_uplink(channels, device_ts=ts)
        r2 = self.tx.send_uplink(channels, device_ts=ts)
        self.assertTrue(r1["success"], f"First upload failed: {r1.get('error')}")
        self.assertTrue(r2["success"], f"Second upload failed: {r2.get('error')}")


class TestIPDownlink(unittest.TestCase):
    """Tests for GET /ip/downlink/{device_id}."""

    @classmethod
    def setUpClass(cls):
        cls.tx = _make_transmitter()
        if not cls.tx.is_reachable(timeout_s=3):
            server_url = cls.tx.server_url
            cls.tx.close()
            cls.tx = None
            raise unittest.SkipTest(
                f"Server not reachable at {server_url} — start the API first."
            )

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "tx", None) is not None:
            cls.tx.close()
            cls.tx = None

    def test_01_poll_returns_valid_structure(self):
        """Polling with no pending command returns a well-formed response."""
        result = self.tx.poll_downlink()

        self.assertTrue(result["success"], f"Poll failed: {result.get('error')}")
        # poll_downlink treats 404 (device not yet registered) as success + no command
        self.assertIn(result["status_code"], (200, 404))
        # command is either None (no pending) or a dict
        self.assertIn("command", result)
        cmd = result["command"]
        if cmd is not None:
            self.assertIn("hex_payload", cmd)
            self.assertIn("queue_id", cmd)
            print(f"  Received pending command: {cmd}")
        else:
            print("  No pending commands (expected if queue is empty)")

    def test_02_poll_does_not_crash_on_unknown_device(self):
        """Polling for a never-seen device should return 200 with no command."""
        tx2 = IPTransmitter(
            config_path=_DEFAULT_CONFIG_PATH,
            override_url=self.tx.server_url,
            override_device_id="__nonexistent_test_device__",
        )
        try:
            result = tx2.poll_downlink()
        finally:
            tx2.close()
        # May return 200 {"command": null} or 404 depending on API version
        self.assertIn(result["status_code"], (200, 404))
        self.assertIsNone(result.get("command"))


class TestIPReachability(unittest.TestCase):
    """Basic connectivity checks."""

    def test_is_reachable_live_server(self):
        """is_reachable() should return True when server is up."""
        tx = _make_transmitter()
        try:
            reachable = tx.is_reachable()
        finally:
            tx.close()
        if not reachable:
            self.skipTest(f"Server not reachable at {tx.server_url}")
        self.assertTrue(reachable)

    def test_is_reachable_bad_url(self):
        """is_reachable() should return False for a definitely-wrong URL."""
        tx = IPTransmitter(override_url="http://127.0.0.1:19999")
        try:
            result = tx.is_reachable()
        finally:
            tx.close()
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
