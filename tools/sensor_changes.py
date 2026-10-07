"""Which sensor fields to send over LoRa: those that changed past the threshold.

Each field is compared with the value last *sent* (not the last reading, so a
slow drift is sent once it adds up). The sent values are kept in a small file,
because the sensor tracker token reaches the LoRa step as a fresh copy every
capture and the Pi powers off between wakes. A field never sent before always
qualifies. Non-numeric fields qualify when they differ.

LoRa uplinks are unconfirmed, so a lost packet would leave the server with an
old value until the field changed again. Every lora_full_send_hours (default
24) all fields are sent regardless, which bounds how stale a value can get.
"""

import json
import os
import time
from typing import Any, Dict, Optional

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
DEFAULT_STATE_PATH = os.path.join(_REPO_ROOT, "data", "lora_last_sent.json")
SKIP = ("timestamp", "error")
LAST_FULL_KEY = "_last_full_send"


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path) as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def full_send_due(hours: float, state_path: Optional[str] = None,
                  now: Optional[float] = None) -> bool:
    """True if all fields should be sent this capture (none sent in the last hours)."""
    last = _load(state_path or DEFAULT_STATE_PATH).get(LAST_FULL_KEY)
    now = time.time() if now is None else now
    return not isinstance(last, (int, float)) or now - last >= hours * 3600


def changed_fields(data: Dict[str, Any], threshold: float = 0.05,
                   state_path: Optional[str] = None,
                   everything: bool = False) -> Dict[str, Dict[str, Any]]:
    """Fields of data that differ from the last sent value by at least threshold
    (relative), or every field if everything (the periodic full send)."""
    last = _load(state_path or DEFAULT_STATE_PATH)
    out = {}
    for name, cur in data.items():
        if name in SKIP:
            continue
        prev = last.get(name)
        if everything:
            out[name] = {"current_value": cur, "previous_value": prev, "change_percent": None,
                         "reason": "periodic_full_send"}
        elif prev is None:
            out[name] = {"current_value": cur, "previous_value": None, "change_percent": 100.0,
                         "reason": "first_reading"}
        elif isinstance(cur, (int, float)) and isinstance(prev, (int, float)) \
                and not isinstance(cur, bool) and not isinstance(prev, bool):
            pct = abs((cur - prev) / prev) if prev != 0 else (1.0 if cur != 0 else 0.0)
            if pct >= threshold:
                out[name] = {"current_value": cur, "previous_value": prev,
                             "change_percent": pct * 100, "reason": "threshold_exceeded"}
        elif cur != prev:
            out[name] = {"current_value": cur, "previous_value": prev, "change_percent": 100.0,
                         "reason": "changed"}
    return out


def mark_sent(fields: Dict[str, Any], state_path: Optional[str] = None,
              full: bool = False, now: Optional[float] = None) -> None:
    """Record these values as sent (or queued to send); full marks a periodic
    full send. Never raises."""
    if not fields:
        return
    state_path = state_path or DEFAULT_STATE_PATH
    state = _load(state_path)
    state.update({k: v for k, v in fields.items() if k not in SKIP and k != LAST_FULL_KEY})
    if full:
        state[LAST_FULL_KEY] = time.time() if now is None else now
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh)
        os.replace(tmp, state_path)
    except (OSError, TypeError, ValueError):
        pass
