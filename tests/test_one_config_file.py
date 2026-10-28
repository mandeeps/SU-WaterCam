"""runtime_config.json is the one config file; the LoRa handler keeps no copy.

The Pi-side LoRa queue settings come from its lora_queue section, else an old
lora_config.json, else the default (tools/lora_handler_concurrent.lookup_setting).
"""
import json
from unittest.mock import patch

import pytest

import tools.lora_handler_concurrent as lhc
import tools.lora_runtime_integration as lri
from tools.lora_runtime_integration import LoRaRuntimeManager


@pytest.fixture
def runtime(tmp_path):
    path = tmp_path / "runtime_config.json"

    def make(data):
        path.write_text(json.dumps(data))
        with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
            m = LoRaRuntimeManager(config_file=str(path))
        m.lora_handler = None
        return m
    with patch.object(lri, "get_runtime_manager") as get:
        yield lambda data: (get.configure_mock(return_value=make(data)), path)[1]


def test_queue_settings_come_from_the_lora_queue_section(runtime, tmp_path):
    runtime({"lora_queue": {"max_depth": 12, "enabled": False}})
    legacy = {"lora_sf_max_depth": 50}
    assert lhc.lookup_setting("lora_sf_max_depth", 96, legacy=legacy) == 12
    assert lhc.lookup_setting("lora_sf_enabled", True, legacy=legacy) is False


def test_an_old_lora_config_is_used_until_the_section_exists(runtime):
    runtime({})
    legacy = {"lora_sf_max_depth": 50, "shutdown_iteration_limit": 2}
    assert lhc.lookup_setting("lora_sf_max_depth", 96, legacy=legacy) == 50
    assert lhc.lookup_setting("lora_sf_drain_per_cycle", 2, legacy=legacy) == 2      # default
    # the old file's copies of runtime parameters are ignored
    assert lhc.lookup_setting("shutdown_iteration_limit", 3, legacy=legacy) == 3


def test_get_config_value_needs_no_daemon(runtime):
    runtime({"area_threshold": 42})
    with patch.object(lhc, "get_lora_handler", return_value=None):
        assert lhc.get_config_value("area_threshold", 10) == 42


def test_handler_without_callback_stores_in_runtime_config(runtime, tmp_path):
    path = runtime({"area_threshold": 10})
    h = lhc.LoRaHandler(config_file=str(tmp_path / "missing_lora_config.json"))
    h.update_config("area_threshold", 55)
    assert json.loads(path.read_text())["area_threshold"] == 55
    assert h.get_config("area_threshold") == 55
    h.update_config("area_threshold", 500)                   # out of range: refused
    assert json.loads(path.read_text())["area_threshold"] == 55


def test_handler_writes_no_lora_config(runtime, tmp_path):
    runtime({})
    legacy = tmp_path / "lora_config.json"
    h = lhc.LoRaHandler(config_file=str(legacy))
    h.update_config("emergency_mode", True)
    assert not legacy.exists()
