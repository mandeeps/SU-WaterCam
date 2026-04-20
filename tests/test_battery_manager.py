"""Tests for tools/battery_manager.py (ADS1115 D+ and INA260 coulomb paths)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.battery_manager as bm


# ---------------------------------------------------------------------------
# ADS1115 / D+ path
# ---------------------------------------------------------------------------

class TestCellVoltageToPct(unittest.TestCase):

    def test_full_cell_voltage(self):
        self.assertEqual(bm._cell_voltage_to_pct(4.2), 100)

    def test_empty_cell_voltage(self):
        self.assertEqual(bm._cell_voltage_to_pct(3.0), 0)

    def test_midpoint(self):
        mid_v = (bm.CELL_V_MIN + bm.CELL_V_MAX) / 2   # 3.6V → 50%
        self.assertEqual(bm._cell_voltage_to_pct(mid_v), 50)

    def test_above_max_clamps_to_100(self):
        self.assertEqual(bm._cell_voltage_to_pct(5.0), 100)

    def test_below_min_clamps_to_zero(self):
        self.assertEqual(bm._cell_voltage_to_pct(1.0), 0)

    def test_empirical_observed_full(self):
        # Voltaic blog: ~1.85V D+ at observed full → cell ~3.7V → ~58%
        pct = bm._cell_voltage_to_pct(1.85 * 2.0)
        self.assertGreater(pct, 50)
        self.assertLess(pct, 75)

    def test_empirical_observed_empty(self):
        # Voltaic blog: ~1.54V D+ at observed empty → cell ~3.08V → ~7%
        pct = bm._cell_voltage_to_pct(1.54 * 2.0)
        self.assertGreaterEqual(pct, 0)
        self.assertLess(pct, 15)

    def test_returns_int(self):
        self.assertIsInstance(bm._cell_voltage_to_pct(3.6), int)


class TestADS1115Path(unittest.TestCase):

    def _status(self, d_plus_v):
        with patch.object(bm, "_read_ads1115_dplus", return_value=d_plus_v):
            return bm.get_battery_status()

    def test_source_tag(self):
        self.assertEqual(self._status(1.85)["battery_source"], "ads1115_dplus")

    def test_d_plus_returned(self):
        self.assertAlmostEqual(self._status(1.75)["d_plus_v"], 1.75, places=3)

    def test_cell_voltage_is_double_dplus(self):
        self.assertAlmostEqual(self._status(1.85)["cell_voltage_v"], 3.70, places=2)

    def test_full_charge(self):
        self.assertEqual(self._status(2.1)["battery_pct"], 100)

    def test_empty(self):
        self.assertEqual(self._status(1.5)["battery_pct"], 0)

    def test_pct_in_range(self):
        pct = self._status(1.75)["battery_pct"]
        self.assertGreaterEqual(pct, 0)
        self.assertLessEqual(pct, 100)

    def test_ina260_fields_are_none(self):
        result = self._status(1.80)
        self.assertIsNone(result["current_ma"])
        self.assertIsNone(result["mah_remaining"])


# ---------------------------------------------------------------------------
# INA260 coulomb-counting path
# ---------------------------------------------------------------------------

class TestCoulombCounting(unittest.TestCase):

    def test_discharge_reduces_remaining(self):
        state = {"mah_remaining": 13500.0, "last_updated_utc": None}
        remaining = bm._update_coulomb_state(state, current_ma=1000.0, elapsed_s=3600.0)
        self.assertAlmostEqual(remaining, 12500.0, places=1)

    def test_discharge_clamps_to_zero(self):
        state = {"mah_remaining": 10.0, "last_updated_utc": None}
        remaining = bm._update_coulomb_state(state, current_ma=5000.0, elapsed_s=3600.0)
        self.assertEqual(remaining, 0.0)

    def test_zero_elapsed_no_change(self):
        state = {"mah_remaining": 8000.0, "last_updated_utc": None}
        remaining = bm._update_coulomb_state(state, current_ma=1000.0, elapsed_s=0.0)
        self.assertAlmostEqual(remaining, 8000.0)

    def test_elapsed_seconds_valid_timestamp(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        elapsed = bm._elapsed_seconds_since(past)
        self.assertGreater(elapsed, 3500)
        self.assertLess(elapsed, 3700)

    def test_elapsed_seconds_none_returns_zero(self):
        self.assertEqual(bm._elapsed_seconds_since(None), 0.0)

    def test_elapsed_seconds_bad_string_returns_zero(self):
        self.assertEqual(bm._elapsed_seconds_since("not-a-date"), 0.0)

    def test_elapsed_seconds_naive_datetime_does_not_raise(self):
        # Naive ISO timestamp (no timezone) must not raise TypeError
        naive_ts = "2026-01-01T00:00:00"
        try:
            elapsed = bm._elapsed_seconds_since(naive_ts)
            self.assertGreater(elapsed, 0)
        except TypeError:
            self.fail("_elapsed_seconds_since raised TypeError on naive timestamp")


class TestStateFile(unittest.TestCase):

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "battery_state.json")
            with patch.object(bm, "STATE_FILE", state_file):
                state_in = {"mah_remaining": 9876.5, "calibrated": True,
                            "last_updated_utc": "2026-01-01T00:00:00+00:00"}
                bm._save_state(state_in)
                state_out = bm._load_state()
            self.assertAlmostEqual(state_out["mah_remaining"], 9876.5)

    def test_missing_file_returns_full_capacity(self):
        with patch.object(bm, "STATE_FILE", "/nonexistent/path/battery_state.json"):
            state = bm._load_state()
        self.assertAlmostEqual(state["mah_remaining"], bm.VOLTAIC_V50_MAH)

    def test_missing_mah_key_defaults_to_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "battery_state.json")
            with open(state_file, "w") as f:
                json.dump({"calibrated": False}, f)
            with patch.object(bm, "STATE_FILE", state_file):
                state = bm._load_state()
            self.assertAlmostEqual(state["mah_remaining"], bm.VOLTAIC_V50_MAH)


class TestINA260Path(unittest.TestCase):

    def _status_with_ina(self, mah_remaining=6750.0, current_ma=500.0):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "battery_state.json")
            seed = {"mah_remaining": mah_remaining, "calibrated": True,
                    "last_updated_utc": None}
            with open(state_file, "w") as f:
                json.dump(seed, f)
            with patch.object(bm, "_read_ads1115_dplus", return_value=None):
                with patch.object(bm, "_read_ina260", return_value=(5.1, current_ma, 2550.0)):
                    with patch.object(bm, "STATE_FILE", state_file):
                        return bm.get_battery_status()

    def test_source_tag(self):
        self.assertEqual(self._status_with_ina()["battery_source"], "ina260")

    def test_pct_at_half_capacity(self):
        result = self._status_with_ina(mah_remaining=bm.VOLTAIC_V50_MAH / 2)
        self.assertEqual(result["battery_pct"], 50)

    def test_ads1115_fields_are_none(self):
        result = self._status_with_ina()
        self.assertIsNone(result["d_plus_v"])
        self.assertIsNone(result["cell_voltage_v"])

    def test_current_ma_returned(self):
        result = self._status_with_ina(current_ma=750.0)
        self.assertAlmostEqual(result["current_ma"], 750.0, places=1)


# ---------------------------------------------------------------------------
# WittyPi output-voltage path
# ---------------------------------------------------------------------------

class TestWittyPiOutputPath(unittest.TestCase):

    def _status(self, output_v, output_a=0.5):
        with patch.object(bm, "_read_ads1115_dplus", return_value=None):
            with patch.object(bm, "_read_ina260", return_value=None):
                with patch.object(bm, "_read_wittypi_output", return_value=(output_v, output_a)):
                    return bm.get_battery_status()

    def test_source_tag(self):
        self.assertEqual(self._status(5.05)["battery_source"], "wittypi_output")

    def test_full_voltage_returns_100(self):
        self.assertEqual(self._status(bm.WITTYPI_OUTPUT_V_FULL)["battery_pct"], 100)

    def test_empty_voltage_returns_0(self):
        self.assertEqual(self._status(bm.WITTYPI_OUTPUT_V_EMPTY)["battery_pct"], 0)

    def test_midpoint(self):
        mid_v = (bm.WITTYPI_OUTPUT_V_FULL + bm.WITTYPI_OUTPUT_V_EMPTY) / 2
        pct = self._status(mid_v)["battery_pct"]
        self.assertEqual(pct, 50)

    def test_above_full_clamps_to_100(self):
        self.assertEqual(self._status(6.0)["battery_pct"], 100)

    def test_below_empty_clamps_to_0(self):
        self.assertEqual(self._status(3.0)["battery_pct"], 0)

    def test_output_voltage_returned(self):
        result = self._status(5.02)
        self.assertAlmostEqual(result["output_voltage_v"], 5.02, places=2)

    def test_output_current_returned(self):
        result = self._status(5.02, output_a=0.75)
        self.assertAlmostEqual(result["output_current_a"], 0.75, places=2)

    def test_ads1115_fields_none(self):
        result = self._status(5.02)
        self.assertIsNone(result["d_plus_v"])
        self.assertIsNone(result["cell_voltage_v"])

    def test_ina260_fields_none(self):
        result = self._status(5.02)
        self.assertIsNone(result["current_ma"])
        self.assertIsNone(result["mah_remaining"])



# ---------------------------------------------------------------------------
# Fallback chain
# ---------------------------------------------------------------------------

class TestFallbackChain(unittest.TestCase):

    def test_ads1115_takes_priority_over_ina260(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=1.80):
            with patch.object(bm, "_read_ina260", return_value=(5.1, 500.0, 2550.0)):
                result = bm.get_battery_status()
        self.assertEqual(result["battery_source"], "ads1115_dplus")

    def test_ads1115_takes_priority_over_wittypi(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=1.80):
            with patch.object(bm, "_read_wittypi_output", return_value=(5.05, 0.5)):
                result = bm.get_battery_status()
        self.assertEqual(result["battery_source"], "ads1115_dplus")

    def test_ina260_used_when_ads1115_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "s.json")
            with patch.object(bm, "_read_ads1115_dplus", return_value=None):
                with patch.object(bm, "_read_ina260", return_value=(5.1, 500.0, 2550.0)):
                    with patch.object(bm, "STATE_FILE", state_file):
                        result = bm.get_battery_status()
        self.assertEqual(result["battery_source"], "ina260")

    def test_ina260_takes_priority_over_wittypi(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = os.path.join(tmpdir, "s.json")
            with patch.object(bm, "_read_ads1115_dplus", return_value=None):
                with patch.object(bm, "_read_ina260", return_value=(5.1, 500.0, 2550.0)):
                    with patch.object(bm, "_read_wittypi_output", return_value=(5.05, 0.5)):
                        with patch.object(bm, "STATE_FILE", state_file):
                            result = bm.get_battery_status()
        self.assertEqual(result["battery_source"], "ina260")

    def test_wittypi_used_when_ads1115_and_ina260_absent(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=None):
            with patch.object(bm, "_read_ina260", return_value=None):
                with patch.object(bm, "_read_wittypi_output", return_value=(5.05, 0.5)):
                    result = bm.get_battery_status()
        self.assertEqual(result["battery_source"], "wittypi_output")

    def test_unavailable_when_all_absent(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=None):
            with patch.object(bm, "_read_ina260", return_value=None):
                with patch.object(bm, "_read_wittypi_output", return_value=None):
                    result = bm.get_battery_status()
        self.assertIsNone(result["battery_pct"])
        self.assertEqual(result["battery_source"], "unavailable")

    def test_unavailable_does_not_raise(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=None):
            with patch.object(bm, "_read_ina260", return_value=None):
                with patch.object(bm, "_read_wittypi_output", return_value=None):
                    try:
                        bm.get_battery_status()
                    except Exception as e:
                        self.fail(f"get_battery_status raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# LoRa packet encoding
# ---------------------------------------------------------------------------

class TestLoRaPacketEncoding(unittest.TestCase):

    def setUp(self):
        sys.modules.setdefault("serial", MagicMock())

    def test_none_battery_excluded(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        packet = lt.compressed_encoding({"timestamp": 1000000, "battery_percent": None})
        self.assertNotIn("0201", packet.hex())

    def test_valid_battery_included(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        packet = lt.compressed_encoding({"timestamp": 1000000, "battery_percent": 75})
        self.assertIn("0201", packet.hex())

    def test_zero_battery_included(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        packet = lt.compressed_encoding({"timestamp": 1000000, "battery_percent": 0})
        self.assertIn("0201", packet.hex())


if __name__ == "__main__":
    unittest.main()
