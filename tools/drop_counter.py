#!/usr/bin/env python3
"""Count store-and-forward entries thrown away unsent.

Both queues (tools/lora_store_forward.py and the IP queue in tools/transmit_ip.py)
drop their oldest entry when full, and evict entries older than their age limit.
That loses captures silently, so each drop is counted here and the running total
goes out with the status reports:

    IP uplink / LoRa packet  channel 01 0C  sf_dropped (u32)
    LoRa debug status reply  key "sd"

The file is data/sf_dropped.json: {"lora": n, "ip": n, ...}. It lives in data/,
not /tmp, so the count survives reboots (a unit sleeps most of the time).

The same file holds event counters that are not drops (EVENTS), such as
"lora_daemon_unavailable": cycles whose LoRa packets went into the queue because
the LoRa daemon was down. They are not in total() / sf_dropped; the LoRa debug
status reply carries them (key "du").
"""

import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
DEFAULT_PATH = os.path.join(_REPO_ROOT, "data", "sf_dropped.json")
KINDS = ("lora", "ip")
EVENTS = ("lora_daemon_unavailable",)


def path_for(queue_dir: str) -> str:
    """The counter file for a queue directory: beside it, so a test's temporary
    queue gets its own counter and the real one (data/) is never touched."""
    return os.path.join(os.path.dirname(os.path.abspath(queue_dir)), "sf_dropped.json")


def _load(path: Optional[str], keys) -> Dict[str, int]:
    path = path or DEFAULT_PATH   # read at call time, so tests can point it elsewhere
    out = {k: 0 for k in keys}
    try:
        with open(path) as fh:
            data = json.load(fh)
        for k in keys:
            v = data.get(k, 0)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                out[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    return out


def counts(path: Optional[str] = None) -> Dict[str, int]:
    """Per-queue totals; zeros when the file is missing or unreadable."""
    return _load(path, KINDS)


def events(path: Optional[str] = None) -> Dict[str, int]:
    """Event counters (EVENTS); zeros when the file is missing or unreadable."""
    return _load(path, EVENTS)


def _add(key: str, n: int, path: Optional[str]) -> None:
    path = path or DEFAULT_PATH
    data = _load(path, KINDS + EVENTS)
    data[key] += n
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def total(path: Optional[str] = None) -> int:
    return sum(counts(path).values())


def record(kind: str, n: int = 1, path: Optional[str] = None) -> None:
    """Add n drops to a queue's total. Never raises: counting must not break queueing."""
    if n <= 0 or kind not in KINDS:
        return
    try:
        _add(kind, n, path)
    except OSError as exc:
        logger.warning("could not record %d dropped %s entries: %s", n, kind, exc)


def record_event(name: str, n: int = 1, path: Optional[str] = None) -> None:
    """Add n to an event counter (EVENTS). Never raises."""
    if n <= 0 or name not in EVENTS:
        return
    try:
        _add(name, n, path)
    except OSError as exc:
        logger.warning("could not record %s: %s", name, exc)
