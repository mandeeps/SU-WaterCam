"""Small payload limits (US915 DR0: 11 bytes): the compact packet and queueing
what doesn't fit (tools/lora_handler_concurrent.py encode_compact_packet,
tools/lora_store_forward.py drain(size_limit=...))."""
import json
import os
import shutil
import struct
import subprocess

import pytest

from tools import lora_store_forward as lsf
from tools.lora_handler_concurrent import COMPACT_PACKET_SIZE, encode_compact_packet

CODEC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "config", "chirpstack_device_profile_codec.js")


def test_compact_packet_layout():
    pkt = encode_compact_packet({
        "timestamp": 1760411904, "emergency_status": 1, "camera_flood_detected": 1,
        "camera_flood_growing": 0, "health_status": 0, "movement_threshold": 1,
        "pi_throttled": 0x50005, "battery_percent": 85, "wittypi_battery_voltage": 4.95,
        "temperature_celsius": 21.4, "relative_humidity": 55,
        "lat_lon_z": [10.0, 20.0, 30.0], "tilt_roll_yaw": [1.0, 2.0, 3.0],
    })
    assert len(pkt) == COMPACT_PACKET_SIZE == 11
    assert pkt[:2] == b"\x0c\x01"
    assert struct.unpack(">I", pkt[2:6])[0] == 1760411904
    assert pkt[6] == 0b1110011           # emergency, flood, moved, under-voltage now and since boot
    assert pkt[7] == 85 and pkt[8] == 198 and struct.unpack(">b", pkt[9:10])[0] == 21 and pkt[10] == 55


def test_compact_packet_missing_values():
    pkt = encode_compact_packet({"timestamp": 5})
    assert len(pkt) == 11
    assert pkt[6] == 0 and pkt[7] == 255 and pkt[8] == 255 and pkt[9:10] == b"\x80" and pkt[10] == 255


def test_compact_packet_clamps():
    pkt = encode_compact_packet({"timestamp": 1, "battery_percent": 300, "wittypi_battery_voltage": 9.0,
                                 "temperature_celsius": -200, "relative_humidity": -3})
    assert pkt[7] == 254 and pkt[8] == 254 and struct.unpack(">b", pkt[9:10])[0] == -127 and pkt[10] == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_codec_decodes_compact_packet():
    pkt = encode_compact_packet({"timestamp": 1760411904, "camera_flood_detected": 1,
                                 "battery_percent": 85, "wittypi_battery_voltage": 4.95,
                                 "temperature_celsius": -3, "relative_humidity": 55})
    js = ("const fs=require('fs');eval(fs.readFileSync(process.argv[1],'utf8'));"
          "console.log(JSON.stringify(decodeUplink({bytes:JSON.parse(process.argv[2]),fPort:1}).data));")
    out = json.loads(subprocess.check_output(["node", "-e", js, CODEC, json.dumps(list(pkt))], text=True))
    assert out["compact"] == 1 and out["timestamp"] == 1760411904
    assert out["camera_flood_detected"] == 1 and out["emergency_status"] == 0
    assert out["battery_percent"] == 85 and out["wittypi_battery_voltage"] == 4.95
    assert out["temperature_celsius"] == -3 and out["relative_humidity"] == 55


class FakeHandler:
    def __init__(self, fail_on=()):
        self.sent, self.fail_on = [], set(fail_on)

    def transmit(self, hex_payload):
        if hex_payload in self.fail_on:
            return False
        self.sent.append(hex_payload)
        return True


def _queue(tmp_path, n, sensor_len=59, bitmap_len=None):
    for i in range(n):
        assert lsf.enqueue("aa" * sensor_len, ("bb" * bitmap_len) if bitmap_len else None,
                           1000 + i, queue_dir=str(tmp_path))


def test_drain_defers_payloads_over_the_limit(tmp_path):
    _queue(tmp_path, 2)
    h = FakeHandler()
    r = lsf.drain(h, queue_dir=str(tmp_path), size_limit=11)
    assert r == {"drained": 0, "failed": False, "deferred": True}
    assert h.sent == [] and lsf.queue_depth(str(tmp_path)) == 2


def test_drain_sends_once_the_limit_allows(tmp_path):
    _queue(tmp_path, 3)
    h = FakeHandler()
    r = lsf.drain(h, max_packets=2, queue_dir=str(tmp_path), size_limit=240)
    assert r["drained"] == 2 and not r["deferred"] and len(h.sent) == 2
    assert lsf.queue_depth(str(tmp_path)) == 1


def test_drain_keeps_order_behind_an_oversize_entry(tmp_path):
    _queue(tmp_path, 1, sensor_len=59, bitmap_len=200)   # oldest: bitmap needs a high data rate
    _queue(tmp_path, 1, sensor_len=20)
    h = FakeHandler()
    r = lsf.drain(h, queue_dir=str(tmp_path), size_limit=125)
    assert r["deferred"] and h.sent == []


