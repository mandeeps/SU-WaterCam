"""Remote start and emergency mode are separate commands.

'!' (0x21) is only a remote start: with the mDot's Class C packet processor on,
the firmware powers a sleeping Pi on and prints "EMERGENCY: PA_6 pin state: N";
with it off, the packet reaches the Pi as the hex payload "21". Neither turns
emergency mode on any more: it used to only when the Pi was already awake, so the
same command meant two things depending on power state.

Emergency mode is 21 91 HH (HH hours, hex; 00 = emergency_max_hours), and the node
answers with its status. 99 99 still ends it.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.lora_handler_concurrent import LoRaHandler
from tools.lora_runtime_integration import LoRaRuntimeManager


@pytest.fixture
def handler(tmp_path):
    h = LoRaHandler(config_file=str(tmp_path / "lora_config.json"))
    h.update_config("emergency_mode", False)
    return h


@pytest.fixture
def manager(tmp_path):
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text(json.dumps({"emergency_mode": False}))
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        m = LoRaRuntimeManager(config_file=str(cfg))
    m.lora_handler = None
    return m


# ── the handler in lora_daemon ──────────────────────────────────────────────

def test_payload_21_is_only_a_remote_start(handler):
    handler.decode("21")
    assert handler.config["emergency_mode"] is False


def test_the_firmware_line_does_not_turn_emergency_on(handler):
    with patch("tools.lora_runtime_integration.set_parameter") as set_param:
        assert handler._is_emergency_message("EMERGENCY: PA_6 pin state: 1")
    set_param.assert_not_called()


@pytest.mark.parametrize("payload, hours", [("219106", 6), ("219130", 48), ("219100", None)])
def test_21_91_turns_emergency_on_for_hh_hours(handler, payload, hours):
    with patch.object(LoRaHandler, "_reply_with_status") as reply:
        handler.decode(payload)
    assert handler.config["emergency_mode"] is True
    assert isinstance(handler.config["emergency_since"], float)
    if hours:
        assert handler.config["emergency_max_hours"] == hours
    else:
        assert "emergency_max_hours" not in handler.config     # default kept
    reply.assert_called_once()


def test_21_91_duration_is_capped(handler):
    with patch.object(LoRaHandler, "_reply_with_status"):
        handler.decode("2191ff")
    assert handler.config["emergency_max_hours"] == 168


@pytest.mark.parametrize("payload", ["2100", "2101", "21000", "210", "121", "00", "9921"])
def test_other_payloads_do_not(handler, payload):
    handler.decode(payload)
    assert handler.config["emergency_mode"] is False


@pytest.mark.parametrize("line", ["EMERGENCY: PA_6 pin state: 0",
                                  "EMERGENCY: PA_6 pin state: 1",
                                  "EMERGENCY: PA_6 pin state: 1\r\n"])
def test_the_firmware_emergency_line_is_recognised(handler, line):
    assert handler._is_emergency_message(line)


@pytest.mark.parametrize("line", ["EMERGENCY: Activating switch on PB_1...",
                                  "EMERGENCY: Input pin HIGH, no action taken",
                                  "EMERGENCY: PB_1 state check: 1",
                                  "emergency: pa_6 pin state: 0",
                                  "NOT AN EMERGENCY: PA_6 pin state: 0",
                                  "EMERGENCY: PA_6 pin state: 2",
                                  "Class C packet processor EMERGENCY test",
                                  "EMERGENCY"])
def test_no_other_line_counts(handler, line):
    assert not handler._is_emergency_message(line)


# ── the runtime manager's own payload parser ────────────────────────────────

def test_runtime_payload_21_is_only_a_remote_start(manager):
    assert manager.process_lora_payload("21")
    assert manager.get_parameter("emergency_mode") is False


def test_emergency_max_hours_is_validated(manager):
    assert manager.set_parameter("emergency_max_hours", 12)
    assert not manager.set_parameter("emergency_max_hours", 500)


@pytest.mark.parametrize("payload", ["2100", "21 00 00"])
def test_runtime_tlv_21_no_longer_does(manager, payload):
    manager.process_lora_payload(payload)
    assert manager.get_parameter("emergency_mode") is False
