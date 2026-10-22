#!/usr/bin/env python3
"""LoRa store-and-forward queue.

Persists capture-cycle payloads to disk when the mDot is not joined so they
can be retransmitted (trickle-drain, 2/cycle by default) once the mDot rejoins.

Queue directory: $WATERCAM_REPO/data/lora_pending/
File per cycle:  {captured_at_int}_{counter:06d}.json
Record schema:
    {
        "sensor_hex": "<hex string, or a list of hex strings (the chunks), or null>",
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

A capture's sensor packet is queued as chunks (encode_sensor_chunks in
tools/lora_handler_concurrent.py), a list in sensor_hex; each chunk that goes out
is removed from the list. An entry with a payload larger than the limit now is
skipped, left queued, and the drain goes on to smaller ones.

The nodes' processes (the capture graph, the commands, the IP path) all touch the
queue, so every read-modify-write holds an flock on LOCK_NAME in the queue
directory.

Timestamps are captured at collection time and baked into the stored hex bytes,
so retransmitted packets carry the original capture timestamp, not the retransmit
wall-clock time.
"""

import contextlib
import fcntl
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
LOCK_NAME = ".lock"


@contextlib.contextmanager
def _locked(queue_dir: str):
    """Hold the queue's flock (blocking) around a read-modify-write.  The lock is
    per open file, so it also excludes other threads that open it themselves."""
    try:
        os.makedirs(queue_dir, exist_ok=True)
        fd = os.open(os.path.join(queue_dir, LOCK_NAME), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        logger.warning("LoRa S&F: no lock file (%s): going without", exc)
        yield
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)        # closing releases the flock


def _sensor_list(record: dict) -> list:
    """The record's sensor payloads (hex) as a list, whatever sensor_hex holds."""
    v = record.get("sensor_hex")
    if not v:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


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
        with _locked(queue_dir):
            return _enqueue_locked(sensor_hex, bitmap_hex, captured_at, queue_dir, max_depth,
                                   mask_path, bitmap_policy)
    except OSError as exc:
        logger.warning("LoRa S&F: enqueue failed: %s", exc)
        return False


def _enqueue_locked(sensor_hex, bitmap_hex, captured_at, queue_dir, max_depth, mask_path, bitmap_policy):
    try:
        if bitmap_hex:
            _supersede_locked(captured_at, queue_dir)
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
            if not isinstance(record, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            continue        # removed by another process since the listing
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
                    _update(path, queue_dir, record, [], False)

        # An entry with a payload that doesn't fit waits for a data rate that does;
        # smaller entries behind it still go
        payloads = _sensor_list(record) + ([record["bitmap_hex"]] if record.get("bitmap_hex") else [])
        if size_limit is not None and any(len(h) // 2 > size_limit for h in payloads):
            logger.info("LoRa S&F: %s waits for a payload limit above %d B", fname, size_limit)
            result["deferred"] = True
            continue

        # Every payload must succeed; stop on first failure.  The sensor chunks go
        # first, one at a time, then the bitmap.
        ok = True
        delivered = []
        bitmap_sent = False
        for hex_payload in _sensor_list(record):
            if not handler.transmit(hex_payload):
                logger.warning("LoRa S&F: drain failed on %s", fname)
                ok = False
                break
            delivered.append(hex_payload)
        if ok and record.get("bitmap_hex"):
            if handler.transmit(record["bitmap_hex"]):
                bitmap_sent = True
            else:
                logger.warning("LoRa S&F: drain failed on %s", fname)
                ok = False
        # What went out is cleared from the file (as it is now, not as it was read:
        # another process may have superseded the bitmap meanwhile)
        if delivered or bitmap_sent:
            _update(path, queue_dir, record, delivered, bitmap_sent)
        if bitmap_sent and on_bitmap_sent is not None:
            try:
                on_bitmap_sent(record)
            except Exception as exc:
                logger.warning("LoRa S&F: on_bitmap_sent failed for %s: %s", fname, exc)
        if ok:
            result["drained"] += 1
            sent += 1
        else:
            result["failed"] = True
            break

    return result


def _update(path: str, queue_dir: str, seen: dict, delivered_sensors: list, bitmap_sent: bool) -> None:
    """Clear what went out from the file's record, under the lock (never raises).

    The record is read again, so a bitmap another process superseded meanwhile
    stays gone; `seen` is the drain's copy, whose own changes (a bitmap compressed
    again) are kept when nothing else changed the bitmap.  An entry left with
    nothing to send is removed."""
    try:
        with _locked(queue_dir):
            try:
                with open(path) as fh:
                    record = json.load(fh)
            except (OSError, ValueError):
                return          # removed meanwhile
            if record.get("bitmap_hex") and record.get("bitmap_hex") != seen.get("bitmap_hex") \
                    and record.get("captured_at") == seen.get("captured_at") and not bitmap_sent:
                record["bitmap_hex"] = seen.get("bitmap_hex")   # compressed again
            left = [h for h in _sensor_list(record) if h not in delivered_sensors]
            record["sensor_hex"] = left or None
            if bitmap_sent:
                record["bitmap_hex"] = None
                record.pop("mask_path", None)
                record.pop("bitmap_policy", None)
            if record.get("sensor_hex") or record.get("bitmap_hex"):
                _write_atomic(path + ".tmp", path, record)
            else:
                os.unlink(path)
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
    with _locked(queue_dir):
        return _supersede_locked(captured_at, queue_dir)


def _supersede_locked(captured_at: float, queue_dir: str) -> int:
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
            if _sensor_list(record):
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
    with _locked(queue_dir):
        return _remove_locked(wanted, queue_dir)


def _remove_locked(wanted: set, queue_dir: str) -> int:
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
