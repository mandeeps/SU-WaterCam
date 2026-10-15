#!/usr/bin/env python3
"""Count store-and-forward entries thrown away unsent.

Both queues (tools/lora_store_forward.py and the IP queue in tools/transmit_ip.py)
drop their oldest entry when full, and evict entries older than their age limit.
That loses captures silently, so each drop is counted here and the running total
goes out with the status reports:

    IP uplink / LoRa packet  channel 01 0C  sf_dropped (u32)
    LoRa debug status reply  key "sd"

The file is data/sf_dropped.json: {"lora": n, "ip": n}. It lives in data/, not
/tmp, so the count survives reboots (a unit sleeps most of the time).
"""

import json
import logging
import os
from typing import Dict

logger = logging.getLogger(__name__)

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
DEFAULT_PATH = os.path.join(_REPO_ROOT, "data", "sf_dropped.json")
KINDS = ("lora", "ip")


def path_for(queue_dir: str) -> str:
    """The counter file for a queue directory: beside it, so a test's temporary
    queue gets its own counter and the real one (data/) is never touched."""
    return os.path.join(os.path.dirname(os.path.abspath(queue_dir)), "sf_dropped.json")


def counts(path: str = DEFAULT_PATH) -> Dict[str, int]:
    """Per-queue totals; zeros when the file is missing or unreadable."""
    out = {k: 0 for k in KINDS}
    try:
        with open(path) as fh:
            data = json.load(fh)
        for k in KINDS:
            v = data.get(k, 0)
            if isinstance(v, int) and v > 0:
                out[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    return out


def total(path: str = DEFAULT_PATH) -> int:
    return sum(counts(path).values())


def record(kind: str, n: int = 1, path: str = DEFAULT_PATH) -> None:
    """Add n drops to a queue's total. Never raises: counting must not break queueing."""
    if n <= 0 or kind not in KINDS:
        return
    try:
        data = counts(path)
        data[kind] += n
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("could not record %d dropped %s entries: %s", n, kind, exc)
