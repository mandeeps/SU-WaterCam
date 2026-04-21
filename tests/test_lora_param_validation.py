"""
Unit tests for LoRaRuntimeManager parameter validation and coercion.

Covers _validate_param / _coerce_param / set_parameter boundaries:
  - min/max inclusive acceptance
  - out-of-range rejection
  - fractional rejection for _INT_PARAMS
  - bool rejection for ranged params
  - string input coercion
  - stored-type correctness (int vs float)
"""

import sys
import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure tools/ is importable
_TOOLS_DIR = os.path.join(os.path.dirname(__file__), '..', 'tools')
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

# Stub out the LoRa handler dependency so the module can be imported without hardware
_mock_handler = MagicMock()
_mock_handler.set_runtime_callback = MagicMock()
_mock_handler.start_listening = MagicMock()

with patch.dict('sys.modules', {
    'lora_handler_concurrent': MagicMock(
        get_lora_handler=MagicMock(return_value=_mock_handler),
        get_config_value=MagicMock(return_value=None),
    )
}):
    from lora_runtime_integration import LoRaRuntimeManager


def _make_manager() -> LoRaRuntimeManager:
    """Return a manager backed by a temp config file (no LoRa hardware)."""
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmp = f.name

    with patch.object(LoRaRuntimeManager, '_init_lora_handler', lambda self: None):
        mgr = LoRaRuntimeManager(config_file=tmp)
    mgr.lora_handler = None
    return mgr


