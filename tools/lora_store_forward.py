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

from tools import drop_counter

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
                drop_counter.record("lora", path=drop_counter.path_for(queue_dir))
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
    size_limit: Optional[int] = None,
) -> dict:
    """Transmit up to *max_packets* oldest queued entries via *handler*.

    Both payloads in a record (sensor + bitmap) must transmit successfully
    before the file is deleted; one that went out is cleared from the file, so
    a later retry doesn't send it twice.  Stops on first transmission failure
    and leaves the remainder for the next cycle.

    *size_limit* (the mDot's current payload limit): a payload larger than it
    isn't tried at all (the mDot would refuse it and the gateway gains nothing);
    draining stops there until a cycle with a higher data rate ("deferred").

    Returns {"drained": int, "failed": bool, "deferred": bool}.
    """
    result = {"drained": 0, "failed": False, "deferred": False}
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
                drop_counter.record("lora", path=drop_counter.path_for(queue_dir))
            except OSError:
                pass
            continue

        if record.get("enqueued_at", 0) < cutoff:
            logger.info("LoRa S&F: evicting stale entry %s", fname)
            try:
                os.unlink(path)
                drop_counter.record("lora", path=drop_counter.path_for(queue_dir))
            except OSError:
                pass
            continue

        # Oldest first: wait for a data rate that fits rather than skipping ahead
        if size_limit is not None and any(
                record.get(k) and len(record[k]) // 2 > size_limit for k in ("sensor_hex", "bitmap_hex")):
            logger.info("LoRa S&F: %s waits for a payload limit above %d B", fname, size_limit)
            result["deferred"] = True
            break

        # Both payloads must succeed; stop on first failure.
        ok = True
        for key in ("sensor_hex", "bitmap_hex"):
            hex_payload = record.get(key)
            if not hex_payload:
                continue
            if not handler.transmit(hex_payload):
                logger.warning("LoRa S&F: drain failed on %s", fname)
                ok = False
                break
            record[key] = None
            if any(record.get(k) for k in ("sensor_hex", "bitmap_hex")):
                _rewrite(path, record)

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


def _rewrite(path: str, record: dict) -> None:
    """Save a record with its delivered payload cleared (atomic; never raises)."""
    try:
        with open(path + ".tmp", "w") as fh:
            json.dump(record, fh)
        os.replace(path + ".tmp", path)
    except OSError as exc:
        logger.warning("LoRa S&F: could not update %s: %s", path, exc)


def remove_delivered(
    delivered_ts,
    *,
    queue_dir: str = DEFAULT_QUEUE_DIR,
) -> int:
    """Drop entries whose capture was already delivered over IP.

    Entries are named by their capture time (tools/capture_time.py), which is
    also the IP reading's device_ts, so a match is exact.  Returns the number
    removed (never raises).
    """
    wanted = {int(ts) for ts in delivered_ts}
    try:
        entries = sorted(f for f in os.listdir(queue_dir) if f.endswith(".json"))
    except FileNotFoundError:
        return 0
    removed = 0
    for fname in entries:
        try:
            captured = int(fname.split("_", 1)[0])
        except ValueError:
            continue
        if captured not in wanted:
            continue
        try:
            os.unlink(os.path.join(queue_dir, fname))
            removed += 1
            logger.info("LoRa S&F: %s already delivered over IP, removed", fname)
        except OSError:
            pass
    return removed


def queue_depth(queue_dir: str = DEFAULT_QUEUE_DIR) -> int:
    """Return the number of pending queue files."""
    try:
        return sum(1 for f in os.listdir(queue_dir) if f.endswith(".json"))
    except FileNotFoundError:
        return 0


def pending_bitmaps(queue_dir: str = DEFAULT_QUEUE_DIR) -> int:
    """Queued entries still holding a bitmap: LoRa is holding them back (tools/ip_fallback.py)."""
    try:
        entries = [f for f in os.listdir(queue_dir) if f.endswith(".json")]
    except FileNotFoundError:
        return 0
    n = 0
    for fname in entries:
        try:
            with open(os.path.join(queue_dir, fname)) as fh:
                if json.load(fh).get("bitmap_hex"):
                    n += 1
        except (OSError, ValueError, AttributeError):
            continue
    return n
