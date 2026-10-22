"""A bitmap queued at DR0 must drain once there is room, even a few bytes less
than 242 B (a MAC answer pending), and the bitmap policy counts a bitmap as
sent only when the mDot transmits it (tools/bitmap_fit.py,
tools/lora_store_forward.py, tools/bitmap_policy.py)."""
import functools
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import bitmap_fit, bitmap_policy as bp, lora_store_forward as lsf  # noqa: E402
from tools import sensor_changes as sc  # noqa: E402
from tools.compress_segmented import compress_image  # noqa: E402
from tools.flood_baseline import MASK_NAMES  # noqa: E402
from tools.lora_handler_concurrent import _encode_compressed_packet  # noqa: E402

T0 = 1791400000
#: Stands in for a bitmap compressed for the full 242 B room (238 B)
FULL_ROOM_BITMAP = bytes([2, 0, 75, 0, 56]) + bytes(233)


def _capture(tmp_path, name, t, seed):
    """A capture directory with a busy mask (needs most of 238 B) and its time."""
    cap = tmp_path / name
    cap.mkdir()
    (cap / "capture_time").write_text(f"{t}\n")
    rng = np.random.default_rng(seed)
    arr = (rng.random((24, 32)) > 0.5).astype(np.uint8)
    arr = np.kron(arr, np.ones((8, 8), np.uint8))     # 192 x 256 blocks
    Image.fromarray(arr * 255, mode="L").save(cap / MASK_NAMES[0])
    return cap


def _bitmap(cap, max_bytes):
    r = compress_image(str(cap / MASK_NAMES[0]), max_bytes=max_bytes, method2=True)
    assert r["success"]
    return r["compressed_data"]


