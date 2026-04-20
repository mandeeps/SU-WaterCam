"""Tests for tools/battery_manager.py"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta


class TestCoulombCounting(unittest.TestCase):

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import tools.battery_manager as bm
        self.bm = bm

    def test_full_capacity_on_fresh_state(self):
        state = self.bm._load_state.__func__ if hasattr(self.bm._load_state, "__func__") else None
        # _load_state with missing file returns full capacity
        with patch("builtins.open", side_effect=FileNotFoundError):
            state = self.bm._load_state()
        self.assertAlmostEqual(state["mah_remaining"], self.bm.VOLTAIC_V50_MAH)
        self.assertFalse(state["calibrated"])

    def test_discharge_reduces_remaining(self):
        state = {"mah_remaining": 13500.0, "last_updated_utc": None, "calibrated": True}
        # 1000 mA draw for 1 hour = 1000 mAh consumed
        remaining = self.bm._update_coulomb_state(state, current_ma=1000.0, elapsed_s=3600.0)
        self.assertAlmostEqual(remaining, 12500.0, places=1)

    def test_discharge_clamps_to_zero(self):
        state = {"mah_remaining": 10.0, "last_updated_utc": None, "calibrated": True}
        remaining = self.bm._update_coulomb_state(state, current_ma=5000.0, elapsed_s=3600.0)
        self.assertEqual(remaining, 0.0)

    def test_zero_elapsed_no_change(self):
        state = {"mah_remaining": 8000.0, "last_updated_utc": None, "calibrated": True}
        remaining = self.bm._update_coulomb_state(state, current_ma=1000.0, elapsed_s=0.0)
        self.assertAlmostEqual(remaining, 8000.0)

    def test_soc_percent_from_mah(self):
        mah = self.bm.VOLTAIC_V50_MAH * 0.75
        pct = max(0, min(100, int(mah / self.bm.VOLTAIC_V50_MAH * 100)))
        self.assertEqual(pct, 75)

    def test_elapsed_seconds_valid_timestamp(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        elapsed = self.bm._elapsed_seconds_since(past)
        self.assertGreater(elapsed, 3500)
        self.assertLess(elapsed, 3700)

    def test_elapsed_seconds_none_returns_zero(self):
        self.assertEqual(self.bm._elapsed_seconds_since(None), 0.0)

    def test_elapsed_seconds_bad_string_returns_zero(self):
        self.assertEqual(self.bm._elapsed_seconds_since("not-a-date"), 0.0)


class TestStateFile(unittest.TestCase):

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import tools.battery_manager as bm
        self.bm = bm

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_state_file = os.path.join(tmpdir, "battery_state.json")
            with patch.object(self.bm, "STATE_FILE", test_state_file):
                state_in = {"mah_remaining": 9876.5, "calibrated": True, "last_updated_utc": "2026-01-01T00:00:00+00:00"}
                self.bm._save_state(state_in)
                state_out = self.bm._load_state()
            self.assertAlmostEqual(state_out["mah_remaining"], 9876.5)
            self.assertTrue(state_out["calibrated"])

    def test_load_missing_file_returns_defaults(self):
        with patch.object(self.bm, "STATE_FILE", "/nonexistent/path/battery_state.json"):
            state = self.bm._load_state()
        self.assertAlmostEqual(state["mah_remaining"], self.bm.VOLTAIC_V50_MAH)


class TestGetBatteryStatusFallback(unittest.TestCase):
    """When INA260 hardware is absent, get_battery_status() must return unavailable."""

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import tools.battery_manager as bm
        self.bm = bm

    def test_no_hardware_returns_none_pct(self):
        with patch.object(self.bm, "_read_ina260", return_value=None):
            with patch.object(self.bm, "_log_wittypi_vin_diagnostic"):
                result = self.bm.get_battery_status()
        self.assertIsNone(result["battery_pct"])
        self.assertEqual(result["battery_source"], "unavailable")

    def test_ina260_present_returns_pct(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            test_state_file = os.path.join(tmpdir, "battery_state.json")
            # Seed state at 50%
            seed = {"mah_remaining": self.bm.VOLTAIC_V50_MAH * 0.5,
                    "calibrated": True,
                    "last_updated_utc": None}
            with open(test_state_file, "w") as f:
                json.dump(seed, f)

            with patch.object(self.bm, "_read_ina260", return_value=(5.1, 500.0, 2550.0)):
                with patch.object(self.bm, "STATE_FILE", test_state_file):
                    result = self.bm.get_battery_status()

        self.assertEqual(result["battery_source"], "ina260")
        self.assertIsNotNone(result["battery_pct"])
        self.assertGreaterEqual(result["battery_pct"], 0)
        self.assertLessEqual(result["battery_pct"], 100)


class TestLoRaPacketEncoding(unittest.TestCase):
    """Verify battery_percent=None is skipped in lora_transmit.compressed_encoding."""

    def setUp(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        # lora_transmit.py imports serial at module level; stub it out
        serial_mock = MagicMock()
        sys.modules.setdefault("serial", serial_mock)

    def test_none_battery_excluded_from_packet(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        data = {"timestamp": 1000000, "temperature_celsius": 20.0, "battery_percent": None}
        packet = lt.compressed_encoding(data)
        # Channel 0x02 0x01 must not appear
        self.assertNotIn("0201", packet.hex())

    def test_valid_battery_included_in_packet(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        data = {"timestamp": 1000000, "battery_percent": 75}
        packet = lt.compressed_encoding(data)
        self.assertIn("0201", packet.hex())


if __name__ == "__main__":
    unittest.main()
