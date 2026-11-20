"""LoRa network time (DeviceTimeReq), asked once per boot for syncTime.sh to apply.

Units don't run NTP. Their clock comes from the Witty Pi RTC at boot, then from
config/wittypi/syncTime.sh, and after an IP upload from the server's reply
(transmit_ip.correct_clock). The mDot can ask the network server for the time:
AT+GPSTIME sends an empty uplink carrying a DeviceTimeReq and the server answers
in a receive window (measured on 005, 2026-11-10: 1.2 s round trip, the answers
within about 15 ms of each other and about 0.2 s ahead of true time). The answer
is a LoRaWAN downlink, authenticated by the device's session keys, and needs no
data plan. A weak link makes answers rarer, not less accurate.

This module only asks. It writes the answer to ANSWER_PATH: the network's time
when it arrived (bias_s subtracted) and the time since boot at that moment.
syncTime.sh, which runs as root, applies it with config/wittypi/lora_answer.py,
first if it comes first, and network time replaces it only if the two disagree
(docs/TIME_SYNC.md has the order). So lora_daemon, which runs sandboxed with no
privileges, never sets the clock.

lora_time_sync in runtime_config.json: on (default) or off. The old values
healthy, measure and always mean on.

Rules:
- once per boot and at most once a day (INTERVAL_S): the RTC keeps time well
  between boots. An answer that came too late to be used (syncTime.sh had
  finished with no source) is asked for again after RETRY_S; an unanswered ask
  waits the full day, as answers are rare where asks go unanswered;
- not when syncTime.sh has already finished, since nothing would apply it.

Every attempt, used or not, goes into data/time_sync_log.jsonl with the clock's
offset from LoRa time, as do the GPS window's fixes (tools/gps_window.py), so the
sources can be compared on the same unit.
"""
from __future__ import annotations

import json
import os
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
TIME_SOURCE = "/run/watercam-time-source"
#: This boot's attempt, read by config/wittypi/lora_answer.py (as root):
#: {"boot", "status": asking | answer | none, "unix", "boottime", ...}
ANSWER_PATH = "/tmp/watercam-lora-answer.json"
#: Written by syncTime.sh when it finishes
TIME_SYNC_DONE = "/run/watercam-time-sync-done"
#: When it last asked (kept across boots): each ask is an extra uplink and
#: downlink, and the RTC keeps time well between boots (005 drifted 2 s in 4
#: days), so a unit waking every hour asks at most once every INTERVAL_S
STATE_PATH = os.path.join(REPO, "data", "lora_time.json")
INTERVAL_S = 24 * 3600
#: After an answer came too late to be used, ask again sooner
RETRY_S = 6 * 3600
#: Joining after a power cut can take a while; the daemon waits this long
JOIN_WAIT_S = 15 * 60
#: How long to wait for syncTime.sh to finish after an answer (its waits, plus a GPS try)
OUTCOME_WAIT_S = 300


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
        value = str((config_io.read_json(CONFIG) or {}).get("lora_time_sync") or "on").strip().lower()
    except Exception:  # noqa: BLE001 - an unreadable config means the default
        value = "on"
    return "off" if value == "off" else "on"


def _time_source() -> str:
    try:
        with open(TIME_SOURCE) as f:
            return f.read().strip()
    except OSError:
        return ""


def _boottime() -> float:
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def _write_answer(status: str, **fields: Any) -> None:
    """Replace ANSWER_PATH in one step, so lora_answer.py never reads half a file."""
    try:
        tmp = f"{ANSWER_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump({"boot": _boot_id(), "status": status, **fields}, f)
        os.replace(tmp, ANSWER_PATH)
    except OSError:
        pass


