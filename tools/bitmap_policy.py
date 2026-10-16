"""When a capture's water-mask bitmap (TLV 08 18) goes out over LoRa.

Each wake takes a couple of captures a minute apart, and a site's mask rarely
changes for long periods, so sending the ~230-byte bitmap for every capture
mostly repeats the last one. The sensor packet already carries the water %
(07 37) and the flood flags (07 17 / 07 27). Over LoRa the bitmap is sent only
when one of these holds (decide()):

- no bitmap has been sent yet (or the state file is missing or unreadable);
- the water % differs from the last bitmap sent by max(1, area_threshold)
  percentage points or more;
- camera_flood_detected or camera_flood_growing is 1, or either changed since
  the last bitmap sent;
- emergency mode is on;
- the last bitmap sent is bitmap_refresh_hours old or more (runtime parameter,
  default 24; 0 sends every bitmap, the old behaviour);
- the water % is unknown (no usable mask), so nothing is dropped silently.

IP uploads send the bitmap every capture regardless.

The last bitmap handed to the LoRa daemon or the store-and-forward queue is
recorded in data/lora_bitmap_state.json:
    {"water_pct": 12, "camera_flood_detected": 0, "camera_flood_growing": 0,
     "time": 1760000000, "capture": "20261015-120000"}
"""
import os
import sys
import time
from typing import Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools import config_io  # noqa: E402

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
DEFAULT_STATE_PATH = os.path.join(_REPO_ROOT, "data", "lora_bitmap_state.json")
DEFAULT_REFRESH_HOURS = 24
FLAGS = ("camera_flood_detected", "camera_flood_growing")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def load_state(path: Optional[str] = None) -> Optional[dict]:
    """The last bitmap sent, or None if there is none or the file is unusable."""
    try:
        state = config_io.read_json(path or DEFAULT_STATE_PATH)
    except Exception:
        return None
    if not isinstance(state, dict) or not _number(state.get("water_pct")) \
            or not _number(state.get("time")):
        return None
    return state


def decide(water_pct, flags: dict, last: Optional[dict], now: float,
           area_threshold=10, refresh_hours=DEFAULT_REFRESH_HOURS,
           emergency: bool = False) -> Tuple[bool, str]:
    """(send, reason) for this capture's LoRa bitmap; see the module docstring.

    flags holds this capture's camera_flood_detected / camera_flood_growing
    (absent when flood detection is off); last is load_state()'s result."""
    if not _number(water_pct):
        return True, "water % unknown"
    if last is None:
        return True, "no bitmap sent before"
    if emergency:
        return True, "emergency mode"
    try:
        refresh = float(refresh_hours)
    except (TypeError, ValueError):
        refresh = DEFAULT_REFRESH_HOURS
    if refresh <= 0:
        return True, "bitmap_refresh_hours is 0"
    for k in FLAGS:
        if flags.get(k):
            return True, f"{k}=1"
        if (flags.get(k) or 0) != (last.get(k) or 0):
            return True, f"{k} changed"
    try:
        step = max(1, float(area_threshold))
    except (TypeError, ValueError):
        step = 10
    change = abs(water_pct - last["water_pct"])
    if change >= step:
        return True, f"water {last['water_pct']}% -> {water_pct}%"
    age = now - last["time"]
    if age < 0:
        return True, "last bitmap time is in the future (clock changed)"
    if age >= refresh * 3600:
        return True, f"last bitmap {age / 3600:.1f} h old (refresh {refresh:g} h)"
    return False, (f"water {water_pct}% within {step:g} points of the last bitmap's "
                   f"{last['water_pct']}%, no flood, last sent {age / 3600:.1f} h ago "
                   f"(refresh {refresh:g} h)")


def mark_sent(data: dict, dirname: str, now: Optional[float] = None,
              path: Optional[str] = None) -> None:
    """Record a bitmap handed to the LoRa daemon or the store-and-forward queue.

    data holds the capture's uplink fields (camera_water_pct and the flags)."""
    water_pct = data.get("camera_water_pct")
    capture = os.path.basename(os.path.normpath(dirname)) if dirname else None
    if not _number(water_pct):
        return    # without a water % there is nothing to compare later captures with
    path = path or DEFAULT_STATE_PATH
    state = {"water_pct": water_pct, "time": int(time.time() if now is None else now),
             "capture": capture}
    state.update({k: 1 if data.get(k) else 0 for k in FLAGS})
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        config_io.write_json(path, state)
    except OSError as e:
        print(f"⚠️ Bitmap policy: couldn't save {path}: {e}")


def lora_decision(data: dict, now: float, get_parameter,
                  state_path: Optional[str] = None) -> Tuple[bool, str]:
    """decide() with this capture's uplink fields and the runtime parameters."""
    return decide(data.get("camera_water_pct"), {k: data.get(k) for k in FLAGS},
                  load_state(state_path), now,
                  area_threshold=get_parameter("area_threshold", 10),
                  refresh_hours=get_parameter("bitmap_refresh_hours", DEFAULT_REFRESH_HOURS),
                  emergency=bool(get_parameter("emergency_mode", False)))
