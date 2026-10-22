"""Keep the SD card from filling with captures.

A wake writes two captures of about 10 MB each (JPEGs and five-band TIFFs), and
nothing sent them anywhere or removed them, so a card filled in months. Each
wake cycle, run() removes whole capture directories (every file in them: photos,
masks, metadata, audio, thermal frames), and loose JPEG/TIFF files at the top of
images/. Masks and metadata used to be kept, but they filled the card on their
own and are of no use without their photos. Camera-wide files that sit directly
in images/ (registration_transform.json and the like) are never touched.

1. older than data_retention_days (runtime parameter; the default 0 keeps
   everything, while the system is still being developed), judged by the newest
   file in the directory, and
2. if the card's free space is at or below storage_low_free_pct (default 20 %),
   the oldest ones until it's back to storage_target_free_pct (default 30 %).

The capture being written now is never touched. Rule 2 still applies with
retention off, so a full card can't stop the unit: it removes only the oldest
photos, and only when space is short.
"""
import os
import shutil
import time
from typing import Iterable, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES_DIR = os.path.join(REPO, "images")
EXTENSIONS = (".jpg", ".jpeg", ".tif", ".tiff")
DEFAULT_DAYS = 0         # keep everything
DEFAULT_LOW_PCT = 20.0
DEFAULT_TARGET_PCT = 30.0


def _param(name: str, default: float) -> float:
    try:
        from tools.lora_runtime_integration import get_parameter
        return float(get_parameter(name, default))
    except Exception:
        return default


def _photos(root: str, skip: Iterable[str]) -> List[Tuple[float, int, str]]:
    """(mtime, size, path) of every capture directory and loose photo in root, oldest first.

    A directory's mtime is that of its newest file, so a capture still being
    added to (audio, a late mask) is as new as its last write.
    """
    skip = {os.path.realpath(s) for s in skip if s}
    out = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return out
    for entry in entries:
        if os.path.realpath(entry.path) in skip:
            continue
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                newest, size = None, 0
                for dirpath, _, filenames in os.walk(entry.path):
                    for name in filenames:
                        try:
                            st = os.lstat(os.path.join(dirpath, name))
                        except OSError:
                            continue
                        newest, size = max(newest or st.st_mtime, st.st_mtime), size + st.st_size
                out.append((entry.stat().st_mtime if newest is None else newest, size, entry.path))
            elif entry.name.lower().endswith(EXTENSIONS):
                st = entry.stat(follow_symlinks=False)
                out.append((st.st_mtime, st.st_size, entry.path))
        except OSError:
            continue
    out.sort()
    return out


def _remove(path: str) -> None:
    """Delete a capture directory or a loose photo; raises OSError if it can't."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
        if os.path.exists(path):
            raise OSError(f"{path} not fully removed")
    else:
        os.remove(path)


def free_pct(path: str) -> float:
    u = shutil.disk_usage(path)
    return 100.0 * u.free / u.total if u.total else 100.0


def run(current_dir: Optional[str] = None, root: str = IMAGES_DIR, now: Optional[float] = None) -> dict:
    """Apply both rules; returns what was removed. Never raises."""
    summary = {"aged": 0, "for_space": 0, "freed_mb": 0.0, "free_pct": None}
    try:
        if not os.path.isdir(root):
            return summary
        now = now or time.time()
        days = _param('data_retention_days', DEFAULT_DAYS)
        low = _param('storage_low_free_pct', DEFAULT_LOW_PCT)
        target = max(low, _param('storage_target_free_pct', DEFAULT_TARGET_PCT))
        photos = _photos(root, [current_dir])
        freed = 0
        kept = []
        cutoff = now - days * 86400 if days > 0 else None
        for mtime, size, path in photos:
            if cutoff is not None and mtime < cutoff:
                try:
                    _remove(path)
                    summary["aged"] += 1
                    freed += size
                except OSError:
                    kept.append((mtime, size, path))
            else:
                kept.append((mtime, size, path))
        if free_pct(root) <= low:
            for mtime, size, path in kept:          # oldest first
                if free_pct(root) >= target:
                    break
                try:
                    _remove(path)
                    summary["for_space"] += 1
                    freed += size
                except OSError:
                    pass
        summary["freed_mb"] = round(freed / 1e6, 1)
        summary["free_pct"] = round(free_pct(root), 1)
        if summary["aged"] or summary["for_space"]:
            print(f"🗑️ Photo retention: removed {summary['aged']} captures older than {days:g} days and "
                  f"{summary['for_space']} for space ({summary['freed_mb']} MB); "
                  f"{summary['free_pct']} % free")
    except Exception as e:
        print(f"⚠️ Photo retention failed: {e}")
    return summary
