"""IP fallback: one IP upload when LoRa hasn't carried a bitmap for too long.

At DR0 the mDot's payload limit is 11 bytes and no bitmap fits, so bitmaps wait
in the LoRa store-and-forward queue (data/lora_pending/) for a data rate with
room. A unit that stays there sends its sensor packets but the server gets no
bitmap. Two things make the IP uplink (ticktalk_main.ip_uplink_transmit) send
anyway, even with ip_upload.enabled false, using the normal transport order
(WiFi first, cellular on demand under the power lock):

- the node's own rule: the last bitmap delivered (over LoRa or IP) is older
  than ip_fallback_after_hours (runtime parameter, default 12; 0 = off) AND LoRa
  is holding bitmaps back (queued or deferred in the store-and-forward queue).
  At most one such upload every ip_fallback_min_interval_hours (default 6),
  whether or not it got through;
- the server's command 17 97 NN: an IP upload in each of the next NN wakes
  (00 cancels), from the dashboard or the API's automatic rule. One upload per
  wake (boot); the count goes down when an upload is delivered. A request older
  than REQUEST_MAX_AGE_S is dropped. The API resends a command until the node
  answers, so the same NN again within REPEAT_WINDOW_S is taken as a resend and
  doesn't restart the count.

"Delivered over LoRa" means the mDot sent the bitmap packet (08 18), recorded
by LoRaHandlerClient.transmit(), including store-and-forward replays; "over IP"
means the server accepted an uplink carrying one. A bitmap the send policy
skips is neither: it isn't queued either, so it doesn't count as held back.

State, data/ip_fallback.json (written atomically under a lock, tools/config_io.py;
the LoRa daemon writes requests, ticktalk the rest):

    {"lora_at": 1760000000, "ip_at": 1760000000, "first_seen": 1759990000,
     "fallback_at": 1760000000,
     "request": {"wakes": 2, "at": 1760000000, "source": "lora", "boot": "..."}}

Cellular data: a wake that uploads over cellular costs about 90 KB (mostly
Tailscale; docs/CELLULAR_DATA.md). Each delivered upload restarts the 12 h, so
the defaults mean at most about two a day (~180 KB) while LoRa can't carry
bitmaps; the 6 h interval only matters when uploads fail.
"""
import os
import time
from typing import Any, Dict, Optional, Tuple

from tools import config_io

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
STATE_FILE = os.path.join(_REPO_ROOT, "data", "ip_fallback.json")

DEFAULT_AFTER_HOURS = 12
DEFAULT_MIN_INTERVAL_HOURS = 6
REPEAT_WINDOW_S = 15 * 60          # the API's wake + resend sequence takes about 6 min
REQUEST_MAX_AGE_S = 7 * 86400
MAX_WAKES = 255
#: The bitmap packet's TLV header (channel 08, type 18), as hex
BITMAP_PREFIX = "0818"


# ── state ────────────────────────────────────────────────────────────────────

def load(path: Optional[str] = None) -> Dict[str, Any]:
    try:
        return config_io.read_json(path or STATE_FILE)
    except (OSError, ValueError):
        return {}


def _update(fn, path: Optional[str] = None) -> Dict[str, Any]:
    """Read, change with fn(state), and write the state under its lock. Never raises."""
    path = path or STATE_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with config_io.locked(path):
            state = load(path)
            fn(state)
            config_io.write_json(path, state, mode=0o664)
            return state
    except OSError as e:
        print(f"⚠️ IP fallback: could not save {path}: {e}")
        return {}


def record_delivery(transport: str, now: Optional[float] = None, path: Optional[str] = None) -> None:
    """A bitmap was delivered over transport ("lora" or "ip")."""
    t = time.time() if now is None else now
    _update(lambda s: s.__setitem__(f"{transport}_at", t), path)


def is_bitmap_packet(content: Any) -> bool:
    """True for a LoRa payload (hex string or bytes) that is a bitmap packet."""
    if isinstance(content, (bytes, bytearray)):
        return content[:2] == b"\x08\x18"
    return isinstance(content, str) and content.strip().lower().startswith(BITMAP_PREFIX)


def request(wakes: int, source: str, now: Optional[float] = None,
            path: Optional[str] = None) -> str:
    """Apply the server's 17 97 NN; returns what was done, for the log."""
    t = time.time() if now is None else now
    wakes = max(0, min(MAX_WAKES, int(wakes)))
    result = []

    def change(state):
        old = state.get("request") or {}
        if wakes == 0:
            state["request"] = {"wakes": 0, "wakes_asked": 0, "at": t, "source": source}
            result.append("cancelled" if old.get("wakes") else "cancel: none pending")
        elif old.get("wakes_asked") == wakes and t - old.get("at", 0) < REPEAT_WINDOW_S:
            result.append(f"repeat of the request {int(t - old.get('at', 0))} s ago: kept "
                          f"({old.get('wakes', 0)} left)")
        else:
            state["request"] = {"wakes": wakes, "wakes_asked": wakes, "at": t, "source": source}
            result.append(f"IP upload in the next {wakes} wake(s)")

    _update(change, path)
    return result[0] if result else "not saved"


