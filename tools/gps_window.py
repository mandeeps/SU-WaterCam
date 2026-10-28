#!/home/pi/SU-WaterCam/venv/bin/python
"""A live GPS fix when one is due, with USB on only for as long as it takes.

The modem (and its GPS) is on USB, which stays off otherwise (tools/usb_power.py).
A fixed camera doesn't need a fix every wake: the last one is cached
(tools/get_gps.py) and used, marked as cached, in packets and photos. A live fix
is taken at a wake when:

- the last one is gps_fix_interval_h old or more (runtime parameter, default 24 h),
  or there is none at all, and no window was tried in the last interval: one
  try a day, so a unit that can't see the sky doesn't spend every wake on it.
  Both are counted ATTEMPT_SLACK_H (15 min) short, so the daily try lands on
  the first capture of the same wake each day rather than drifting to its second;
- the pose monitor saw a knock or removal (tools/pose_monitor.py): that wake,
  and after a removal the next two as well, whenever the last try was;
- the server asked for one (downlink 19 99), likewise.

start_if_due() runs the window on a thread, once per boot, so the captures go
on meanwhile: powering USB brings the modem up and quectel-gnss.service starts
its GNSS (40-50 s to a fix with a clear sky). The window lasts up to
FIX_TIMEOUT_S, but never past the wake's awake time (the schedule's `awake`,
less SHUTDOWN_MARGIN_S), and call_shutdown() waits for it (wait_until_done),
so a window opened late in a wake isn't cut short. The fix is saved, USB is
given back (it goes off unless a cellular upload or recording holds it), and if
nothing set the clock this boot (no network time), the fix's time sets it.
"""
import json
import os
import subprocess
import sys
import threading
import time
from typing import List, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tools import config_io   # noqa: E402

DEFAULT_INTERVAL_H = 24
#: The most a window lasts: the whole of a 5-minute wake
FIX_TIMEOUT_S = 300
#: Too short to be worth a try (the modem alone takes 15 s): the next wake tries instead
MIN_WINDOW_S = 60
#: Left at the end of the awake time for the last capture's uplink and the shutdown
SHUTDOWN_MARGIN_S = 30
#: Awake minutes assumed when no schedule is installed
DEFAULT_AWAKE_MIN = 5
#: The daily try comes due this much short of the interval: enough for the minute between
#: a wake's captures and boot jitter, well under the gap to the wake before (an hour at
#: hourly wakes), so the try stays on the same wake each day and doesn't creep earlier
ATTEMPT_SLACK_H = 0.25
POLL_S = 3
REQUEST_FILE = os.path.join(_REPO, "data", "gps_request.json")
#: When the last window started (the once-a-day rule)
ATTEMPT_FILE = os.path.join(_REPO, "data", "gps_window_attempt.json")
TIME_SOURCE = "/run/watercam-time-source"
SET_CLOCK = "/usr/local/sbin/watercam-set-clock"
#: Once per boot: a second start_if_due() in the same boot (a knock seen later) runs only if this is absent.
#: It also holds the window's deadline and, once it ends, done: wait_until_done() reads it,
#: from whichever process calls shutdown.
BOOT_MARK = "/tmp/watercam-gps-window"

_thread: Optional[threading.Thread] = None


def last_live_fix_age_s(now: Optional[float] = None) -> Optional[float]:
    from tools import get_gps
    c = get_gps._read_cache()
    if not c or not c.get("fix_time"):
        return None
    return (now or time.time()) - float(c["fix_time"])


def _interval_h() -> float:
    try:
        from tools.lora_runtime_integration import get_parameter
        return float(get_parameter("gps_fix_interval_h", DEFAULT_INTERVAL_H))
    except Exception:
        return DEFAULT_INTERVAL_H


def request() -> None:
    """Downlink 19 99: a live fix at the next wake (or now, if awake)."""
    os.makedirs(os.path.dirname(REQUEST_FILE), exist_ok=True)
    config_io.write_json(REQUEST_FILE, {"requested_at": time.time()}, mode=0o664)


def _take_request() -> bool:
    try:
        os.unlink(REQUEST_FILE)
        return True
    except OSError:
        return False


