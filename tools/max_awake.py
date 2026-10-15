#!/usr/bin/env python3
"""Backstop: power the Pi off when it has been awake too long.

A normal wake is a few minutes and ticktalk's call_shutdown() powers off at the
end of it. If that never happens (a hung capture, a restart loop, a downlink that
keeps the unit awake) nothing else ends the wake until the Witty Pi's shutdown
alarm, and the Witty Pi isn't armed to do that after a boot between slots or in
some recovery states. max-awake.timer runs `max_awake.py check` every 5 minutes;
once the Pi has been up longer than the limit it shuts down.

Limit: `max_awake_minutes` in runtime_config.json (default 60; 0 turns the
backstop off). A normal wake is 10-15 min, so the default only ever fires on a
fault.

Never shuts down while
- a remote debug session is active (data/remote_debug.json, tools/remote_debug.py;
  sessions end on their own, at most 240 min),
- the hold flag exists: /etc/watercam/hold-awake (`sudo touch` it to keep a unit
  up for bench work, remove it to release; same directory as usb-on-at-boot), or
- emergency mode is on (it keeps the unit awake on purpose and call_shutdown()
  ends it after emergency_max_hours).

The shutdown is the one call_shutdown() uses: `doas /usr/sbin/shutdown -h now`
(sudo as the fallback). That is a normal OS shutdown, so the Witty Pi sees the Pi
halt and wittypi-next-wake.service arms the next slot on the way down.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Optional

REPO = os.environ.get("WATERCAM_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNTIME_CONFIG = os.path.join(REPO, "runtime_config.json")
SESSION_STATE = os.path.join(REPO, "data", "remote_debug.json")
HOLD_FLAG = "/etc/watercam/hold-awake"
DEFAULT_MAX_MINUTES = 60
SHUTDOWN_COMMANDS = (["doas", "/usr/sbin/shutdown", "-h", "now"],
                     ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"])


def _read_json(path: str) -> dict:
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def uptime_minutes() -> Optional[float]:
    try:
        with open("/proc/uptime") as fh:
            return float(fh.read().split()[0]) / 60
    except (OSError, ValueError, IndexError):
        return None


def max_minutes(config: Optional[dict] = None) -> float:
    """The configured limit in minutes; 0 means off. A bad value falls back to the default."""
    cfg = _read_json(RUNTIME_CONFIG) if config is None else config
    value = cfg.get("max_awake_minutes", DEFAULT_MAX_MINUTES)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return DEFAULT_MAX_MINUTES
    return value


def decide(uptime_min: Optional[float], limit_min: float, session_active: bool,
           hold: bool, emergency: bool) -> str:
    """'shutdown' or 'stay', with the reason: ('stay: ...')."""
    if limit_min <= 0:
        return "stay: backstop disabled (max_awake_minutes=0)"
    if uptime_min is None:
        return "stay: uptime unreadable"
    if uptime_min <= limit_min:
        return f"stay: awake {uptime_min:.0f} min of {limit_min:g}"
    if session_active:
        return "stay: remote debug session active"
    if hold:
        return f"stay: hold flag {HOLD_FLAG} present"
    if emergency:
        return "stay: emergency mode"
    return "shutdown"


def check(dry_run: bool = False) -> str:
    cfg = _read_json(RUNTIME_CONFIG)
    verdict = decide(
        uptime_minutes(), max_minutes(cfg),
        session_active=bool(_read_json(SESSION_STATE).get("active")),
        hold=os.path.exists(HOLD_FLAG),
        emergency=bool(cfg.get("emergency_mode")),
    )
    print(f"max_awake: {verdict}", flush=True)
    if verdict == "shutdown" and not dry_run:
        print("max_awake: awake past the limit, powering off", flush=True)
        for cmd in SHUTDOWN_COMMANDS:
            try:
                if subprocess.call(cmd, timeout=30) == 0:
                    break
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"max_awake: {cmd[0]} shutdown failed: {exc}", flush=True)
    return verdict


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("action", nargs="?", default="check", choices=["check"])
    parser.add_argument("--dry-run", action="store_true", help="say what would happen, don't power off")
    args = parser.parse_args(argv)
    check(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