class Node:
    """lora_token_with_tracker with a real queue and real bitmap policy state."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.q = str(tmp_path / "lora_pending")
        self.state = str(tmp_path / "bitmap_state.json")
        self.sent = []

    def run(self, cap, bitmap, size_limit, transmit_ok=True):
        import ticktalk_main
        h = MagicMock()
        h.is_joined.return_value = True
        h.size_limit_now.return_value = size_limit
        h.compressed_encoding.side_effect = _encode_compressed_packet

        def transmit(hex_payload):
            if not transmit_ok or len(hex_payload) // 2 > size_limit:
                return False
            self.sent.append(hex_payload)
            return True
        h.transmit.side_effect = transmit
        q = self.q
        with ExitStack() as st:
            p = st.enter_context
            p(patch.object(sc, "DEFAULT_STATE_PATH", str(self.tmp / "last_sent.json")))
            p(patch.object(bp, "DEFAULT_STATE_PATH", self.state))
            p(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=h))
            p(patch("tools.drop_counter.DEFAULT_PATH", str(self.tmp / "sf_dropped.json")))
            p(patch("tools.lora_handler_concurrent.get_config_value", side_effect=lambda k, d=None: d))
            p(patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d=None: d))
            p(patch("tools.bno055_imu.get_orientation", return_value={}))
            p(patch("tools.aht20_temperature.get_aht20",
                    return_value={"temperature_celsius": 20.0, "relative_humidity": 50}))
            p(patch("tools.get_gps.get_location_with_retry", return_value=(None, None)))
            p(patch("tools.wittypi_control.get_wittypi_status", return_value={"status": "none"}))
            p(patch("tools.battery_manager.get_battery_status",
                    return_value={"battery_pct": 80, "battery_source": "test"}))
            p(patch("tools.pi_power.get_throttled", return_value=0))
            p(patch("tools.gps_home.movement_fields", return_value=({}, None)))
            for fn in ("enqueue", "drain", "supersede_bitmaps", "queue_depth", "pending_bitmaps"):
                p(patch(f"tools.lora_store_forward.{fn}",
                        functools.partial(getattr(lsf, fn), queue_dir=q)))
            ticktalk_main.lora_token_with_tracker.__wrapped__(
                bitmap, {"change_threshold": 0.05, "previous_values": {}}, str(cap))
        return h

    def queued(self):
        out = []
        for f in sorted(x for x in os.listdir(self.q) if x.endswith(".json")) if os.path.isdir(self.q) else []:
            with open(os.path.join(self.q, f)) as fh:
                out.append(json.load(fh))
        return out

    def bitmaps_sent(self):
        return [p for p in self.sent if p.startswith("0818")]

    def policy(self):
        return bp.load_state(self.state)


def test_dr0_then_240_byte_room_drains_the_queued_bitmap(tmp_path):
    """The bench sequence: capture 1 at DR0 (11 B), capture 2 with 240 B of room."""
    node = Node(tmp_path)
    c1 = _capture(tmp_path, "20261016-120000", T0, seed=1)
    # compress_bitmap at an 11 B stored limit: the queue budget
    b1 = _bitmap(c1, bitmap_fit.queue_budget())
    node.run(c1, b1, size_limit=11)
    [entry] = node.queued()
    assert entry["bitmap_hex"] and len(entry["bitmap_hex"]) // 2 <= 242 - bitmap_fit.QUEUE_MARGIN
    assert entry["mask_path"].endswith(MASK_NAMES[0])
    assert node.bitmaps_sent() == []
    assert node.policy() is None                     # queued is not sent
    assert lsf.pending_bitmaps(node.q) == 1          # held back: counts for the IP fallback

    # Capture 2, same scene: the policy compares with the last bitmap *sent*
    # (none), so it sends; compressed for the stored 242 B limit, then again
    # for the 240 B room now. Capture 1's queued bitmap is superseded.
    c2 = _capture(tmp_path, "20261016-120100", T0 + 60, seed=1)
    b2 = FULL_ROOM_BITMAP          # compressed for 238 B: a 242 B packet
    node.run(c2, b2, size_limit=240)
    [sent] = node.bitmaps_sent()
    assert len(sent) // 2 <= 240 and bytes.fromhex(sent)[4:] != b2
    assert lsf.pending_bitmaps(node.q) == 0
    assert all(not e.get("bitmap_hex") for e in node.queued())
    state = node.policy()
    assert state["capture"] == "20261016-120100" and state["time"] == T0 + 60


def test_queued_bitmap_drains_at_240_when_the_policy_skips(tmp_path):
    node = Node(tmp_path)
    c1 = _capture(tmp_path, "20261016-120000", T0, seed=2)
    node.run(c1, _bitmap(c1, bitmap_fit.queue_budget()), size_limit=11)
    assert node.policy() is None
    c2 = _capture(tmp_path, "20261016-120100", T0 + 60, seed=2)
    with patch("tools.bitmap_policy.lora_decision", return_value=(False, "test")):
        node.run(c2, _bitmap(c2, 238), size_limit=240)
    [sent] = node.bitmaps_sent()                     # capture 1's, from the queue
    assert len(sent) // 2 <= 240
    assert lsf.pending_bitmaps(node.q) == 0
    assert node.policy()["capture"] == "20261016-120000"   # marked when drained


def test_oversize_queued_bitmap_is_compressed_again_on_drain(tmp_path):
    """An entry holding a 242 B packet (queued after a failed send at full room)
    goes out at 240 B, compressed again from the capture's mask."""
    q = str(tmp_path / "q")
    c1 = _capture(tmp_path, "20261016-120000", T0, seed=3)
    pkt = _encode_compressed_packet({"flood_bitmap_compressed": FULL_ROOM_BITMAP})
    assert len(pkt) == 242
    marked = []
    assert lsf.enqueue(None, pkt.hex(), T0, queue_dir=q, mask_path=str(c1 / MASK_NAMES[0]),
                       bitmap_policy={"camera_water_pct": 50, "capture": c1.name})
    h = MagicMock()
    h.transmit.return_value = True
    refit = lambda rec, lim: bitmap_fit.refit_packet(rec.get("mask_path"), lim,  # noqa: E731
                                                     _encode_compressed_packet, True)
    r = lsf.drain(h, queue_dir=q, size_limit=240, refit=refit, on_bitmap_sent=marked.append)
    assert r["drained"] == 1 and not r["deferred"]
    [sent] = [c.args[0] for c in h.transmit.call_args_list]
    assert sent.startswith("0818") and len(sent) // 2 <= 240
    assert marked and marked[0]["bitmap_policy"]["capture"] == c1.name


