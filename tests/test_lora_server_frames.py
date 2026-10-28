"""The LoRa decoder applies the frames the server encodes (API app/encoders.py).

They used to fall through to older parsers that never matched them, so the
dashboard's "Send Configuration" had no effect on LoRa nodes. A frame is
[seq: 1 byte][code: 2 bytes][fixed-width value]..., possibly several blocks in
one downlink; the handler applies them with the IP decoder, so both transports
share one table, and answers each frame with one status reply carrying the ack.
"""
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.lora_handler_concurrent import LoRaHandler  # noqa: E402
from tools.transmit_ip import parse_downlink_frame  # noqa: E402


@pytest.fixture
def handler():
    h = LoRaHandler.__new__(LoRaHandler)
    # h.config stands in for runtime_config.json: commands reach it through the runtime callback
    h.config, h.config_file = {}, os.path.join(tempfile.mkdtemp(), "c.json")
    h.runtime_callback = h.config.__setitem__
    with patch.object(LoRaHandler, "_reply_with_status") as reply:
        h.reply = reply
        yield h


# What the API sends for each dashboard field: seq 07 first
@pytest.mark.parametrize("frame, key, value", [
    ("07109032", "area_threshold", 50),              # area 50 %
    ("07119100c8", "stage_threshold", 200),          # stage 2.00 m -> 200 cm (u16)
    ("07139305", "emergency_frequency", 5),          # 5 min
    ("0714941e", "neighborhood_emergency_frequency", 30),  # 30 min
    ("07169601", "audio_recording_enabled", True),
])
def test_dashboard_fields_are_applied(handler, frame, key, value):
    handler.decode(frame)
    assert handler.config[key] == value
    handler.reply.assert_called_once_with(ack=(7, 0))


def test_several_commands_in_one_downlink(handler):
    handler.decode("07" + "109032" + "139305" + "169600")
    assert handler.config["area_threshold"] == 50
    assert handler.config["emergency_frequency"] == 5
    assert handler.config["audio_recording_enabled"] is False
    handler.reply.assert_called_once_with(ack=(7, 0))     # one ack per frame


def test_uppercase_hex_from_the_mdot(handler):
    handler.decode("07119100C8")
    assert handler.config["stage_threshold"] == 200


def test_out_of_range_interval_is_skipped_and_acked_rejected(handler):
    handler.decode("07139300")                     # 0 minutes
    assert "emergency_frequency" not in handler.config
    handler.reply.assert_called_once_with(ack=(7, 2))


def test_partly_applied_frame_is_acked_as_partial(handler):
    handler.decode("07" + "109032" + "ffff")        # unknown second block: the whole frame is invalid
    assert "area_threshold" not in handler.config
    handler.reply.assert_called_once_with(ack=(7, 2))
    handler.reply.reset_mock()
    handler.decode("08" + "109032" + "1393ff")      # 255 min: out of range, the area is applied
    assert handler.config["area_threshold"] == 50
    handler.reply.assert_called_once_with(ack=(8, 1))


def test_actions_go_to_their_handlers(handler):
    with patch.object(LoRaHandler, "_start_emergency", return_value=True) as em, \
         patch.object(LoRaHandler, "_start_remote_debug") as rd:
        handler.decode("01219106")
        handler.decode("02189803")
        handler.decode("03189903")
        handler.decode("04189a03")
    em.assert_called_once_with("06")
    assert [c.args for c in rd.call_args_list] == [("03", "18 98"), ("03", "18 99"), ("03", "18 9A")]


def test_emergency_off_frame(handler):
    handler.config["emergency_mode"] = True
    handler.decode("059999")
    assert handler.config["emergency_mode"] is False
    handler.reply.assert_called_once_with(ack=(5, 0))


@pytest.mark.parametrize("payload", ["0a5a0132", "1090", "10903", "109032ff", "zz"])
def test_other_formats_are_not_server_frames(payload):
    data = bytes.fromhex(payload) if len(payload) % 2 == 0 and payload != "zz" else None
    assert data is None or parse_downlink_frame(data) is None


def test_debug_status_request_is_a_command(handler):
    handler.decode("0b5001")
    handler.reply.assert_called_once_with(ack=(11, 0))


@pytest.mark.parametrize("payload", ["07149402", "0e5e0102", "14945"])
def test_14_94_never_sets_photo_interval(handler, payload):
    """#116: 14 94 is the flood-code frequency on every path, never photo_interval."""
    handler.config["photo_interval"] = 60
    handler.decode(payload)
    assert handler.config["photo_interval"] == 60


def test_runtime_manager_14_94_never_sets_photo_interval(tmp_path):
    import json
    from tools.lora_runtime_integration import LoRaRuntimeManager
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text(json.dumps({"photo_interval": 60}))
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        m = LoRaRuntimeManager(config_file=str(cfg))
    for payload in ("149402", "0e5e0102", "14945"):  # seqless: the manager is not the LoRa path
        m.process_lora_payload(payload)
    assert m.get_parameter("photo_interval") == 60


# ── one downlink table: legacy codes gone, code 12 is the server's index ────

@pytest.mark.parametrize("payload", [
    "22000001", "31000005", "32000003", "40000000", "41000005", "42000007", "43000001", "15950001", "44000001",   # TLV
    "22001", "31005", "32003", "40000", "41005", "42007", "43001", "15951", "44001",              # digits
])
def test_removed_legacy_codes_ignored(handler, payload):
    before = dict(handler.config)
    handler.decode(payload)
    for key in ("debug_mode", "compression_level", "max_retransmissions",
                "auto_shutdown_enabled", "shutdown_iteration_limit",
                "data_retention_days", "backup_enabled",
                "neighborhood_emergency_frequency", "audio_recording_enabled"):
        assert handler.config.get(key) == before.get(key)


@pytest.mark.parametrize("payload", ["22000001", "40000000", "41000001", "42000001", "43000000", "31000005", "32000003"])
def test_runtime_manager_ignores_removed_codes(tmp_path, payload):
    import json
    from tools.lora_runtime_integration import LoRaRuntimeManager
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text(json.dumps({"auto_shutdown_enabled": True, "shutdown_iteration_limit": 2}))
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        m = LoRaRuntimeManager(config_file=str(cfg))
    assert not m.process_lora_payload(payload)
    assert m.get_parameter("auto_shutdown_enabled") is True
    assert m.get_parameter("shutdown_iteration_limit") == 2


def test_handler_code_15_is_the_schedule(handler):
    from tools import schedule_control
    with patch.object(schedule_control, "apply", return_value=(True, "applied")) as apply:
        handler.decode("071595" + "0800100f007814")   # 08:00-16:15, 120 min, 20 min awake
    assert apply.call_args.args[0] == schedule_control.Schedule(480, 975, 120, 20)
    handler.reply.assert_called_once_with(ack=(7, 0))


def test_debug_status_typed_as_text_in_chirpstack(handler):
    """006 got "5001" as ASCII text (35 30 30 31) and read it as channel 35/30; no reply."""
    handler.decode("303035303031")      # "005001" as text
    handler.reply.assert_called_once_with(ack=(0, 0))


def test_text_hex_emergency_off(handler):
    handler.config["emergency_mode"] = True
    handler.decode("303039393939")      # "009999" as text
    assert handler.config["emergency_mode"] is False


@pytest.mark.parametrize("payload", ["005001", "9999", "109005", "2191", "189801", "21", "a1b2", "zz", ""])
def test_real_commands_are_never_unwrapped(handler, payload):
    assert handler._unwrap_text_hex(payload) == payload