def pending_wakes(state: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> int:
    """Uploads still requested by the server (0 if none or expired)."""
    state = load() if state is None else state
    req = state.get("request") or {}
    t = time.time() if now is None else now
    if t - req.get("at", 0) > REQUEST_MAX_AGE_S:
        return 0
    try:
        return max(0, int(req.get("wakes", 0)))
    except (TypeError, ValueError):
        return 0


# ── the decision ─────────────────────────────────────────────────────────────

def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h"


def decide(state: Dict[str, Any], now: float, *, after_hours: float, min_interval_hours: float,
           lora_pending_bitmaps: int, have_bitmap: bool, boot_id: str) -> Tuple[bool, str, str]:
    """Whether this capture goes over IP for the fallback; (upload, source, reason).

    source is "request" (the server's 17 97), "stale" (the node's own rule) or "".
    Pure: the caller supplies the state, the time and the queue.
    """
    wakes = pending_wakes(state, now)
    if wakes:
        req = state.get("request") or {}
        if req.get("boot") and req.get("boot") == boot_id:
            return False, "", f"server request: already uploaded this wake ({wakes} left)"
        return True, "request", f"server asked for an IP upload ({wakes} wake(s) left)"

    if not after_hours or after_hours <= 0:
        return False, "", "off (ip_fallback_after_hours = 0)"
    delivered = [state[k] for k in ("lora_at", "ip_at") if isinstance(state.get(k), (int, float))]
    since = max(delivered) if delivered else state.get("first_seen", now)
    age = now - since
    what = "last bitmap delivered" if delivered else "no bitmap delivered since"
    if age < after_hours * 3600:
        return False, "", f"not needed ({what} {_hours(age)} ago)"
    if lora_pending_bitmaps <= 0:
        return False, "", f"{what} {_hours(age)} ago, but LoRa isn't holding bitmaps back"
    if not have_bitmap:
        return False, "", f"{what} {_hours(age)} ago, but this capture has no bitmap"
    last = state.get("fallback_at")
    if isinstance(last, (int, float)) and now - last < min_interval_hours * 3600:
        return False, "", (f"{what} {_hours(age)} ago, {lora_pending_bitmaps} bitmap(s) held back by LoRa; "
                           f"last fallback {_hours(now - last)} ago (min {min_interval_hours} h)")
    return True, "stale", (f"{what} {_hours(age)} ago (limit {after_hours} h) and "
                           f"{lora_pending_bitmaps} bitmap(s) held back by LoRa")


def plan(have_bitmap: bool, boot_id: str, lora_pending_bitmaps: int,
         after_hours: float = DEFAULT_AFTER_HOURS,
         min_interval_hours: float = DEFAULT_MIN_INTERVAL_HOURS,
         now: Optional[float] = None, path: Optional[str] = None) -> Tuple[bool, str, str]:
    """decide() on the saved state; notes the first evaluation (the "since" for a
    unit that never delivered a bitmap) and, for a "stale" upload, its time (the
    rate limit counts attempts, so a failing link isn't retried every wake)."""
    t = time.time() if now is None else now
    state = load(path)
    if "first_seen" not in state:
        state = _update(lambda s: s.setdefault("first_seen", t), path) or {"first_seen": t}
    upload, source, reason = decide(state, t, after_hours=after_hours,
                                    min_interval_hours=min_interval_hours,
                                    lora_pending_bitmaps=lora_pending_bitmaps,
                                    have_bitmap=have_bitmap, boot_id=boot_id)
    if upload and source == "stale":
        _update(lambda s: s.__setitem__("fallback_at", t), path)
    return upload, source, reason


def uploaded(source: str, boot_id: str, path: Optional[str] = None) -> int:
    """An upload forced by `source` was delivered; returns the request's wakes left."""
    left = []

    def change(state):
        req = state.get("request")
        if source == "request" and isinstance(req, dict) and req.get("boot") != boot_id:
            req["wakes"] = max(0, int(req.get("wakes", 0)) - 1)
            req["boot"] = boot_id       # kept at 0 wakes, so a late resend is still seen as one
        if isinstance(req, dict):
            left.append(int(req.get("wakes", 0)))

    _update(change, path)
    return left[0] if left else 0


def wifi_wanted(after_hours: float = DEFAULT_AFTER_HOURS,
                min_interval_hours: float = DEFAULT_MIN_INTERVAL_HOURS,
                now: Optional[float] = None, path: Optional[str] = None,
                lora_pending_bitmaps: Optional[int] = None) -> bool:
    """At wake start: will this wake probably upload over IP for the fallback?
    tools/wifi_policy.py then leaves WiFi on so it can join before cellular is tried."""
    t = time.time() if now is None else now
    if lora_pending_bitmaps is None:
        from tools import lora_store_forward
        lora_pending_bitmaps = lora_store_forward.pending_bitmaps()
    state = load(path)
    upload, _, _ = decide(state, t, after_hours=after_hours, min_interval_hours=min_interval_hours,
                          lora_pending_bitmaps=lora_pending_bitmaps, have_bitmap=True,
                          boot_id=config_io.current_boot_id() or "")
    return upload