class TestSetParameterRanges(unittest.TestCase):

    def setUp(self):
        self.mgr = _make_manager()

    # ---- min/max inclusive acceptance ----------------------------------------

    def test_area_threshold_min_accepted(self):
        self.assertTrue(self.mgr.set_parameter('area_threshold', 0))
        self.assertEqual(self.mgr.get_parameter('area_threshold'), 0)

    def test_area_threshold_max_accepted(self):
        self.assertTrue(self.mgr.set_parameter('area_threshold', 100))
        self.assertEqual(self.mgr.get_parameter('area_threshold'), 100)

    def test_stage_threshold_min_accepted(self):
        self.assertTrue(self.mgr.set_parameter('stage_threshold', 0))

    def test_stage_threshold_max_accepted(self):
        self.assertTrue(self.mgr.set_parameter('stage_threshold', 1000))

    def test_monitoring_frequency_min_accepted(self):
        self.assertTrue(self.mgr.set_parameter('monitoring_frequency', 1))

    def test_monitoring_frequency_max_accepted(self):
        self.assertTrue(self.mgr.set_parameter('monitoring_frequency', 10080))

    def test_data_retention_days_min_accepted(self):
        self.assertTrue(self.mgr.set_parameter('data_retention_days', 1))

    def test_data_retention_days_max_accepted(self):
        self.assertTrue(self.mgr.set_parameter('data_retention_days', 365))

    # ---- out-of-range rejection -----------------------------------------------

    def test_area_threshold_below_min_rejected(self):
        self.assertFalse(self.mgr.set_parameter('area_threshold', -1))

    def test_area_threshold_above_max_rejected(self):
        self.assertFalse(self.mgr.set_parameter('area_threshold', 101))

    def test_stage_threshold_above_max_rejected(self):
        self.assertFalse(self.mgr.set_parameter('stage_threshold', 1001))

    def test_monitoring_frequency_zero_rejected(self):
        self.assertFalse(self.mgr.set_parameter('monitoring_frequency', 0))

    def test_monitoring_frequency_above_max_rejected(self):
        self.assertFalse(self.mgr.set_parameter('monitoring_frequency', 10081))

    def test_data_retention_days_above_max_rejected(self):
        self.assertFalse(self.mgr.set_parameter('data_retention_days', 366))

    # ---- fractional rejection for _INT_PARAMS --------------------------------

    def test_area_threshold_fractional_rejected(self):
        self.assertFalse(self.mgr.set_parameter('area_threshold', 1.5))

    def test_monitoring_frequency_fractional_rejected(self):
        self.assertFalse(self.mgr.set_parameter('monitoring_frequency', 60.9))

    def test_photo_interval_fractional_rejected(self):
        self.assertFalse(self.mgr.set_parameter('photo_interval', 30.1))

    def test_max_retransmissions_fractional_rejected(self):
        self.assertFalse(self.mgr.set_parameter('max_retransmissions', 3.5))

    def test_shutdown_iteration_limit_fractional_rejected(self):
        self.assertFalse(self.mgr.set_parameter('shutdown_iteration_limit', 2.9))

    def test_data_retention_days_fractional_rejected(self):
        self.assertFalse(self.mgr.set_parameter('data_retention_days', 7.7))

    # stage_threshold is NOT in _INT_PARAMS — fractional values should be accepted
    def test_stage_threshold_fractional_accepted(self):
        self.assertTrue(self.mgr.set_parameter('stage_threshold', 50.5))

    # ---- bool rejection -------------------------------------------------------

    def test_area_threshold_bool_true_rejected(self):
        self.assertFalse(self.mgr.set_parameter('area_threshold', True))

    def test_area_threshold_bool_false_rejected(self):
        self.assertFalse(self.mgr.set_parameter('area_threshold', False))

    def test_monitoring_frequency_bool_rejected(self):
        self.assertFalse(self.mgr.set_parameter('monitoring_frequency', True))

    def test_stage_threshold_bool_rejected(self):
        self.assertFalse(self.mgr.set_parameter('stage_threshold', True))

    # Non-ranged params (bool-typed) should still be settable via set_parameter
    def test_emergency_mode_bool_accepted(self):
        self.assertTrue(self.mgr.set_parameter('emergency_mode', True))
        self.assertEqual(self.mgr.get_parameter('emergency_mode'), True)

    # ---- string input coercion -----------------------------------------------

    def test_area_threshold_string_int_coerced(self):
        self.assertTrue(self.mgr.set_parameter('area_threshold', '50'))
        self.assertEqual(self.mgr.get_parameter('area_threshold'), 50)

    def test_monitoring_frequency_string_int_coerced(self):
        self.assertTrue(self.mgr.set_parameter('monitoring_frequency', '60'))
        self.assertEqual(self.mgr.get_parameter('monitoring_frequency'), 60)

    def test_stage_threshold_string_float_coerced(self):
        self.assertTrue(self.mgr.set_parameter('stage_threshold', '75'))
        self.assertEqual(self.mgr.get_parameter('stage_threshold'), 75.0)

    def test_non_numeric_string_rejected(self):
        self.assertFalse(self.mgr.set_parameter('area_threshold', 'abc'))

    # ---- stored-type correctness ----------------------------------------------

    def test_area_threshold_stored_as_int(self):
        self.mgr.set_parameter('area_threshold', 30)
        result = self.mgr.get_parameter('area_threshold')
        self.assertIsInstance(result, int)
        self.assertNotIsInstance(result, bool)

    def test_monitoring_frequency_stored_as_int(self):
        self.mgr.set_parameter('monitoring_frequency', 120)
        result = self.mgr.get_parameter('monitoring_frequency')
        self.assertIsInstance(result, int)

    def test_stage_threshold_stored_as_float(self):
        self.mgr.set_parameter('stage_threshold', 50)
        result = self.mgr.get_parameter('stage_threshold')
        self.assertIsInstance(result, float)

    def test_area_threshold_string_stored_as_int(self):
        self.mgr.set_parameter('area_threshold', '20')
        result = self.mgr.get_parameter('area_threshold')
        self.assertIsInstance(result, int)
        self.assertEqual(result, 20)

    def test_stage_threshold_string_stored_as_float(self):
        self.mgr.set_parameter('stage_threshold', '100')
        result = self.mgr.get_parameter('stage_threshold')
        self.assertIsInstance(result, float)
        self.assertEqual(result, 100.0)

    # ---- set_parameter return value ------------------------------------------

    def test_set_parameter_returns_bool_true_on_success(self):
        result = self.mgr.set_parameter('area_threshold', 10)
        self.assertIs(result, True)

    def test_set_parameter_returns_bool_false_on_rejection(self):
        result = self.mgr.set_parameter('area_threshold', 999)
        self.assertIs(result, False)

    # ---- rejected values do not overwrite stored value -----------------------

    def test_rejected_value_does_not_overwrite(self):
        self.mgr.set_parameter('area_threshold', 20)
        self.mgr.set_parameter('area_threshold', 999)  # out of range
        self.assertEqual(self.mgr.get_parameter('area_threshold'), 20)


if __name__ == '__main__':
    unittest.main()
