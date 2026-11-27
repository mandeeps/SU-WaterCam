"""The live IP reading is queued before its send and removed once it's delivered (#311)."""
import json
import os
import time
from unittest.mock import patch

from tools.transmit_ip import IPTransmitter

BITMAP = {"code": "08 18", "payload_b64": "AQID"}


def _ch(bitmap=False):
    ch = [{"code": "00 01", "payload_hex": "00" * 8}, {"code": "06 01", "payload_hex": "23"}]
    return ch + [dict(BITMAP)] if bitmap else ch


def _tx(tmp_path, **kw):
    return IPTransmitter(config_path=str(tmp_path / "none.json"), queue_dir=str(tmp_path / "q"), **kw)


def _ok():
    return {"success": True, "status_code": 200, "attempts": 1, "error": None}


def _codes(path):
    with open(path) as f:
        return [c["code"] for c in json.load(f)["channels"]]


def test_live_entry_is_on_disk_until_unqueued(tmp_path):
    tx = _tx(tmp_path)
    path = tx._enqueue_live(_ch(), 1000)
    assert os.path.exists(path)
    tx.unqueue(path)
    assert not os.path.exists(path)
    tx.unqueue(path)            # already gone (drained by another upload): no error
    tx.unqueue(None)


def test_queuing_live_leaves_older_bitmaps_until_the_send_fails(tmp_path):
    tx = _tx(tmp_path)
    tx._enqueue(_ch(bitmap=True), 900)
    old = os.path.join(tx._queue_dir, tx._queued_files()[0])
    live = tx._enqueue_live(_ch(bitmap=True), 1000)
    assert "08 18" in _codes(old)            # a successful send changes nothing else
    assert tx.keep_queued(live, _ch(bitmap=True), 1000)
    assert "08 18" not in _codes(old)        # failed: newest bitmap rule, as _enqueue
    assert "08 18" in _codes(live)


def test_keep_queued_trims_to_depth_but_never_the_live_entry(tmp_path):
    tx = _tx(tmp_path)
    tx.max_queue_depth = 3
    for i in range(3):
        tx._enqueue(_ch(), 900 + i)
    live = tx._enqueue_live(_ch(), 1000)
    assert len(tx._queued_files()) == 4      # over depth by one while the send runs
    tx.keep_queued(live, _ch(), 1000)
    files = tx._queued_files()
    assert len(files) == 3 and os.path.basename(live) in files
    assert not any(f.startswith("900_") for f in files)   # the oldest went


def test_keep_queued_queues_again_if_the_entry_is_gone(tmp_path):
    tx = _tx(tmp_path)
    live = tx._enqueue_live(_ch(), 1000)
    os.unlink(live)
    assert tx.keep_queued(live, _ch(), 1000)
    assert len(tx._queued_files()) == 1
    assert tx.keep_queued(None, _ch(), 1001)   # _enqueue_live couldn't write it
    assert len(tx._queued_files()) == 2


def test_drain_skips_the_live_entry(tmp_path):
    tx = _tx(tmp_path)
    tx._enqueue(_ch(), int(time.time()) - 10)
    live = tx._enqueue_live(_ch(), int(time.time()))
    with patch.object(tx, "send_uplink", return_value=_ok()) as send:
        result = tx._drain_queue(skip=live)
    assert result["drained"] == 1 and send.call_count == 1
    assert tx._queued_files() == [os.path.basename(live)]


def test_drain_without_skip_still_sends_everything(tmp_path):
    tx = _tx(tmp_path)
    tx._enqueue(_ch(), int(time.time()) - 10)
    tx._enqueue_live(_ch(), int(time.time()))
    with patch.object(tx, "send_uplink", return_value=_ok()):
        assert tx._drain_queue()["drained"] == 2


def test_enqueue_live_returns_none_on_io_error(tmp_path):
    tx = _tx(tmp_path)
    with patch("os.replace", side_effect=OSError("disk full")):
        assert tx._enqueue_live(_ch(), 1000) is None
