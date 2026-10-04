"""Only the downlink '21' (and the mDot's report of it) turns emergency mode on.

The emergency downlink is the single byte '!' (0x21). With the mDot's Class C
packet processor on, the firmware handles it and prints
"EMERGENCY: PA_6 pin state: N"; with it off, the packet reaches the Pi as the
hex payload "21". Nothing else may trigger emergency mode: not the TLV forms
"2100"/"21 0" that used to, and not any serial line merely containing
"EMERGENCY".
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

def test_payload_21_turns_emergency_on(handler):
    handler.decode("21")
    assert handler.config["emergency_mode"] is True


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

def test_runtime_payload_21_turns_emergency_on(manager):
    assert manager.process_lora_payload("21")
    assert manager.get_parameter("emergency_mode") is True


@pytest.mark.parametrize("payload", ["2100", "21 00 00"])
def test_runtime_tlv_21_no_longer_does(manager, payload):
    manager.process_lora_payload(payload)
    assert manager.get_parameter("emergency_mode") is False
