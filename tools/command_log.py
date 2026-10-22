"""Server commands this node has applied, and its acks still owed over IP.

A server command is one frame, the same over LoRa and IP (docs/SYSTEM_REFERENCE.md,
"Command frames"):

    [seq][code HH LL][value]...

The server keeps a command until the node acks it: it resends over LoRa and
offers it again in every IP reply. So the node applies a frame once and acks
every copy. Applied frames are remembered by (seq, hash of the frame) for
KEEP_S, the last MAX_ENTRIES of them; seq 0 is a frame typed by hand, applied
every time and never remembered.

Acks for commands that came over IP go to POST /ip/ack at once; the ones that
couldn't be posted are kept here and sent with the next IP uplink
(tools/transmit_ip.py).

Both files are written by the LoRa daemon and by ticktalk, so every change is
a locked read-modify-write (tools/config_io.py).
"""
import hashlib
import os
import time
from typing import Any, Dict, Iterable, List, Optional

from tools import config_io

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
APPLIED_FILE = os.path.join(_REPO_ROOT, "data", "applied_commands.json")
PENDING_ACKS_FILE = os.path.join(_REPO_ROOT, "data", "pending_acks.json")

MAX_ENTRIES = 64
KEEP_S = 7 * 86400
MAX_PENDING_ACKS = 64

#: Ack results, as the server reads them
APPLIED, PARTLY, REJECTED = 0, 1, 2


def frame_key(frame: bytes) -> str:
    return hashlib.sha1(bytes(frame)).hexdigest()[:8]


def _load(path: str) -> Dict[str, Any]:
    try:
        data = config_io.read_json(path)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(path: str, fn) -> Dict[str, Any]:
    """Read, change with fn(state), write, under the file's lock. Never raises."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with config_io.locked(path):
            state = _load(path)
            fn(state)
            config_io.write_json(path, state, mode=0o664)
            return state
    except (OSError, ValueError, config_io.ConfigLockTimeout) as e:
        print(f"⚠️ {os.path.basename(path)} not updated: {e}")
        return {}


def seen(frame: bytes, now: Optional[float] = None, path: Optional[str] = None) -> Optional[int]:
    """The result this frame got when it was applied, if it was in the last KEEP_S; else None."""
    if not frame or frame[0] == 0:
        return None
    t = time.time() if now is None else now
    key = frame_key(frame)
    for e in _load(path or APPLIED_FILE).get("applied", []):
        if (isinstance(e, dict) and e.get("seq") == frame[0] and e.get("key") == key
                and t - float(e.get("at", 0)) <= KEEP_S):
            return int(e.get("result", APPLIED))
    return None


def record(frame: bytes, result: int, now: Optional[float] = None, path: Optional[str] = None) -> None:
    """Remember that this frame was applied with `result`."""
    if not frame or frame[0] == 0:
        return
    t = time.time() if now is None else now
    entry = {"seq": frame[0], "key": frame_key(frame), "result": int(result), "at": t}

    def add(state):
        kept = [e for e in state.get("applied", [])
                if isinstance(e, dict) and t - float(e.get("at", 0)) <= KEEP_S
                and not (e.get("seq") == entry["seq"] and e.get("key") == entry["key"])]
        state["applied"] = (kept + [entry])[-MAX_ENTRIES:]
    _update(path or APPLIED_FILE, add)


def add_pending_acks(acks: Iterable[Dict[str, Any]], path: Optional[str] = None) -> None:
    """Keep acks that couldn't be posted, for the next IP uplink."""
    new = [{"id": int(a["id"]), "result": int(a.get("result", APPLIED))}
           for a in acks if isinstance(a, dict) and "id" in a]
    if not new:
        return

    def add(state):
        ids = {a["id"] for a in new}
        old = [a for a in state.get("acks", []) if isinstance(a, dict) and a.get("id") not in ids]
        state["acks"] = (old + new)[-MAX_PENDING_ACKS:]
    _update(path or PENDING_ACKS_FILE, add)


def pending_acks(path: Optional[str] = None) -> List[Dict[str, Any]]:
    return [a for a in _load(path or PENDING_ACKS_FILE).get("acks", []) if isinstance(a, dict)]


def clear_pending_acks(ids: Iterable[int], path: Optional[str] = None) -> None:
    """Forget acks the server has taken."""
    done = set(ids)
    if not done:
        return

    def drop(state):
        state["acks"] = [a for a in state.get("acks", []) if isinstance(a, dict) and a.get("id") not in done]
    _update(path or PENDING_ACKS_FILE, drop)
