"""Tests for the Pi throttled/under-voltage flags (tools/pi_power.py) and their 01 07 encoding."""

import subprocess
from unittest import mock

from tools import pi_power
from tools.lora_handler_concurrent import _encode_compressed_packet


def _vcgencmd(stdout):
    return mock.patch.object(
        pi_power.subprocess, "run",
        return_value=subprocess.CompletedProcess(["vcgencmd"], 0, stdout=stdout, stderr=""),
    )


def test_get_throttled_parses_register():
    # The value unit 006 reported on the bench: under-voltage + throttled, now and since boot
    with _vcgencmd("throttled=0x50005\n"):
        assert pi_power.get_throttled() == 0x50005


def test_get_throttled_none_when_vcgencmd_missing():
    with mock.patch.object(pi_power.subprocess, "run", side_effect=FileNotFoundError):
        assert pi_power.get_throttled() is None


def test_get_throttled_none_on_garbage():
    with _vcgencmd("error=1 error_msg=\"Command not registered\"\n"):
        assert pi_power.get_throttled() is None


def test_pack_round_trip():
    for raw in (0x0, 0x50005, 0x50000, 0xF000F, 0x10001, 0x80008):
        packed = pi_power.pack_throttled_u8(raw)
        assert 0 <= packed <= 0xFF
        assert pi_power.unpack_throttled_u8(packed) == raw


def test_pack_layout():
    # now-bits low nibble, since-boot bits high nibble
    assert pi_power.pack_throttled_u8(0x50005) == 0x55
    assert pi_power.pack_throttled_u8(0x50000) == 0x50


def test_encoded_last_so_older_decoders_stop_cleanly():
    packet = _encode_compressed_packet({
        "timestamp": 1790727396,
        "battery_percent": 44,
        "wittypi_internal_voltage": 4.83,
        "pi_throttled": 0x50005,
    })
    assert packet[-3:] == bytes([0x01, 0x07, 0x55])


def test_omitted_when_unreadable():
    packet = _encode_compressed_packet({"battery_percent": 44, "pi_throttled": None})
    assert packet == bytes([0x02, 0x01, 44])
