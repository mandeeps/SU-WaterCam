"""Tests for tools/battery_manager.py (ADS1115 + D+ pin SOC estimation)."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.battery_manager as bm


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
        # Voltaic blog reports ~1.85V D+ at observed full → cell ~3.7V → ~58%
        d_plus = 1.85
        cell_v = d_plus * 2.0
        pct = bm._cell_voltage_to_pct(cell_v)
        self.assertGreater(pct, 50)
        self.assertLess(pct, 75)

    def test_empirical_observed_empty(self):
        # Voltaic blog reports ~1.54V D+ at observed empty → cell ~3.08V → ~7%
        d_plus = 1.54
        cell_v = d_plus * 2.0
        pct = bm._cell_voltage_to_pct(cell_v)
        self.assertGreaterEqual(pct, 0)
        self.assertLess(pct, 15)

    def test_returns_int(self):
        result = bm._cell_voltage_to_pct(3.6)
        self.assertIsInstance(result, int)


class TestGetBatteryStatusFallback(unittest.TestCase):
    """When ADS1115 is absent, get_battery_status() returns unavailable."""

    def test_no_hardware_returns_none_pct(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=None):
            with patch.object(bm, "_log_wittypi_vin_diagnostic"):
                result = bm.get_battery_status()
        self.assertIsNone(result["battery_pct"])
        self.assertEqual(result["battery_source"], "unavailable")
        self.assertIsNone(result["cell_voltage_v"])
        self.assertIsNone(result["d_plus_v"])

    def test_no_hardware_does_not_raise(self):
        with patch.object(bm, "_read_ads1115_dplus", return_value=None):
            with patch.object(bm, "_log_wittypi_vin_diagnostic"):
                try:
                    bm.get_battery_status()
                except Exception as e:
                    self.fail(f"get_battery_status raised unexpectedly: {e}")


class TestGetBatteryStatusWithHardware(unittest.TestCase):
    """When ADS1115 reads a valid D+ voltage, SOC is computed correctly."""

    def _run_with_dplus(self, d_plus_v):
        with patch.object(bm, "_read_ads1115_dplus", return_value=d_plus_v):
            return bm.get_battery_status()

    def test_source_tag(self):
        result = self._run_with_dplus(1.85)
        self.assertEqual(result["battery_source"], "ads1115_dplus")

    def test_d_plus_v_returned(self):
        result = self._run_with_dplus(1.75)
        self.assertAlmostEqual(result["d_plus_v"], 1.75, places=3)

    def test_cell_voltage_is_double_dplus(self):
        result = self._run_with_dplus(1.85)
        self.assertAlmostEqual(result["cell_voltage_v"], 3.70, places=2)

    def test_full_charge(self):
        # D+ = 2.1V → cell = 4.2V → 100%
        result = self._run_with_dplus(2.1)
        self.assertEqual(result["battery_pct"], 100)

    def test_empty(self):
        # D+ = 1.5V → cell = 3.0V → 0%
        result = self._run_with_dplus(1.5)
        self.assertEqual(result["battery_pct"], 0)

    def test_pct_in_range(self):
        result = self._run_with_dplus(1.75)
        self.assertGreaterEqual(result["battery_pct"], 0)
        self.assertLessEqual(result["battery_pct"], 100)

    def test_pct_is_int(self):
        result = self._run_with_dplus(1.80)
        self.assertIsInstance(result["battery_pct"], int)


class TestLoRaPacketEncoding(unittest.TestCase):
    """battery_percent=None must be skipped in compressed_encoding."""

    def setUp(self):
        serial_mock = MagicMock()
        sys.modules.setdefault("serial", serial_mock)

    def test_none_battery_excluded_from_packet(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        data = {"timestamp": 1000000, "temperature_celsius": 20.0, "battery_percent": None}
        packet = lt.compressed_encoding(data)
        self.assertNotIn("0201", packet.hex())

    def test_valid_battery_included_in_packet(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        data = {"timestamp": 1000000, "battery_percent": 75}
        packet = lt.compressed_encoding(data)
        self.assertIn("0201", packet.hex())

    def test_zero_battery_included_in_packet(self):
        import importlib
        import tools.lora_transmit as lt
        importlib.reload(lt)
        data = {"timestamp": 1000000, "battery_percent": 0}
        packet = lt.compressed_encoding(data)
        self.assertIn("0201", packet.hex())


if __name__ == "__main__":
    unittest.main()
