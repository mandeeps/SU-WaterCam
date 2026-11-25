"""A bitmap drained from the queue without its sensor chunks gets its capture
time sent just before it, and only then (tools/lora_store_forward.py, #333)."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import lora_store_forward as lsf  # noqa: E402

T0 = 1791400000
BITMAP = (bytes([0x08, 0x18, 0, 10]) + bytes(10)).hex()
SENSOR = (bytes([0x00, 0x01]) + struct.pack(">I", T0) + bytes([0x07, 0x37, 30])).hex()
TIME_A = (bytes([0x00, 0x01]) + struct.pack(">I", T0)).hex()


class Radio:
    def __init__(self, fail_on=None):
        self.sent, self.fail_on = [], fail_on

    def transmit(self, h):
        if h == self.fail_on:
            return False
        self.sent.append(h)
        return True


def _q(tmp_path, sensor, bitmap):
    d = str(tmp_path / "q")
    os.makedirs(d, exist_ok=True)
    assert lsf.enqueue(sensor, bitmap, T0, queue_dir=d)
    return d


def test_delayed_bitmap_gets_its_capture_time_first(tmp_path):
    d, r = _q(tmp_path, None, BITMAP), Radio()
    assert lsf.drain(r, queue_dir=d, size_limit=242)["drained"] == 1
    assert r.sent == [TIME_A, BITMAP]


def test_bitmap_after_its_sensor_chunks_gets_no_extra_packet(tmp_path):
    d, r = _q(tmp_path, SENSOR, BITMAP), Radio()
    lsf.drain(r, queue_dir=d, size_limit=242)
    assert r.sent == [SENSOR, BITMAP]


def test_sensor_only_record_gets_no_extra_packet(tmp_path):
    d, r = _q(tmp_path, SENSOR, None), Radio()
    lsf.drain(r, queue_dir=d, size_limit=242)
    assert r.sent == [SENSOR]


def test_retry_after_failed_bitmap_resends_capture_time(tmp_path):
    d = _q(tmp_path, SENSOR, BITMAP)
    r = Radio(fail_on=BITMAP)
    assert lsf.drain(r, queue_dir=d, size_limit=242)["failed"]
    assert r.sent == [SENSOR]          # chunks cleared, bitmap left queued
    r2 = Radio()
    lsf.drain(r2, queue_dir=d, size_limit=242)
    assert r2.sent == [TIME_A, BITMAP]


def test_failed_capture_time_keeps_the_bitmap_queued(tmp_path):
    d = _q(tmp_path, None, BITMAP)
    r = Radio(fail_on=TIME_A)
    res = lsf.drain(r, queue_dir=d, size_limit=242)
    assert res["failed"] and r.sent == []
    r2 = Radio()
    lsf.drain(r2, queue_dir=d, size_limit=242)
    assert r2.sent == [TIME_A, BITMAP]
