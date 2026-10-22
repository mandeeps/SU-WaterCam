"""The IP queue: cellular drain cap, one bitmap, 4xx and malformed entries, fsynced writes."""
import json
import os
import time
from unittest.mock import patch

import pytest

import tools.transmit_ip as tip
from tools.transmit_ip import IPTransmitter


def _tx(tmp_path):
    return IPTransmitter(config_path=str(tmp_path / "none.json"), queue_dir=str(tmp_path / "q"))


def _ch(bitmap=False, pad=0):
    ch = [{"code": "00 01", "payload_hex": "00" * 8}, {"code": "06 01", "payload_hex": "23"}]
    if bitmap:
        ch.append({"code": "08 18", "payload_b64": "AQID"})
    if pad:
        ch.append({"code": "09 19", "payload_hex": "ab" * pad})
    return ch


def _ok():
    return {"success": True, "status_code": 200, "attempts": 1, "error": None}


def _queue(tx, n, **kw):
    t0 = int(time.time())
    for i in range(n):
        assert tx._enqueue(_ch(**kw), t0 + i)


def _bitmaps(tx):
    out = []
    for f in sorted(os.listdir(tx._queue_dir)):
        if f.endswith(".json"):
            rec = json.load(open(os.path.join(tx._queue_dir, f)))
            out.append(any(c["code"] == "08 18" for c in rec["channels"]))
    return out


def test_wifi_drains_everything_cellular_a_few(tmp_path):
    tx = _tx(tmp_path)
    _queue(tx, 8)
    with patch.object(IPTransmitter, "send_uplink", return_value=_ok()):
        r = tx._drain_queue(metered=True)
        assert r["drained"] == tip.CELLULAR_DRAIN_MAX_ENTRIES and r["limited"] and not r["failed"]
        r = tx._drain_queue()
        assert r["drained"] == 3 and not r["limited"]


def test_cellular_byte_cap(tmp_path, monkeypatch):
    tx = _tx(tmp_path)
    monkeypatch.setattr(tip, "CELLULAR_DRAIN_MAX_BYTES", 3000)
    _queue(tx, 6, pad=700)                  # about 1.5 KB of JSON each
    with patch.object(IPTransmitter, "send_uplink", return_value=_ok()):
        r = tx._drain_queue(metered=True)
    assert 1 <= r["drained"] < 6 and r["limited"]


def test_only_the_newest_bitmap_stays_queued(tmp_path):
    tx = _tx(tmp_path)
    t = int(time.time())
    tx._enqueue(_ch(bitmap=True), t)
    tx._enqueue(_ch(bitmap=True), t + 1)
    tx._enqueue(_ch(), t + 2)               # no bitmap: leaves the others alone
    assert _bitmaps(tx) == [False, True, False]
    tx._enqueue(_ch(bitmap=True), t + 3)
    assert _bitmaps(tx) == [False, False, False, True]


def test_an_older_capture_does_not_strip_a_newer_bitmap(tmp_path):
    tx = _tx(tmp_path)
    t = int(time.time())
    tx._enqueue(_ch(bitmap=True), t + 5)
    tx._enqueue(_ch(bitmap=True), t)
    assert _bitmaps(tx) == [True, True]


@pytest.mark.parametrize("status,dropped", [(400, True), (404, True), (413, True), (422, True),
                                             (401, False), (403, False), (429, False), (500, False)])
def test_a_rejected_entry_is_dropped_but_auth_and_rate_limits_block(tmp_path, status, dropped):
    tx = _tx(tmp_path)
    _queue(tx, 2)
    bad = {"success": False, "status_code": status, "attempts": 1, "error": f"HTTP {status}"}
    with patch.object(IPTransmitter, "send_uplink", side_effect=[bad, _ok()]):
        r = tx._drain_queue()
    if dropped:
        assert r["drained"] == 1 and not r["failed"]
        assert len(tx._queued_files()) == 0
    else:
        assert r["failed"] and r["drained"] == 0 and len(tx._queued_files()) == 2


def test_malformed_files_are_dropped_not_fatal(tmp_path):
    tx = _tx(tmp_path)
    os.makedirs(tx._queue_dir)
    for name, body in (("1_000000.json", '{"device_ts": 1, "enqueued_at": %d}' % time.time()),
                       ("2_000000.json", '[1, 2]'),
                       ("3_000000.json", '{"channels": "x", "enqueued_at": %d}' % time.time()),
                       ("4_000000.json", '{not json')):
        open(os.path.join(tx._queue_dir, name), "w").write(body)
    _queue(tx, 1)
    with patch.object(IPTransmitter, "send_uplink", return_value=_ok()) as send:
        r = tx._drain_queue()
    assert r["drained"] == 1 and send.call_count == 1 and tx._queued_files() == []


def test_enqueue_uses_fsynced_write_json_and_orphans_are_cleaned(tmp_path):
    tx = _tx(tmp_path)
    os.makedirs(tx._queue_dir)
    old = os.path.join(tx._queue_dir, "5_000000.json.tmp.123")
    fresh = os.path.join(tx._queue_dir, "6_000000.json.tmp.456")
    for p in (old, fresh):
        open(p, "w").write("{")
    past = time.time() - tip.ORPHAN_TMP_AGE_S - 10
    os.utime(old, (past, past))
    with patch.object(tip.config_io, "write_json", wraps=tip.config_io.write_json) as w:
        assert tx._enqueue(_ch(), int(time.time()))
    assert w.called
    assert not os.path.exists(old) and os.path.exists(fresh)    # a recent one may be a live write
