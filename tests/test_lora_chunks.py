"""LoRa sensor packet: per-field robustness, chunking, stage threshold as u16,
the queue's flock and chunk lists, the wake's deadline short-circuit, health cache."""
import json
import math
import os
import struct
import threading
import time
from unittest.mock import MagicMock

import pytest

from tools import lora_health as lh
from tools import lora_send
from tools import lora_store_forward as lsf
from tools.lora_handler_concurrent import (
    CHUNK_MIN_LIMIT, _encode_compressed_packet, encode_sensor_chunks)

FULL = {
    'timestamp': 1792108958, 'emergency_status': 0, 'movement_threshold': 0, 'health_status': 0,
    'battery_percent': 80, 'tilt_roll_yaw': (10.0, 7.125, 85.0625), 'temperature_celsius': 22.2,
    'relative_humidity': 35, 'lat_lon_z': [43.0, -76.0, 120.0], 'pi_throttled': 0,
    'registration_status': 0, 'software_version': 5, 'model_id': 320164049, 'sf_dropped': 3,
    'clock_unverified': 1, 'camera_water_pct': 70, 'camera_flood_detected': 1,
    'camera_flood_growing': 0, 'status_area_threshold': 10, 'stage_threshold': 50,
    'monitoring_frequency': 60, 'emergency_frequency': 30, 'neighborhood_emergency_frequency': 30,
    'audio_recording_enabled': 1, 'wittypi_battery_voltage': 4.9,
}


def test_stage_threshold_is_a_u16_in_cm():
    assert _encode_compressed_packet({'stage_threshold': 300}) == b"\x09\x29\x01\x2c"
    assert _encode_compressed_packet({'stage_threshold': 99999}) == b"\x09\x29\xff\xff"
    assert _encode_compressed_packet({'stage_threshold': -4}) == b"\x09\x29\x00\x00"


def test_no_witty_pi_battery_channel():
    assert b"\x0a\x02" not in _encode_compressed_packet({'wittypi_battery_voltage': 4.9})
    assert _encode_compressed_packet({'wittypi_battery_voltage': 4.9}) == b""


@pytest.mark.parametrize("bad", [float('nan'), float('inf'), "x", 300, -1, [1]])
def test_a_bad_field_drops_only_itself(bad):
    pkt = _encode_compressed_packet({'timestamp': 7, 'relative_humidity': bad, 'battery_percent': 50})
    assert pkt == b"\x00\x01\x00\x00\x00\x07" + b"\x02\x01\x32"


def test_bad_float_fields_are_skipped():
    pkt = _encode_compressed_packet({'timestamp': 7, 'lat_lon_z': [float('nan'), 1, 2],
                                     'temperature_celsius': math.inf, 'tilt_roll_yaw': (1, None, 2)})
    assert pkt == b"\x00\x01\x00\x00\x00\x07"


def _tlv_fields(chunk):
    """The (channel, type) pairs of a chunk; sizes are fixed except the ones skipped here."""
    sizes = {(0, 1): 4, (1, 4): 1, (1, 5): 1, (1, 6): 1, (2, 1): 1, (3, 2): 6, (4, 1): 12, (5, 2): 2,
             (6, 1): 1, (7, 0x17): 1, (7, 0x27): 1, (7, 0x37): 1, (9, 0x19): 1, (9, 0x29): 2,
             (9, 0x39): 2, (9, 0x49): 2, (9, 0x59): 2, (9, 0x69): 1, (1, 7): 1, (1, 8): 1,
             (1, 9): 4, (1, 0xB): 4, (1, 0xC): 4, (1, 0xD): 1}
    out, i = [], 0
    while i < len(chunk):
        key = (chunk[i], chunk[i + 1])
        out.append(key)
        i += 2 + sizes[key]
    assert i == len(chunk)
    return out


@pytest.mark.parametrize("limit", [CHUNK_MIN_LIMIT, 20, 51, 125, 242])
def test_chunks_fit_start_with_the_capture_time_and_hold_every_field_once(limit):
    chunks = encode_sensor_chunks(FULL, limit)
    assert chunks and all(len(c) <= limit and c[:2] == b"\x00\x01" for c in chunks)
    seen = [k for c in chunks for k in _tlv_fields(c) if k != (0, 1)]
    assert len(seen) == len(set(seen))
    whole = [k for k in _tlv_fields(_encode_compressed_packet(FULL)) if k != (0, 1)]
    if limit >= 20:                                    # GPS (14 B) needs room for its chunk
        assert sorted(seen) == sorted(whole)
    else:
        assert (4, 1) not in seen and set(seen) <= set(whole)


def test_priority_flood_and_emergency_first_gps_and_settings_later():
    first = _tlv_fields(encode_sensor_chunks(FULL, 20)[0])
    assert first[:4] == [(0, 1), (1, 4), (7, 0x17), (7, 0x27)]
    flat = [k for c in encode_sensor_chunks(FULL, 20) for k in _tlv_fields(c)]
    assert flat.index((7, 0x37)) < flat.index((4, 1)) < flat.index((9, 0x29)) < flat.index((1, 9))


def test_one_chunk_when_everything_fits():
    assert len(encode_sensor_chunks(FULL, 242)) == 1


def test_no_chunks_without_a_capture_time_or_room():
    assert encode_sensor_chunks({'battery_percent': 5}, 51) == []
    assert encode_sensor_chunks(FULL, 6) == []


