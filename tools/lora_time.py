"""Set the clock from LoRa network time (DeviceTimeReq), once per boot.

Units don't run NTP. Their clock comes from the Witty Pi RTC at boot, then
from network time (config/wittypi/syncTime.sh, HTTP Date header, 1 s
resolution) or GPS time, and after an IP upload from the server's reply
(transmit_ip.correct_clock). A unit with only LoRa had only the daily GPS
window. The mDot can ask the network server instead: AT+GPSTIME sends an empty
uplink carrying a DeviceTimeReq and the server answers in a receive window
(measured on 005, 2026-11-10: 1.2 s round trip, the answers within about 15 ms
of each other and about 0.2 s ahead of true time). The answer is a LoRaWAN downlink,
authenticated by the device's session keys, and needs no data plan.

lora_time_sync in runtime_config.json says when to use it:

  healthy  (default) when the LoRa link is healthy (lora_health.assess): set the
           clock from LoRa, ahead of IP time. On a weak link, leave it to IP,
           GPS and the RTC.
  measure  as healthy, and on a weak link still ask, but only log the answer
           (data for choosing between LoRa and GPS time on weak links; each ask
           is one more uplink and downlink).
  always   use LoRa time on a weak link too.
  off      never ask.

Rules, the same as for the server's time (transmit_ip.correct_clock):
- at most once a day (INTERVAL_S; once per boot at most; RETRY_S after an
  unanswered ask on a healthy link), and not after GPS time, which is more exact;
- an offset of LORA_TOLERANCE_S or less is left alone (the clock helper takes
  whole seconds), but counts as verified;
- a step of more than MAX_STEP_S (a day) only when the RTC is known to be bad
  or the clock is before 2026: an answer a day or more off is more likely wrong;
- through the root-owned watercam-set-clock (system clock and Witty Pi RTC).

Every attempt, applied or not, goes into data/time_sync_log.jsonl, as do the
GPS window's fixes (tools/gps_window.py), so the two can be compared on the
same unit (docs/TIME_SYNC.md).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Callable, Dict, Optional

try:
    from tools import config_io
except ImportError:  # run from tools/
    import config_io

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO, "runtime_config.json")
LOG_PATH = os.path.join(REPO, "data", "time_sync_log.jsonl")
LOG_MAX_LINES = 1000
SET_CLOCK = "/usr/local/sbin/watercam-set-clock"
TIME_SOURCE = "/run/watercam-time-source"
RTC_UNTRUSTED = "/run/watercam-rtc-untrusted"
#: This boot's attempt (its boot id), so the daemon asks at most once per boot
DONE_PATH = "/tmp/watercam-lora-time"
#: When it last asked (kept across boots): each ask is an extra uplink and
#: downlink, and the RTC keeps time well between boots (005 drifted 2 s in 4
#: days), so a unit waking every hour asks at most once every INTERVAL_S
STATE_PATH = os.path.join(REPO, "data", "lora_time.json")
INTERVAL_S = 24 * 3600
#: After an ask on a healthy link went unanswered (one lost uplink or downlink),
#: ask again sooner. Not after one on a weak link (measure, always): that would
#: be an extra ask every few hours where answers are rare. A gone gateway fails
#: the link check first, so it costs no asks either way.
RETRY_S = 6 * 3600
CLOCK_FLOOR = 1767225600                 # 2026-01-01, as watercam-set-clock
LORA_TOLERANCE_S = 1.0
MAX_STEP_S = 24 * 3600
MODES = ("healthy", "measure", "always", "off")
#: Joining after a power cut can take a while; the daemon waits this long
JOIN_WAIT_S = 15 * 60


def _boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


#: LoRa time's measured bias (lora_time_bias_s): subtracted from every answer
BIAS_DEFAULT_S = 0.21
BIAS_LIMIT_S = 2.0


def bias_s() -> float:
    """How far LoRa time runs ahead of true time (runtime_config lora_time_bias_s).

    Measured on 005 against NTP (2026-11-10): +0.214 s, 9 ms standard deviation,
    most likely ChirpStack answering with its time after its 200 ms deduplication
    delay. It changes if that delay does or a gateway sends GPS timestamps, so it
    is a setting; out of range or unreadable means the default."""
    try:
        value = float((config_io.read_json(CONFIG) or {}).get("lora_time_bias_s", BIAS_DEFAULT_S))
    except Exception:  # noqa: BLE001
        return BIAS_DEFAULT_S
    return value if abs(value) <= BIAS_LIMIT_S else BIAS_DEFAULT_S


def mode() -> str:
    try:
        value = str((config_io.read_json(CONFIG) or {}).get("lora_time_sync") or "healthy").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable config means the default
        value = "healthy"
    return value if value in MODES else "healthy"


def synced_this_boot() -> bool:
    """True once LoRa time set or confirmed the clock this boot (IP time then steps aside)."""
    try:
        with open(DONE_PATH) as f:
            done = json.load(f)
    except (OSError, ValueError):
        return False
    return done.get("boot") == _boot_id() and bool(done.get("verified"))


def _time_source() -> str:
    try:
        with open(TIME_SOURCE) as f:
            return f.read().strip()
    except OSError:
        return ""


def log_event(event: Dict[str, Any], path: Optional[str] = None) -> None:
    """Append one line to the time sync log, keeping the newest LOG_MAX_LINES."""
    path = path or LOG_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path) as f:
                lines = f.read().splitlines()[-(LOG_MAX_LINES - 1):]
        except OSError:
            lines = []
        lines.append(json.dumps(event, default=str))
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _apply(target: float, run: Callable) -> Optional[str]:
    """Set the clock and RTC through the helper; None if it worked, else why not."""
    if not os.path.exists(SET_CLOCK):
        return "the clock helper (watercam-set-clock) isn't installed"
    try:
        r = run(["doas", SET_CLOCK, str(int(round(target))), "lora"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)
    return None if r.returncode == 0 else ((r.stderr or r.stdout or "").strip() or f"exit {r.returncode}")


def sync(handler, now: Callable[[], float] = time.time, run: Callable = subprocess.run,
         assess: Optional[Callable] = None) -> Dict[str, Any]:
    """One attempt: decide, ask the network, set the clock if it is off. Never raises.

    Returns the logged event: {"source": "lora", "mode", "link", "asked", "ok",
    "offset_s" (network minus this clock, after
    bias_s is subtracted), "bias_s", "rtt_s", "applied", "verified", "reason"}.
    """
    m = mode()
    event: Dict[str, Any] = {"t": int(now()), "source": "lora", "mode": m, "asked": False, "ok": False,
                             "applied": False, "verified": False, "reason": ""}

    def done(reason: str) -> Dict[str, Any]:
        event["reason"] = reason
        log_event(event)
        try:
            with open(DONE_PATH, "w") as f:
                json.dump({"boot": _boot_id(), "verified": event["verified"], "at": event["t"]}, f)
        except OSError:
            pass
        return event

    if m == "off":
        event["reason"] = "lora_time_sync is off"
        return event
    if _time_source() == "gps":
        return done("GPS time set the clock this boot (more exact)")
    if assess is None:
        from tools import lora_health
        assess = lambda: lora_health.assess(handler)  # noqa: E731
    link = assess() or {}
    event["link"] = {k: link.get(k) for k in ("healthy", "margin_db", "gateways", "reason")}
    healthy = bool(link.get("healthy"))
    if not link.get("joined", healthy):
        return done("not joined")
    if not healthy and m == "healthy":
        return done(f"weak link ({link.get('reason') or 'not healthy'}): left to IP, GPS and the RTC")

    event["asked"] = True
    _write_state({"last_asked": event["t"], "answered": False, "healthy": healthy})
    answer = handler.network_time()
    if answer.get("ok"):
        _write_state({"answered": True})
    else:
        event["error"] = answer.get("error")
        return done(f"no network time: {answer.get('error') or 'no answer'}")
    bias = bias_s()
    unix = answer["unix"] - bias                   # the network time, corrected for its bias
    offset = unix - answer["answered_at"]
    event.update(ok=True, offset_s=round(offset, 3), bias_s=bias, rtt_s=answer.get("rtt_s"))
    if not healthy and m == "measure":
        return done(f"measured on a weak link, not applied ({offset:+.2f} s)")
    if abs(offset) <= LORA_TOLERANCE_S:
        event["verified"] = True
        _mark_verified()
        return done(f"clock agrees with LoRa time ({offset:+.2f} s)")
    clock = now()
    if abs(offset) > MAX_STEP_S and not os.path.exists(RTC_UNTRUSTED) and clock >= CLOCK_FLOOR:
        return done(f"LoRa time is {abs(offset) / 3600:.1f} h off the clock: more than a day, not applied "
                    "(the RTC looks valid)")
    why = _apply(unix + (clock - answer["answered_at"]), run)
    if why:
        event["error"] = why
        return done(f"clock {offset:+.1f} s off LoRa time, not corrected: {why}")
    event.update(applied=True, verified=True)
    _mark_verified()
    return done(f"clock was {abs(offset):.1f} s {'behind' if offset > 0 else 'ahead of'} LoRa time; corrected")


def _read_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _write_state(update: Dict[str, Any]) -> None:
    state = {**_read_state(), **update}
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)
    except OSError:
        pass


def due(now: Optional[float] = None) -> bool:
    """True when it last asked INTERVAL_S or longer ago (RETRY_S after an
    unanswered ask on a healthy link), or never, or 'in the future' (the clock
    was wrong then or is now)."""
    now = time.time() if now is None else now
    state = _read_state()
    last = state.get("last_asked")
    wait = RETRY_S if state.get("answered") is False and state.get("healthy") else INTERVAL_S
    return not isinstance(last, (int, float)) or not 0 <= now - last < wait


def _mark_verified() -> None:
    try:
        from tools.capture_time import mark_clock_verified
        mark_clock_verified()
    except Exception:  # noqa: BLE001
        pass


def sync_when_joined(handler, wait_s: float = JOIN_WAIT_S, poll_s: float = 30.0,
                     sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """The daemon's boot-time call: wait for the join (up to wait_s), then sync once."""
    try:
        with open(DONE_PATH) as f:
            if json.load(f).get("boot") == _boot_id():
                return {"reason": "already tried this boot"}
    except (OSError, ValueError):
        pass
    if mode() == "off":
        return {"reason": "lora_time_sync is off"}
    if not due():
        return {"reason": "asked within the last day"}
    end = time.monotonic() + wait_s
    while not handler.is_joined():
        if time.monotonic() >= end:
            return {"reason": "not joined within the wait; no LoRa time this boot"}
        sleep(poll_s)
    return sync(handler)