def test_no_room_for_a_bitmap_still_waits(tmp_path):
    q = str(tmp_path / "q")
    c1 = _capture(tmp_path, "20261016-120000", T0, seed=3)
    pkt = _encode_compressed_packet({"flood_bitmap_compressed": _bitmap(c1, 226)})
    lsf.enqueue(None, pkt.hex(), T0, queue_dir=q, mask_path=str(c1 / MASK_NAMES[0]))
    h = MagicMock()
    refit = lambda rec, lim: bitmap_fit.refit_packet(rec.get("mask_path"), lim,  # noqa: E731
                                                     _encode_compressed_packet, True)
    r = lsf.drain(h, queue_dir=q, size_limit=11, refit=refit)
    assert r["deferred"] and not h.transmit.called and lsf.pending_bitmaps(q) == 1


def test_newer_bitmap_replaces_stale_queued_ones(tmp_path):
    q = str(tmp_path / "q")
    lsf.enqueue("aa" * 10, "0818" + "bb" * 20, T0, queue_dir=q)
    lsf.enqueue(None, "0818" + "cc" * 20, T0 + 60, queue_dir=q)
    lsf.enqueue("dd" * 10, "0818" + "ee" * 20, T0 + 120, queue_dir=q)
    entries = []
    for f in sorted(x for x in os.listdir(q) if x.endswith(".json")):
        with open(os.path.join(q, f)) as fh:
            entries.append(json.load(fh))
    # the oldest keeps its sensor packet, the bitmap-only entry is gone
    assert [(e["sensor_hex"], e["bitmap_hex"]) for e in entries] == [
        ("aa" * 10, None), ("dd" * 10, "0818" + "ee" * 20)]
    assert lsf.pending_bitmaps(q) == 1


def test_superseded_bitmaps_are_not_counted_as_dropped(tmp_path):
    q = str(tmp_path / "q")
    with patch("tools.drop_counter.DEFAULT_PATH", str(tmp_path / "sf_dropped.json")):
        from tools import drop_counter
        lsf.enqueue(None, "0818" + "bb" * 20, T0, queue_dir=q)
        lsf.enqueue(None, "0818" + "cc" * 20, T0 + 60, queue_dir=q)
        assert drop_counter.total(drop_counter.path_for(q)) == 0
    assert lsf.queue_depth(q) == 1


def test_failed_live_send_is_not_marked(tmp_path):
    node = Node(tmp_path)
    c1 = _capture(tmp_path, "20261016-120000", T0, seed=4)
    node.run(c1, _bitmap(c1, 200), size_limit=242, transmit_ok=False)
    assert node.policy() is None and lsf.pending_bitmaps(node.q) == 1


def test_older_drained_bitmap_does_not_replace_a_newer_record(tmp_path):
    path = str(tmp_path / "s.json")
    bp.mark_sent({"camera_water_pct": 30}, "/x/new", now=T0 + 60, path=path)
    bp.mark_sent_queued({"captured_at": T0, "bitmap_policy": {"camera_water_pct": 10, "capture": "old"}},
                        path=path)
    assert bp.load_state(path)["capture"] == "new"


@pytest.mark.parametrize("limit,expected", [(11, None), (35, None), (36, 32), (240, 236), (250, 238)])
def test_packet_budget(limit, expected):
    assert bitmap_fit.packet_budget(limit) == expected
    assert bitmap_fit.queue_budget() == 226
