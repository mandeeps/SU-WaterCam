"""The LoRa decoder applies the frames the server encodes (API app/encoders.py).

They used to fall through to older parsers that never matched them, so the
dashboard's "Send Configuration" had no effect on LoRa nodes. Frames are
[code: 2 bytes][fixed-width value], possibly several in one downlink; the
handler applies them with the IP decoder, so both transports share one table.
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
    h.config, h.config_file, h.runtime_callback = {}, os.path.join(tempfile.mkdtemp(), "c.json"), None
    with patch.object(LoRaHandler, "_reply_with_status") as reply:
        h.reply = reply
        yield h


# What app/encoders.py sends for each dashboard field
@pytest.mark.parametrize("frame, key, value", [
    ("109032", "area_threshold", 50),              # area 50 %
    ("119100c8", "stage_threshold", 200),          # stage 2.00 m -> 200 cm (u16)
    ("129202", "monitoring_frequency", 360),       # 6 h (index 2) -> minutes
    ("139301", "emergency_frequency", 5),          # 5 min (index 1)
    ("149402", "neighborhood_emergency_frequency", 30),  # 30 min (index 2)
    ("169601", "audio_recording_enabled", True),
])
def test_dashboard_fields_are_applied(handler, frame, key, value):
    handler.decode(frame)
    assert handler.config[key] == value
    handler.reply.assert_called_once()


def test_several_commands_in_one_downlink(handler):
    handler.decode("109032" + "139301" + "169600")
    assert handler.config["area_threshold"] == 50
    assert handler.config["emergency_frequency"] == 5
    assert handler.config["audio_recording_enabled"] is False
    handler.reply.assert_called_once()


def test_uppercase_hex_from_the_mdot(handler):
    handler.decode("119100C8")
    assert handler.config["stage_threshold"] == 200


def test_out_of_range_index_is_skipped(handler):
    handler.decode("12920a")                       # no 11th monitoring frequency
    assert "monitoring_frequency" not in handler.config
    handler.reply.assert_not_called()


def test_actions_go_to_their_handlers(handler):
    with patch.object(LoRaHandler, "_start_emergency") as em, \
         patch.object(LoRaHandler, "_start_remote_debug") as rd:
        handler.decode("219106")
        handler.decode("189803")
    em.assert_called_once_with("06")
    rd.assert_called_once_with("03")


def test_emergency_off_frame(handler):
    handler.config["emergency_mode"] = True
    handler.decode("9999")
    assert handler.config["emergency_mode"] is False


@pytest.mark.parametrize("payload", ["5001", "0a5a0132", "1090", "10903", "109032ff", "zz"])
def test_other_formats_are_not_server_frames(payload):
    data = bytes.fromhex(payload) if len(payload) % 2 == 0 and payload != "zz" else None
    assert data is None or parse_downlink_frame(data) is None


def test_debug_status_request_still_works(handler):
    with patch("tools.lora_debug_integration.handle_debug_status_request",
               return_value={"status": "failed", "error": "test"}) as dbg:
        handler.current_size_limit = 51
        handler.decode("5001")
    dbg.assert_called_once()
