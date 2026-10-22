"""One shutdown_limit default (3) everywhere; Witty Pi read failures are None, never fake values."""
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import lora_runtime_integration as lri
from tools import wittypi_control


# --- shutdown_limit ----------------------------------------------------------

def test_shutdown_limit_default_is_three_in_the_config_defaults(tmp_path):
    mgr = lri.LoRaRuntimeManager(config_file=str(tmp_path / "runtime_config.json"))
    assert mgr.get_parameter("shutdown_iteration_limit") == lri.DEFAULT_SHUTDOWN_LIMIT == 3
    assert mgr.get_parameter("emergency_frequency") == lri.DEFAULT_EMERGENCY_FREQUENCY == 10


def test_atomic_increment_falls_back_to_three(tmp_path):
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text("{}")
    mgr = lri.LoRaRuntimeManager(config_file=str(cfg))
    assert mgr.atomic_increment_iteration_count()["shutdown_iteration_limit"] == 3


def test_ticktalk_main_uses_the_constant():
    import ticktalk_main
    src = open(ticktalk_main.__file__).read()
    assert "shutdown_limit = DEFAULT_SHUTDOWN_LIMIT" in src
    assert "shutdown_limit = 3" not in src
    assert "set_parameter('emergency_frequency'" not in src      # no more forcing 1 minute


# --- Witty Pi None ----------------------------------------------------------

def _wp():
    from tools.witty_pi_4 import WittyPi4
    return WittyPi4()


def test_failed_readings_are_none():
    wp = _wp()
    with patch.object(wp, "run_command", return_value="ERROR"):
        assert wp.get_temperature() is None
        assert wp.get_battery_voltage() is None
        assert wp.get_internal_voltage() is None
        assert wp.get_internal_current() is None


def test_good_readings_are_floats():
    wp = _wp()
    with patch.object(wp, "run_command", side_effect=["31.5°C / 88.7°F", "4.9", "5.0", "0.7"]):
        assert wp.get_temperature() == 31.5
        assert wp.get_battery_voltage() == 4.9
        assert wp.get_internal_voltage() == 5.0
        assert wp.get_internal_current() == 0.7


def test_status_with_no_readings_is_not_retrieved():
    with patch.object(wittypi_control, "get_data", return_value=(None, None, None, None)):
        assert wittypi_control.get_wittypi_status()["status"] == "wittypi_read_failed"


def test_status_with_some_readings_keeps_none_for_the_rest():
    with patch.object(wittypi_control, "get_data", return_value=(None, None, 4.9, None)):
        st = wittypi_control.get_wittypi_status()
    assert st["status"] == "wittypi_data_retrieved"
    assert st["internal_voltage"] == 4.9 and st["temperature"] is None


def test_battery_manager_keeps_voltage_when_only_current_failed():
    from tools import battery_manager as bm
    data = {"status": "wittypi_data_retrieved", "internal_voltage": 5.0, "internal_current": None}
    assert bm._read_wittypi_output(data) == (5.0, None)
    assert bm._read_wittypi_output({"status": "wittypi_read_failed"}) is None
    assert bm._read_wittypi_output({"status": "wittypi_data_retrieved", "internal_voltage": None}) is None


def test_debug_status_skips_none_fields():
    from tools import debug_status_command as dsc
    with patch.object(dsc, "check_wittypi_i2c_presence", return_value=True), \
         patch.object(wittypi_control, "get_data", return_value=(None, None, 4.9, None)):
        st = dsc.get_wittypi_status()
    assert st["available"] is True and st["internal_voltage_v"] == 4.9
    assert "temperature_c" not in st and "internal_current_a" not in st
    with patch.object(dsc, "check_wittypi_i2c_presence", return_value=True), \
         patch.object(wittypi_control, "get_data", return_value=(None, None, None, None)):
        assert dsc.get_wittypi_status()["available"] is False
