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
        "enqueued_at": <Unix epoch float>,
        "mask_path": "<the capture's mask, or absent>",
        "bitmap_policy": {<the capture's fields for tools/bitmap_policy.py>, or absent}
    }

One bitmap at a time: queuing a bitmap (or sending one live,
supersede_bitmaps()) clears the bitmaps of older entries, which would only
show the dashboard an older scene; an entry left empty is removed. These are
superseded, not lost, so they don't count in sf_dropped (tools/drop_counter.py).
A queued bitmap that is larger than the room when it is drained is compressed
again from mask_path at that room (drain(refit=...), tools/bitmap_fit.py).

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


def _write_atomic(tmp: str, final: str, record: dict) -> None:
    """Write record to tmp, fsync it, rename it to final, fsync the directory."""
    with open(tmp, "w") as fh:
        json.dump(record, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    try:
        dfd = os.open(os.path.dirname(os.path.abspath(final)), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def enqueue(
    sensor_hex: Optional[str],
    bitmap_hex: Optional[str],
    captured_at: float,
    *,
    queue_dir: str = DEFAULT_QUEUE_DIR,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    mask_path: Optional[str] = None,
    bitmap_policy: Optional[dict] = None,
) -> bool:
    """Persist one capture cycle's payloads to the pending queue.

    A bitmap replaces the bitmaps of older entries (supersede_bitmaps()).
    *mask_path* (the capture's mask) lets drain() compress the bitmap again if
    it doesn't fit then; *bitmap_policy* is passed back to drain()'s
    on_bitmap_sent when the bitmap goes out.

    If the queue is already at *max_depth*, the oldest entry is dropped to make
    room.  Returns True on success, False on I/O error (never raises).
    """
    try:
        os.makedirs(queue_dir, exist_ok=True)
        if bitmap_hex:
            supersede_bitmaps(captured_at, queue_dir=queue_dir)
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
        if bitmap_hex and mask_path:
            record["mask_path"] = mask_path
        if bitmap_hex and bitmap_policy:
            record["bitmap_policy"] = bitmap_policy
        n = len(existing)
        while True:
            fname = f"{int(captured_at)}_{n:06d}.json"
            final = os.path.join(queue_dir, fname)
            if not os.path.exists(final):
                break
            n += 1
        tmp = final + ".tmp"
        _write_atomic(tmp, final, record)
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
    refit=None,
    on_bitmap_sent=None,
) -> dict:
    """Transmit up to *max_packets* oldest queued entries via *handler*.

    Both payloads in a record (sensor + bitmap) must transmit successfully
    before the file is deleted; one that went out is cleared from the file, so
    a later retry doesn't send it twice.  Stops on first transmission failure
    and leaves the remainder for the next cycle.

    *size_limit* (the mDot's current payload limit): a payload larger than it
    isn't tried at all (the mDot would refuse it and the gateway gains nothing);
    draining stops there until a cycle with a higher data rate ("deferred"),
    except a bitmap that *refit(record, size_limit)* can make again to fit (a
    packet, bytes or hex, or None): it is replaced by that and sent.

    *on_bitmap_sent(record)* is called after a queued bitmap went out (the
    record still holds its captured_at and bitmap_policy); its errors are
    logged and ignored.

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

        # A bitmap that doesn't fit is compressed again for the room now
        bitmap = record.get("bitmap_hex")
        if (size_limit is not None and refit is not None and bitmap
                and len(bitmap) // 2 > size_limit):
            try:
                packet = refit(record, size_limit)
            except Exception as exc:
                logger.warning("LoRa S&F: refit of %s failed: %s", fname, exc)
                packet = None
            if packet:
                new_hex = packet.hex() if isinstance(packet, (bytes, bytearray)) else str(packet)
                if len(new_hex) // 2 <= size_limit:
                    logger.info("LoRa S&F: %s bitmap %d B -> %d B for a %d B limit",
                                fname, len(bitmap) // 2, len(new_hex) // 2, size_limit)
                    record["bitmap_hex"] = new_hex
                    _rewrite(path, record)

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
            if key == "bitmap_hex" and on_bitmap_sent is not None:
                try:
                    on_bitmap_sent(record)
                except Exception as exc:
                    logger.warning("LoRa S&F: on_bitmap_sent failed for %s: %s", fname, exc)

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
        _write_atomic(path + ".tmp", path, record)
    except OSError as exc:
        logger.warning("LoRa S&F: could not update %s: %s", path, exc)


def supersede_bitmaps(
    captured_at: float,
    *,
    queue_dir: str = DEFAULT_QUEUE_DIR,
) -> int:
    """Clear the bitmaps of entries captured at or before *captured_at*: a newer
    bitmap was queued or sent.  An entry left with nothing to send is removed.
    Not counted as dropped.  Returns the number of bitmaps cleared (never raises)."""
    try:
        entries = sorted(f for f in os.listdir(queue_dir) if f.endswith(".json"))
    except OSError:
        return 0
    cleared = 0
    for fname in entries:
        path = os.path.join(queue_dir, fname)
        try:
            with open(path) as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not record.get("bitmap_hex"):
            continue
        try:
            if float(record.get("captured_at", 0)) > float(captured_at):
                continue
        except (TypeError, ValueError):
            pass
        record["bitmap_hex"] = None
        record.pop("mask_path", None)
        record.pop("bitmap_policy", None)
        try:
            if record.get("sensor_hex"):
                _write_atomic(path + ".tmp", path, record)
            else:
                os.unlink(path)
        except OSError as exc:
            logger.warning("LoRa S&F: could not supersede the bitmap in %s: %s", fname, exc)
            continue
        cleared += 1
        logger.info("LoRa S&F: %s bitmap superseded by a newer one", fname)
    return cleared


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