def _this_boots_status() -> Optional[str]:
    try:
        with open(ANSWER_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data.get("status") if isinstance(data, dict) and data.get("boot") == _boot_id() else None


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


def sync(handler, now: Callable[[], float] = time.time, boottime: Callable[[], float] = _boottime,
         sleep: Callable[[float], None] = time.sleep, outcome_wait_s: float = OUTCOME_WAIT_S) -> Dict[str, Any]:
    """One attempt: ask the network, hand the answer to syncTime.sh, log what became
    of it. Never raises.

    Returns the logged event: {"source": "lora", "asked", "ok", "offset_s" (network
    minus this clock, after bias_s is subtracted), "bias_s", "rtt_s", "applied"
    (syncTime.sh set the clock from it), "clock_from" (what the clock came from in
    the end: lora, network, gps, server or rtc), "reason"}.
    """
    event: Dict[str, Any] = {"t": int(now()), "source": "lora", "asked": True, "ok": False,
                             "applied": False, "reason": ""}

    def done(reason: str) -> Dict[str, Any]:
        event["reason"] = reason
        log_event(event)
        return event

    _write_state({"last_asked": event["t"], "answered": False, "used": False})
    answer = handler.network_time()
    if not answer.get("ok"):
        _write_answer("none")
        event["error"] = answer.get("error")
        return done(f"no network time: {answer.get('error') or 'no answer'}")
    # The time since boot when it arrived: unaffected by anything that steps the clock later
    bt, clock = boottime(), now()
    bias = bias_s()
    unix = answer["unix"] - bias                   # the network time, corrected for its bias
    offset = unix - answer["answered_at"]
    _write_answer("answer", unix=unix + (clock - answer["answered_at"]), boottime=bt,
                  offset_s=round(offset, 3), rtt_s=answer.get("rtt_s"))
    _write_state({"answered": True})
    event.update(ok=True, offset_s=round(offset, 3), bias_s=bias, rtt_s=answer.get("rtt_s"))

    # syncTime.sh applies it (as root), and network time may then replace it; wait to see
    end = time.monotonic() + outcome_wait_s
    while not os.path.exists(TIME_SYNC_DONE) and time.monotonic() < end:
        sleep(1.0)
    source = _time_source()
    event["clock_from"] = source or "rtc"
    if source == "lora":
        event["applied"] = True
        _write_state({"used": True})
        return done(f"clock set from LoRa time (it was {offset:+.2f} s off)")
    if source:
        _write_state({"used": True})               # the clock is right; asking again today gains nothing
        return done(f"clock set from {source} time; LoRa time {offset:+.2f} s from the clock before")
    return done(f"answered too late to be used ({offset:+.2f} s); asking again in {RETRY_S // 3600} h")


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
    """True when it last asked INTERVAL_S or longer ago (RETRY_S after an answer
    that came too late to be used), or never, or 'in the future' (the clock was
    wrong then or is now)."""
    now = time.time() if now is None else now
    state = _read_state()
    last = state.get("last_asked")
    wait = RETRY_S if state.get("answered") and state.get("used") is False else INTERVAL_S
    return not isinstance(last, (int, float)) or not 0 <= now - last < wait


def sync_when_joined(handler, wait_s: float = JOIN_WAIT_S, poll_s: float = 10.0,
                     sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """The daemon's boot-time call: wait for the join (up to wait_s), then ask once.

    Says "asking" in ANSWER_PATH while it waits, so syncTime.sh waits for the
    answer when it has nothing better, and "none" when no answer is coming."""
    if _this_boots_status() is not None:
        return {"reason": "already tried this boot"}
    why = None
    if mode() == "off":
        why = "lora_time_sync is off"
    elif not due():
        why = "asked within the last day"
    elif os.path.exists(TIME_SYNC_DONE):
        why = "syncTime.sh has finished: too late to be used"
    if why:
        _write_answer("none", reason=why)
        return {"reason": why}
    _write_answer("asking")
    end = time.monotonic() + wait_s
    while not handler.is_joined():
        if os.path.exists(TIME_SYNC_DONE):
            why = "syncTime.sh finished before the join: too late to be used"
        elif time.monotonic() >= end:
            why = "not joined within the wait; no LoRa time this boot"
        if why:
            _write_answer("none", reason=why)
            return {"reason": why}
        sleep(poll_s)
    return sync(handler)