def last_attempt(path: Optional[str] = None) -> Optional[float]:
    """When the last window started (epoch), or None."""
    try:
        with open(path or ATTEMPT_FILE) as f:
            return float(json.load(f)["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _record_attempt(now: float, path: Optional[str] = None) -> None:
    try:
        path = path or ATTEMPT_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        config_io.write_json(path, {"at": now}, mode=0o664)
    except (OSError, TypeError, ValueError):
        pass


def reasons(now: Optional[float] = None, requested: bool = False, pose_wake: bool = False) -> List[str]:
    """Why a fix is due now (empty: not due). Pure apart from reading the cache and the last try."""
    now = time.time() if now is None else now
    out = []
    due_s = max(0.0, _interval_h() - ATTEMPT_SLACK_H) * 3600
    tried = last_attempt()
    if tried is None or not 0 <= now - tried < due_s:     # a try in the future: the clock was wrong
        age = last_live_fix_age_s(now)
        if age is None:
            out.append("no fix yet")
        elif age >= due_s:
            out.append(f"last fix {age / 3600:.0f} h ago")
    if pose_wake:
        out.append("knocked or removed")
    if requested:
        out.append("requested by the server")
    return out


def _uptime_s() -> Optional[float]:
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def window_s(uptime_s: Optional[float] = None, awake_min: Optional[float] = None) -> float:
    """How long a window started now may last: FIX_TIMEOUT_S, but not past the
    wake's awake time (less SHUTDOWN_MARGIN_S), when the Witty Pi cuts power."""
    if awake_min is None:
        try:
            from tools import schedule_control
            s = schedule_control.current()
            awake_min = s.awake if s else DEFAULT_AWAKE_MIN
        except Exception:
            awake_min = DEFAULT_AWAKE_MIN
    up = (_uptime_s() if uptime_s is None else uptime_s) or 0
    if up >= float(awake_min) * 60:
        # Up past its awake time already (always on, held, a remote session):
        # nothing cuts power at the end of the slot
        return float(FIX_TIMEOUT_S)
    left = float(awake_min) * 60 - SHUTDOWN_MARGIN_S - up
    return max(0.0, min(float(FIX_TIMEOUT_S), left))


def _mark(update: dict) -> None:
    """Update this boot's window mark (written by start_if_due)."""
    try:
        try:
            with open(BOOT_MARK) as f:
                state = json.load(f)
        except ValueError:
            state = {}
        state.update(update)
        with open(BOOT_MARK, "w") as f:
            f.write(json.dumps(state))
    except OSError:
        pass


def wait_until_done(poll_s: float = 2.0, now=time.time, sleep=time.sleep) -> Optional[str]:
    """Before shutdown: wait for a window this boot to end, at most to its
    deadline (+10 s). Returns what it waited for, or None if nothing was running."""
    try:
        with open(BOOT_MARK) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    if state.get("done") or not isinstance(state.get("deadline"), (int, float)):
        return None
    limit = state["deadline"] + 10
    if now() >= limit:
        return None
    print(f"🛰️ Waiting up to {limit - now():.0f} s for the GPS window before shutting down")
    while now() < limit:
        sleep(poll_s)
        try:
            with open(BOOT_MARK) as f:
                if json.load(f).get("done"):
                    return "window ended"
        except (OSError, ValueError):
            return "window mark gone"
    return "deadline passed"


def _wait_for_fix(timeout_s: float) -> Optional[dict]:
    from tools import get_gps
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        loc, packet = get_gps.get_location_with_retry(max_retries=1, use_cache=False)
        if loc and packet is not None:
            return {"location": loc, "packet": packet}
        time.sleep(POLL_S)
    return None


def _set_clock_from(packet) -> None:
    """Set the clock from the fix if nothing set it this boot (no network time)."""
    if os.path.exists(TIME_SOURCE):
        return
    try:
        from datetime import datetime
        t = getattr(packet, "time", None)
        ts = datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp() if t else None
    except (ValueError, TypeError):
        ts = None
    if not ts or abs(ts - time.time()) < 2:
        return
    try:
        r = subprocess.run(["doas", SET_CLOCK, str(int(ts))], capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            print(f"🕒 Clock set from GPS ({int(ts - time.time()):+d} s)")
    except (OSError, subprocess.SubprocessError) as e:
        print(f"⚠️ clock not set from GPS: {e}")


def run_window(why: List[str], timeout_s: Optional[float] = None) -> bool:
    """USB on, wait for a live fix, USB back; True if a fix came. timeout_s
    (default window_s()) counts from USB on, the modem's start included."""
    from tools import usb_power
    timeout_s = window_s() if timeout_s is None else timeout_s
    end = time.monotonic() + timeout_s
    print(f"🛰️ GPS window ({', '.join(why)}): USB on for up to {timeout_s:.0f} s")
    usb_power.acquire("gps")
    try:
        usb_power.wait_for("/dev/ttyUSB1", min(45.0, timeout_s))
        got = _wait_for_fix(max(0.0, end - time.monotonic()))
        if got:
            print(f"🛰️ Live fix: {got['location'].get('gps_lat'):.5f}, {got['location'].get('gps_lon'):.5f}")
            _set_clock_from(got["packet"])
        else:
            print("🛰️ No live fix in the window; the cached fix stays in use")
        return bool(got)
    finally:
        usb_power.release("gps")


def start_if_due(background: bool = True) -> Optional[List[str]]:
    """Run the window if a fix is due and it hasn't run this boot; returns the reasons, or None."""
    global _thread
    if os.path.exists(BOOT_MARK) or (_thread is not None and _thread.is_alive()):
        return None
    timeout_s = window_s()
    if timeout_s < MIN_WINDOW_S:
        # Checked before a knock's or the server's request is used up, so the next wake has it
        if reasons() or os.path.exists(REQUEST_FILE):
            print(f"🛰️ GPS fix due but only {timeout_s:.0f} s of this wake left: next wake")
        return None
    try:
        from tools import pose_monitor
        pose_wake = pose_monitor.take_gps_wake()
    except Exception:
        pose_wake = False
    why = reasons(requested=_take_request(), pose_wake=pose_wake)
    if not why:
        return None
    now = time.time()
    try:
        with open(BOOT_MARK, "w") as f:
            f.write(json.dumps({"at": now, "why": why, "deadline": now + timeout_s}))
    except OSError:
        pass
    _record_attempt(now)

    def run():
        try:
            run_window(why, timeout_s=timeout_s)
        finally:
            _mark({"done": True})

    if background:
        _thread = threading.Thread(target=run, name="gps-window", daemon=True)
        _thread.start()
    else:
        run()
    return why


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="GPS fix window")
    p.add_argument("--now", action="store_true", help="take a fix now, due or not")
    a = p.parse_args()
    if a.now:
        sys.exit(0 if run_window(["by hand"]) else 1)
    age = last_live_fix_age_s()
    print("last live fix:", "never" if age is None else f"{age / 3600:.1f} h ago",
          "| due:", reasons() or "no")
