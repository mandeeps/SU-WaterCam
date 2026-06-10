#!/usr/bin/env python3
"""LoRa store-and-forward queue.

Persists capture-cycle payloads to disk when the mDot is not joined so they
can be retransmitted (trickle-drain, 2/cycle by default) once the mDot rejoins.

Queue directory: $WATERCAM_REPO/data/lora_pending/
File per cycle:  {captured_at_int}_{counter:06d}.json
Record schema:
    {
        "sensor_hex": "<hex string or null>",
        "bitmap_hex": "<hex string or null>",
        "captured_at": <Unix epoch float>,
        "enqueued_at": <Unix epoch float>
    }

Timestamps are captured at collection time and baked into the stored hex bytes,
so retransmitted packets carry the original capture timestamp, not the retransmit
wall-clock time.
"""

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
DEFAULT_QUEUE_DIR = os.path.join(_REPO_ROOT, "data", "lora_pending")
DEFAULT_MAX_DEPTH = 96
DEFAULT_MAX_AGE_DAYS = 7


def enqueue(
    sensor_hex: Optional[str],
    bitmap_hex: Optional[str],
    captured_at: float,
    *,
    queue_dir: str = DEFAULT_QUEUE_DIR,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> bool:
    """Persist one capture cycle's payloads to the pending queue.

    If the queue is already at *max_depth*, the oldest entry is dropped to make
    room.  Returns True on success, False on I/O error (never raises).
    """
    try:
        os.makedirs(queue_dir, exist_ok=True)
        existing = sorted(f for f in os.listdir(queue_dir) if f.endswith(".json"))
        while len(existing) >= max_depth:
            try:
                os.unlink(os.path.join(queue_dir, existing.pop(0)))
            except OSError:
                pass
        record = {
            "sensor_hex": sensor_hex,
            "bitmap_hex": bitmap_hex,
            "captured_at": captured_at,
            "enqueued_at": time.time(),
        }
        fname = f"{int(captured_at)}_{len(existing):06d}.json"
        tmp = os.path.join(queue_dir, fname + ".tmp")
        final = os.path.join(queue_dir, fname)
        with open(tmp, "w") as fh:
            json.dump(record, fh)
        os.replace(tmp, final)
        logger.info("LoRa S&F: queued %s", fname)
        return True
    except OSError as exc:
        logger.warning("LoRa S&F: enqueue failed: %s", exc)
        return False


def drain(
    handler,
    max_packets: int = 2,
    *,
    queue_dir: str = DEFAULT_QUEUE_DIR,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> dict:
    """Transmit up to *max_packets* oldest queued entries via *handler*.

    Both payloads in a record (sensor + bitmap) must transmit successfully
    before the file is deleted.  Stops on first transmission failure and
    leaves the remainder for the next cycle.

    Returns {"drained": int, "failed": bool}.
    """
    result = {"drained": 0, "failed": False}
    try:
        entries = sorted(f for f in os.listdir(queue_dir) if f.endswith(".json"))
    except FileNotFoundError:
        return result

    cutoff = time.time() - max_age_days * 86400
    sent = 0

    for fname in entries:
        if sent >= max_packets:
            break
        path = os.path.join(queue_dir, fname)
        try:
            with open(path) as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            logger.warning("LoRa S&F: dropping corrupt file %s", fname)
            try:
                os.unlink(path)
            except OSError:
                pass
            continue

        if record.get("enqueued_at", 0) < cutoff:
            logger.info("LoRa S&F: evicting stale entry %s", fname)
            try:
                os.unlink(path)
            except OSError:
                pass
            continue

        # Both payloads must succeed; stop on first failure.
        ok = True
        for hex_payload in (record.get("sensor_hex"), record.get("bitmap_hex")):
            if not hex_payload:
                continue
            if not handler.transmit(hex_payload):
                logger.warning("LoRa S&F: drain failed on %s", fname)
                ok = False
                break

        if ok:
            try:
                os.unlink(path)
            except OSError:
                pass
            result["drained"] += 1
            sent += 1
        else:
            result["failed"] = True
            break

    return result


def queue_depth(queue_dir: str = DEFAULT_QUEUE_DIR) -> int:
    """Return the number of pending queue files."""
    try:
        return sum(1 for f in os.listdir(queue_dir) if f.endswith(".json"))
    except FileNotFoundError:
        return 0