def test_drain_does_not_resend_a_delivered_half(tmp_path):
    _queue(tmp_path, 1, sensor_len=30, bitmap_len=40)
    h = FakeHandler(fail_on={"bb" * 40})
    r = lsf.drain(h, queue_dir=str(tmp_path), size_limit=240)
    assert r["failed"] and h.sent == ["aa" * 30]
    h.fail_on.clear()
    r = lsf.drain(h, queue_dir=str(tmp_path), size_limit=240)
    assert r["drained"] == 1 and h.sent == ["aa" * 30, "bb" * 40]   # the sensor half only once
    assert lsf.queue_depth(str(tmp_path)) == 0


# Every field the node's sensor packet can carry, as ticktalk passes it (the
# 2026-10-15 test wake on 005, plus the fields that only appear sometimes).
FULL_SENSOR_DATA = {
    'timestamp': 1792108958, 'emergency_status': 0, 'movement_threshold': 0,
    'tilt_roll_yaw': (0.0, 7.125, 85.0625), 'temperature_celsius': 22.2,
    'relative_humidity': 35, 'wittypi_temperature': 30.625,
    'wittypi_internal_voltage': 4.88, 'wittypi_vin': 4.49, 'pi_throttled': 0,
    'registration_status': 0, 'software_version': 5, 'model_id': 320164049,
    'sf_dropped': 3, 'clock_unverified': 1, 'camera_water_pct': 70,
    'camera_flood_detected': 1, 'camera_flood_growing': 0,
    'status_area_threshold': 10, 'stage_threshold': 50, 'monitoring_frequency': 60,
    'emergency_frequency': 30, 'neighborhood_emergency_frequency': 30,
    'audio_recording_enabled': 1,
}


def _codec_decode(pkt):
    js = ("const fs=require('fs');eval(fs.readFileSync(process.argv[1],'utf8'));"
          "console.log(JSON.stringify(decodeUplink({bytes:JSON.parse(process.argv[2]),fPort:1}).data));")
    return json.loads(subprocess.check_output(["node", "-e", js, CODEC, json.dumps(list(pkt))], text=True))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_codec_decodes_every_sensor_channel():
    """The ChirpStack codec stops at the first channel it doesn't know and drops
    the rest of the packet, so a channel added on the node without a codec case
    silently loses everything after it (09 69 did, until 2026-10-15)."""
    from tools.lora_handler_concurrent import _encode_compressed_packet
    pkt = _encode_compressed_packet(dict(FULL_SENSOR_DATA))
    out = _codec_decode(pkt)
    # The last fields of the packet decode only if every channel before them did
    assert out["camera_water_pct"] == 70
    assert out["sf_dropped"] == 3
    assert out["clock_unverified"] == 1
    assert out["model_id"] == 320164049
    assert out["audio_recording_enabled"] == 1
    assert out["camera_flood_detected"] == 1
    assert out["timestamp"] == 1792108958 and out["status_area_threshold"] == 10
    # Compact forms (03 02, 05 02): same field names and units as before
    assert out["tilt_roll_yaw"] == [0.0, 7.12, 85.06]
    assert out["temperature_celsius"] == 22.2
    # The Witty Pi temperature and output voltage aren't sent over LoRa
    assert "wittypi_temperature" not in out and "wittypi_internal_voltage" not in out
    assert len(pkt) == 84   # 104 with the float32 and Witty Pi channels


def test_compact_fields_layout_and_limits():
    from tools.lora_handler_concurrent import _encode_compressed_packet
    pkt = _encode_compressed_packet({"tilt_roll_yaw": (359.999, -7.125, 400.0), "temperature_celsius": -12.345,
                                     "wittypi_temperature": 30.625, "wittypi_internal_voltage": 4.88})
    assert pkt == b"\x03\x02" + struct.pack(">Hhh", 0, -712, 32767) + b"\x05\x02" + struct.pack(">h", -1234)
    assert struct.unpack(">H", _encode_compressed_packet({"tilt_roll_yaw": [-10.0, 0, 0]})[2:4])[0] == 35000
    # No reading, or a partial one, is left out rather than failing the packet
    assert _encode_compressed_packet({"tilt_roll_yaw": (None, 1.0, 2.0), "temperature_celsius": float("nan")}) == b""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_codec_still_decodes_old_float_channels():
    """Units on older code send 03 01, 05 01, 0A 01 and 0A 03 as float32."""
    pkt = (b"\x00\x01" + struct.pack(">I", 1792108958) + b"\x03\x01" + struct.pack(">fff", 0.0, 7.125, 85.0625)
           + b"\x05\x01" + struct.pack(">f", 22.25) + b"\x0a\x01" + struct.pack(">f", 30.625)
           + b"\x0a\x03" + struct.pack(">f", 4.875) + b"\x07\x37\x46")
    out = _codec_decode(pkt)
    assert out["timestamp"] == 1792108958 and out["tilt_roll_yaw"] == [0.0, 7.125, 85.0625]
    assert out["temperature_celsius"] == 22.25 and out["wittypi_temperature"] == 30.625
    assert out["wittypi_internal_voltage"] == 4.875 and out["camera_water_pct"] == 70
