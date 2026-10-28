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
- `hold_awake` is true in runtime_config.json (bench work; the old flag file
  /etc/watercam/hold-awake is still honoured), or
- emergency mode is on, until its deadline: emergency_since + emergency_max_hours
  (runtime_config; 24 h when unset, as `21 91 00` leaves it; 0 = no expiry) plus
  EMERGENCY_MARGIN_MIN. call_shutdown() normally ends emergency mode at
  emergency_max_hours, but only while the TickTalk graph runs; emergency mode
  clears the Witty Pi shutdown alarm, so a hung graph would otherwise keep the
  unit up until the battery is flat. Past the deadline this clears emergency_mode
  in runtime_config and shuts down as below.

  A missing or unusable emergency_since (not a number, or in the future, as after
  a clock that was wrong when it was set) counts the emergency from this boot:
  the deadline is then uptime > emergency_max_hours + margin. That can't strand
  the unit (the next slot is armed on the way down whatever the state) and can't
  keep it up forever (uptime only grows), where "started now" would restart the
  clock on every check.

The shutdown is the one call_shutdown() uses: `doas /usr/sbin/shutdown -h now`
(sudo as the fallback). That is a normal OS shutdown, so the Witty Pi sees the Pi
halt and wittypi-next-wake.service arms the next slot on the way down.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO = os.environ.get("WATERCAM_REPO", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNTIME_CONFIG = os.path.join(REPO, "runtime_config.json")
SESSION_STATE = os.path.join(REPO, "data", "remote_debug.json")
HOLD_FLAG = "/etc/watercam/hold-awake"   # old switch, still honoured; hold_awake replaces it
DEFAULT_MAX_MINUTES = 60
# Same default as RuntimeParameterManager.EMERGENCY_MAX_HOURS and call_shutdown()
EMERGENCY_DEFAULT_HOURS = 24
# Past the deadline the graph's own expiry (once per iteration) gets this long first,
# plus one emergency_frequency: that is how far apart the iterations are
EMERGENCY_MARGIN_MIN = 15
EMERGENCY_DEFAULT_FREQUENCY_MIN = 10
EMERGENCY_MAX_FREQUENCY_MIN = 240
# emergency_since this far ahead of the clock is treated as unusable
CLOCK_SLACK_S = 300
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


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def emergency_elapsed_min(cfg: dict, now: float, uptime_min: Optional[float]) -> Optional[float]:
    """Minutes emergency mode has run: from emergency_since, or from boot when
    that is missing or unusable (see the module docstring). None if neither is known."""
    since = _number(cfg.get("emergency_since"))
    if since is not None and since > 0 and since <= now + CLOCK_SLACK_S:
        return max(0.0, (now - since) / 60)
    return uptime_min


def emergency_limit_min(cfg: dict) -> Optional[float]:
    """emergency_max_hours + margin in minutes; None for 0 (no expiry). A bad value
    falls back to the 24 h default."""
    hours = _number(cfg.get("emergency_max_hours", EMERGENCY_DEFAULT_HOURS))
    if hours is None or hours < 0:
        hours = EMERGENCY_DEFAULT_HOURS
    if hours == 0:
        return None
    every = _number(cfg.get("emergency_frequency"))
    if every is None or every < 1:
        every = EMERGENCY_DEFAULT_FREQUENCY_MIN
    every = min(every, EMERGENCY_MAX_FREQUENCY_MIN)
    return hours * 60 + EMERGENCY_MARGIN_MIN + every


def emergency_expired(cfg: dict, now: Optional[float] = None,
                      uptime_min: Optional[float] = None) -> bool:
    """True when emergency mode is on and has outlived its deadline."""
    if not cfg.get("emergency_mode"):
        return False
    limit = emergency_limit_min(cfg)
    if limit is None:
        return False
    elapsed = emergency_elapsed_min(cfg, time.time() if now is None else now, uptime_min)
    return elapsed is not None and elapsed > limit


def clear_emergency_mode(path: str = None) -> bool:
    """Turn emergency_mode off in runtime_config, as call_shutdown() does at expiry,
    so the next boot follows the schedule. Never raises."""
    path = path or RUNTIME_CONFIG
    try:
        import config_io
        with config_io.locked(path):
            cfg = config_io.read_json(path)
            cfg["emergency_mode"] = False
            cfg["emergency_since"] = None
            config_io.write_json(path, cfg)
        return True
    except Exception as exc:   # the shutdown must still happen
        print(f"max_awake: could not clear emergency_mode: {exc}", flush=True)
        return False


def decide(uptime_min: Optional[float], limit_min: float, session_active: bool,
           hold: bool, emergency: bool, emergency_over: bool = False) -> str:
    """'shutdown' or 'stay', with the reason: ('stay: ...').

    emergency: emergency mode is on; emergency_over: it is past its deadline
    (emergency_expired()), so it no longer keeps the unit up."""
    if limit_min <= 0:
        return "stay: backstop disabled (max_awake_minutes=0)"
    if uptime_min is None:
        return "stay: uptime unreadable"
    if uptime_min <= limit_min:
        return f"stay: awake {uptime_min:.0f} min of {limit_min:g}"
    if session_active:
        return "stay: remote debug session active"
    if hold:
        return "stay: held for bench work (hold_awake)"
    if emergency and not emergency_over:
        return "stay: emergency mode"
    return "shutdown"


def check(dry_run: bool = False) -> str:
    cfg = _read_json(RUNTIME_CONFIG)
    uptime = uptime_minutes()
    emergency = bool(cfg.get("emergency_mode"))
    over = emergency_expired(cfg, uptime_min=uptime)
    verdict = decide(
        uptime, max_minutes(cfg),
        session_active=bool(_read_json(SESSION_STATE).get("active")),
        hold=cfg.get("hold_awake") is True or os.path.exists(HOLD_FLAG),
        emergency=emergency, emergency_over=over,
    )
    print(f"max_awake: {verdict}", flush=True)
    if verdict == "shutdown" and emergency:
        print("max_awake: emergency mode is past emergency_since + emergency_max_hours"
              f" + {EMERGENCY_MARGIN_MIN} min", flush=True)
    if verdict == "shutdown" and not dry_run:
        if emergency:
            clear_emergency_mode()
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
