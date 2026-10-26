#!/home/pi/SU-WaterCam/venv/bin/python
"""Which node software and segmentation model this node runs, for the uplinks.

Releases are CalVer git tags, vYYYY.MM.N (docs/RELEASES.md). The version comes
from `git describe` on the checkout, so a node that is past its release tag or
has local edits can't pass for the release:

    v2026.10.1                    the release itself
    v2026.10.1-3-gabc12345        3 commits past it
    v2026.10.1-dirty              local edits (005's no-shutdown patch, say)
    abc12345                      no release tag reachable

It is sent on three channels (u32 each over IP; LoRa sends the first and last):

    01 09  software_version  (year-2000)<<24 | month<<16 | patch<<8 | flags
    01 0A  software_commit   first 8 hex digits of the commit
    01 0B  model_id          first 8 hex digits of the loaded ONNX file's SHA-256

flags: bit 0 dirty, bit 1 past the tag, bit 2 no release tag (the version
bytes are then 0). The API's app/decoders.py must decode the same layout.

The model is whatever segformer_daemon loaded, which it records in
/run/segformer/model.json; a model file can change with no git change at all.
A capture the daemon segmented with its RGB fallback (a band missing) records
that model in its capture_status.json, and model_id then names it instead: the
id goes with the mask it describes.

    python -m tools.node_version        prints both
"""

import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, Optional

REPO = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
#: Written by tools/segformer_daemon.py when it loads a model.
MODEL_INFO_PATH = "/run/segformer/model.json"
#: git describe once per boot: it doesn't change while the node runs. Keyed by
#: boot_id, so a stale file from an earlier boot is never used.
CACHE_PATH = "/tmp/watercam-version.json"

FLAG_DIRTY = 0x01
FLAG_PAST_TAG = 0x02
FLAG_NO_TAG = 0x04

_DESCRIBE = re.compile(
    r"^v(?P<year>\d{4})\.(?P<month>\d{1,2})\.(?P<patch>\d+)"
    r"(?:-(?P<ahead>\d+)-g(?P<hash>[0-9a-f]+))?(?P<dirty>-dirty)?$")
_BARE = re.compile(r"^(?P<hash>[0-9a-f]{4,40})(?P<dirty>-dirty)?$")


def parse_describe(text: str, commit: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """`git describe --tags --always --dirty` output -> version dict, or None."""
    text = (text or "").strip()
    m = _DESCRIBE.match(text)
    if m:
        ahead = int(m.group("ahead") or 0)
        return {"describe": text, "release": (int(m.group("year")), int(m.group("month")),
                                              int(m.group("patch"))),
                "ahead": ahead, "dirty": bool(m.group("dirty")),
                "commit": (commit or m.group("hash") or "")[:8] or None}
    m = _BARE.match(text)
    if m:
        return {"describe": text, "release": None, "ahead": 0,
                "dirty": bool(m.group("dirty")), "commit": (commit or m.group("hash"))[:8]}
    return None


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                          timeout=10, check=True).stdout.strip()


def software_version(use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """This checkout's version, or None if git can't say. Never raises."""
    if use_cache:
        try:
            with open(CACHE_PATH) as f:
                cached = json.load(f)
            if cached.get("boot_id") == _boot_id():
                return cached["version"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        version = parse_describe(
            _git("describe", "--tags", "--always", "--dirty", "--abbrev=8",
                 "--match", "v[0-9]*.[0-9]*.[0-9]*"),
            commit=_git("rev-parse", "--short=8", "HEAD"))
    except (OSError, subprocess.SubprocessError):
        return None
    if version is not None and use_cache:
        try:
            tmp = f"{CACHE_PATH}.{os.getpid()}"
            with open(tmp, "w") as f:
                json.dump({"boot_id": _boot_id(), "version": version}, f)
            os.replace(tmp, CACHE_PATH)
        except OSError:
            pass    # /run not writable (tests, a dev machine): just don't cache
    return version


def _boot_id() -> Optional[str]:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return None


def encode_version_u32(version: Optional[Dict[str, Any]]) -> Optional[int]:
    """Channel 01 09, or None if there is no version to send."""
    if not version:
        return None
    flags = (FLAG_DIRTY if version.get("dirty") else 0) | \
            (FLAG_PAST_TAG if version.get("ahead") else 0)
    release = version.get("release")
    if not release:
        return FLAG_NO_TAG | flags
    year, month, patch = release
    if not (2000 <= year <= 2255 and 1 <= month <= 12 and 0 <= patch <= 255):
        return FLAG_NO_TAG | flags
    return ((year - 2000) << 24) | (month << 16) | (patch << 8) | flags


def hex8_u32(hex_digits: Optional[str]) -> Optional[int]:
    """First 8 hex digits as a u32 (channels 01 0A and 01 0B), or None."""
    if not hex_digits or len(hex_digits) < 8:
        return None
    try:
        return int(hex_digits[:8], 16)
    except ValueError:
        return None


def model_info(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What segformer_daemon loaded ({"name", "path", "sha256"}), or None."""
    try:
        with open(path or MODEL_INFO_PATH) as f:
            info = json.load(f)
        return info if isinstance(info, dict) and info.get("sha256") else None
    except (OSError, ValueError):
        return None


def capture_model_sha256(dirname: Optional[str]) -> Optional[str]:
    """The model that made this capture's mask, when it was not the loaded one."""
    if not dirname:
        return None
    try:
        from tools import capture_status
        return capture_status.read(dirname).get("segmentation_model_sha256") or None
    except Exception:          # noqa: BLE001 - fall back to the loaded model
        return None


def uplink_fields(dirname: Optional[str] = None) -> Dict[str, int]:
    """software_version, software_commit and model_id for the uplinks; only those
    that can be read. Never raises. With the capture's directory, model_id names
    the model that made its mask (the RGB fallback, say)."""
    out: Dict[str, int] = {}
    try:
        version = software_version()
        model_sha = capture_model_sha256(dirname) or (model_info() or {}).get("sha256")
        for key, value in (("software_version", encode_version_u32(version)),
                           ("software_commit", hex8_u32((version or {}).get("commit"))),
                           ("model_id", hex8_u32(model_sha))):
            if value is not None:
                out[key] = value
    except Exception as exc:   # noqa: BLE001 - the uplink must never fail over this
        print(f"⚠️ node version unavailable: {exc}")
    return out


def short_version() -> Optional[str]:
    """The describe string for status replies, e.g. "v2026.10.1-dirty"."""
    version = software_version()
    return version["describe"] if version else None


def main() -> int:
    version = software_version(use_cache=False)
    model = model_info()
    print(f"software: {version['describe'] if version else 'unknown'}"
          + (f" (commit {version['commit']})" if version and version.get("commit") else ""))
    print(f"model:    {model['name']} sha256 {model['sha256'][:8]}" if model
          else "model:    unknown (segformer_daemon not running?)")
    return 0 if version else 1


if __name__ == "__main__":
    sys.exit(main())