# --- store and forward: chunk lists, skipping oversize entries, the lock

class Rec:
    def __init__(self, fail_on=()):
        self.sent, self.fail_on = [], set(fail_on)

    def transmit(self, h):
        if h in self.fail_on:
            return False
        self.sent.append(h)
        return True


def test_chunk_lists_drain_and_a_failed_chunk_stays(tmp_path):
    q = str(tmp_path)
    assert lsf.enqueue(["0a", "0b", "0c"], None, 1000, queue_dir=q)
    r = lsf.drain(Rec(fail_on={"0b"}), queue_dir=q, size_limit=51)
    assert r["failed"] and lsf.queue_depth(q) == 1
    rec = json.load(open(os.path.join(q, sorted(f for f in os.listdir(q) if f.endswith(".json"))[0])))
    assert rec["sensor_hex"] == ["0b", "0c"]
    h = Rec()
    assert lsf.drain(h, queue_dir=q, size_limit=51)["drained"] == 1
    assert h.sent == ["0b", "0c"] and lsf.queue_depth(q) == 0


def test_a_bitmap_superseded_during_a_drain_stays_gone(tmp_path):
    q = str(tmp_path)
    lsf.enqueue(["0a"], "bb", 1000, queue_dir=q)

    class Slow(Rec):
        def transmit(self, h):
            if h == "0a":
                lsf.supersede_bitmaps(2000, queue_dir=q)    # another process, mid-send
            return super().transmit(h)
    h = Slow()
    r = lsf.drain(h, queue_dir=q, size_limit=51)
    # the bitmap in the drain's copy still goes out; nothing is resurrected afterwards
    assert r["drained"] == 1 and lsf.queue_depth(q) == 0


def test_queue_operations_serialise_on_the_lock(tmp_path):
    q = str(tmp_path)
    lsf.enqueue(["0a"], "bb", 1000, queue_dir=q)
    done = []
    with lsf._locked(q):
        t = threading.Thread(target=lambda: done.append(lsf.supersede_bitmaps(2000, queue_dir=q)))
        t.start()
        t.join(0.3)
        assert t.is_alive() and not done        # waits for the lock
    t.join(5)
    assert done == [1]
    assert os.path.exists(os.path.join(q, lsf.LOCK_NAME))
    assert lsf.queue_depth(q) == 1


# --- one wake's sends: the deadline short-circuit

def test_after_a_deadline_failure_the_rest_are_not_tried(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(lora_send.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(lora_send.lora_health, "invalidate", MagicMock())
    h = MagicMock()

    def slow_fail(p):
        clock[0] += 75
        return False
    h.transmit.side_effect = slow_fail
    s = lora_send.WakeSender(h)
    assert s.transmit("aa") is False and s.stalled
    assert s.transmit("bb") is False
    assert h.transmit.call_count == 1
    lora_send.lora_health.invalidate.assert_called()


def test_a_quick_failure_does_not_stall_the_wake(monkeypatch):
    monkeypatch.setattr(lora_send.lora_health, "invalidate", MagicMock())
    h = MagicMock()
    h.transmit.side_effect = [False, True]
    s = lora_send.WakeSender(h)
    assert s.transmit("aa") is False and not s.stalled
    assert s.transmit("bb") is True
    lora_send.lora_health.invalidate.assert_called_once()


# --- the health cache

@pytest.fixture
def health_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(lh, "HISTORY_PATH", str(tmp_path / "h.json"))
    monkeypatch.setattr(lh, "CACHE_PATH", str(tmp_path / "c.json"))
    monkeypatch.setattr(lh, "_boot_id", lambda: "b")
    monkeypatch.setattr(lh, "_min_margin", lambda: 5)
    monkeypatch.setattr(lh.time, "sleep", lambda s: None)


def _h(ok=True, joined=True):
    h = MagicMock()
    h.is_joined.return_value = joined
    h.link_check.return_value = {"ok": ok, "margin_db": 12 if ok else None, "gateways": int(ok)}
    return h


def test_healthy_result_is_cached_for_half_an_hour(health_paths, monkeypatch):
    h = _h()
    assert lh.assess(h)["healthy"]
    assert lh.assess(h)["healthy"] and h.link_check.call_count == 1
    real = time.time()
    monkeypatch.setattr(lh.time, "time", lambda: real + lh.CACHE_TTL_S + 5)
    lh.assess(h)
    assert h.link_check.call_count == 2


def test_unhealthy_link_check_is_cached_for_ten_minutes(health_paths, monkeypatch):
    h = _h(ok=False)
    assert not lh.assess(h)["healthy"]
    lh.assess(h)
    assert h.link_check.call_count == 2         # two tries, then the cached result
    real = time.time()
    monkeypatch.setattr(lh.time, "time", lambda: real + lh.UNHEALTHY_TTL_S + 5)
    lh.assess(h)
    assert h.link_check.call_count == 4


def test_not_joined_is_never_cached(health_paths):
    h = _h(joined=False)
    assert not lh.assess(h)["healthy"]
    assert lh.cached() is None


def test_invalidate_forces_a_new_check(health_paths):
    h = _h()
    lh.assess(h)
    lh.invalidate()
    lh.assess(h)
    assert h.link_check.call_count == 2
