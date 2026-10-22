#!/home/pi/SU-WaterCam/venv/bin/python
"""A live GPS fix when one is due, with USB on only for as long as it takes.

The modem (and its GPS) is on USB, which stays off otherwise (tools/usb_power.py).
A fixed camera doesn't need a fix every wake: the last one is cached
(tools/get_gps.py) and used, marked as cached, in packets and photos. A live fix
is taken at a wake when:

- the last one is gps_fix_interval_h old or more (runtime parameter, default 24 h);
- the pose monitor saw a knock or removal (tools/pose_monitor.py): that wake,
  and after a removal the next two as well;
- the server asked for one (downlink 19 99);
- there is none at all.

start_if_due() runs the window on a thread, once per boot, so the captures go
on meanwhile: powering USB brings the modem up and quectel-gnss.service starts
its GNSS (40-50 s to a fix). The fix is saved, USB is given back (it goes off
unless a cellular upload or recording holds it), and if nothing set the clock
this boot (no network time), the fix's time sets it.
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
FIX_TIMEOUT_S = 120
POLL_S = 3
REQUEST_FILE = os.path.join(_REPO, "data", "gps_request.json")
TIME_SOURCE = "/run/watercam-time-source"
SET_CLOCK = "/usr/local/sbin/watercam-set-clock"
#: Once per boot: a second start_if_due() in the same boot (a knock seen later) runs only if this is absent
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


def reasons(now: Optional[float] = None, requested: bool = False, pose_wake: bool = False) -> List[str]:
    """Why a fix is due now (empty: not due). Pure apart from reading the cache."""
    out = []
    age = last_live_fix_age_s(now)
    if age is None:
        out.append("no fix yet")
    elif age >= _interval_h() * 3600:
        out.append(f"last fix {age / 3600:.0f} h ago")
    if pose_wake:
        out.append("knocked or removed")
    if requested:
        out.append("requested by the server")
    return out


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


def run_window(why: List[str], timeout_s: float = FIX_TIMEOUT_S) -> bool:
    """USB on, wait for a live fix, USB back; True if a fix came."""
    from tools import usb_power
    print(f"🛰️ GPS window ({', '.join(why)}): USB on for up to {timeout_s:.0f} s")
    usb_power.acquire("gps")
    try:
        usb_power.wait_for("/dev/ttyUSB1", 45)
        got = _wait_for_fix(timeout_s)
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
    try:
        from tools import pose_monitor
        pose_wake = pose_monitor.take_gps_wake()
    except Exception:
        pose_wake = False
    why = reasons(requested=_take_request(), pose_wake=pose_wake)
    if not why:
        return None
    try:
        with open(BOOT_MARK, "w") as f:
            f.write(json.dumps({"at": time.time(), "why": why}))
    except OSError:
        pass
    if background:
        _thread = threading.Thread(target=run_window, args=(why,), name="gps-window", daemon=True)
        _thread.start()
    else:
        run_window(why)
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
