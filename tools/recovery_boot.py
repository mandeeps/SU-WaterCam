#!/usr/bin/env python3
"""
After a power outage, put the node back to sleep until the battery has recharged.

The WittyPi is left on "default ON", so it boots the Pi as soon as the V50
restores its output. That is the only way a node gets back on schedule after
an outage: a WittyPi with no power when its alarm comes round never starts the
Pi later, and never sets a later alarm by itself. But the V50 restores its
output as soon as it has a little charge, and a full capture cycle on a nearly
empty pack browns out again (006, 2026-09-14 to 2026-09-29).

So when this boot came from power returning, rather than from an alarm or the
button, this arms the next schedule slot at least `min_delay_minutes` away and
shuts down before ticktalk starts the cameras, modem and inference.

A scheduled wake whose boot surge browned out the WittyPi itself also reports
"power newly connected" (most of 006's wakes in Aug-Sep 2026). Those are told
apart by time: they boot within a few minutes after the startup alarm the
previous boot armed, and they run the normal cycle.

Runs as root from wittypi-recovery.service, ordered before ticktalk.service.
Settings live under "recovery_boot" in runtime_config.json.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wait_for_wittypi_schedule as ws  # noqa: E402

WITTYPI_DIR = "/home/pi/wittypi"
RUNTIME_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runtime_config.json")
WITTYPI_ADDR = "0x08"

DEFAULTS = {
    "enabled": True,
    # Charging time the next wake must leave before it.
    "min_delay_minutes": 120,
    # Used when schedule.wpi is missing, has ended, or uses WAIT states.
    "fallback_delay_minutes": 120,
    # How long after the previously armed startup a boot still counts as that
    # scheduled wake (006 has restarted up to 13 minutes late).
    "scheduled_wake_window_minutes": 20,
}

# I2C_ACTION_REASON values that mean the WittyPi itself has just regained power.
I2C_ACTION_REASON = 11
POWER_RESTORE_REASONS = {
    0x05: "input voltage reached the restore voltage",
    0x09: "USB 5V connected",
    0x0A: "power supply newly connected",
}

# Startup alarm (I2C_CONF_*_ALARM1) and shutdown alarm (*_ALARM2) registers.
ALARM1_REGS = (27, 28, 29, 30)  # second, minute, hour, day (BCD)
ALARM2_REGS = (32, 33, 34, 35)

_ARMED = re.compile(ws.STAMP + r"Schedule next startup at:\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
_DURATION = re.compile(r"([DHMS])(\d+)")
_UNIT_S = {"D": 86400, "H": 3600, "M": 60, "S": 1}


def load_settings(path: str = RUNTIME_CONFIG) -> dict:
    settings = dict(DEFAULTS)
    try:
        with open(path) as f:
            settings.update(json.load(f).get("recovery_boot", {}))
    except (OSError, ValueError):
        pass
    return settings


def _parse_stamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def previously_armed_startup(log_text: str, boot: float) -> Optional[float]:
    """The startup alarm armed by the last boot before this one, if it was logged."""
    for line in reversed(log_text.splitlines()):
        m = _ARMED.match(line)
        if m and _parse_stamp(m.group(1)) < boot - ws.BOOT_SLACK_S:
            return _parse_stamp(m.group(2))
    return None


def is_scheduled_wake(boot: float, armed: Optional[float], window_s: float) -> bool:
    """True when this boot is the armed wake, even if the WittyPi browned out at it."""
    # Allow a little early too: the RTC and the system clock can differ slightly.
    return armed is not None and armed - 120 <= boot <= armed + window_s


def parse_schedule(text: str) -> Optional[Tuple[float, float, List[Tuple[str, int]]]]:
    """(begin, end, [(state, seconds)]) from a schedule.wpi, or None if it can't be used.

    Mirrors runScript.sh. WAIT states hand the timing to something else, so a
    schedule using them is reported as unusable.
    """
    begin = end = None
    states = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        word, _, rest = line.partition(" ")
        try:
            if word == "BEGIN":
                begin = _parse_stamp(rest.strip())
            elif word == "END":
                end = _parse_stamp(rest.strip())
            elif word in ("ON", "OFF"):
                if "WAIT" in rest:
                    return None
                seconds = sum(int(n) * _UNIT_S[u] for u, n in _DURATION.findall(rest))
                if seconds <= 0:
                    return None
                states.append((word, seconds))
            else:
                return None
        except ValueError:
            return None
    if begin is None or end is None or not states:
        return None
    if not any(s == "ON" for s, _ in states) or not any(s == "OFF" for s, _ in states):
        return None
    return begin, end, states


def next_on_start(schedule, not_before: float) -> Optional[float]:
    """Start of the first ON state at or after `not_before`, or None if the schedule ends first."""
    begin, end, states = schedule
    period = sum(seconds for _, seconds in states)
    t = begin
    if not_before > begin:
        t += (not_before - begin) // period * period
    for _ in range(2 * len(states) + 1):
        for state, seconds in states:
            if t >= end:
                return None
            if state == "ON" and t >= not_before:
                return t
            t += seconds
    return None


def choose_wake(now: float, settings: dict, schedule_text: Optional[str]) -> Tuple[float, str]:
    not_before = now + settings["min_delay_minutes"] * 60
    schedule = parse_schedule(schedule_text) if schedule_text else None
    if schedule:
        wake = next_on_start(schedule, not_before)
        if wake is not None:
            return wake, "next schedule.wpi slot"
    return now + settings["fallback_delay_minutes"] * 60, "fixed delay (no usable schedule.wpi)"


def _bcd(n: int) -> int:
    return (n // 10) << 4 | (n % 10)


def i2c_get(reg: int) -> int:
    out = subprocess.run(["/usr/sbin/i2cget", "-y", "1", WITTYPI_ADDR, str(reg)],
                         capture_output=True, text=True, timeout=5, check=True).stdout
    return int(out.strip(), 16)


def i2c_set(reg: int, value: int) -> None:
    subprocess.run(["/usr/sbin/i2cset", "-y", "1", WITTYPI_ADDR, str(reg), str(value)],
                   timeout=5, check=True)


def arm_startup(wake: float) -> None:
    t = datetime.fromtimestamp(wake)
    for reg, value in zip(ALARM1_REGS, (t.second, t.minute, t.hour, t.day)):
        i2c_set(reg, _bcd(value))
    # The daemon armed a shutdown for the end of this ON slot; it is moot now.
    for reg in ALARM2_REGS:
        i2c_set(reg, 0)
    readback = [i2c_get(reg) for reg in ALARM1_REGS]
    if readback != [_bcd(v) for v in (t.second, t.minute, t.hour, t.day)]:
        raise RuntimeError(f"startup alarm read back as {readback}")


def log_to_wittypi(message: str) -> None:
    """Append to wittyPi.log in the daemon's own format, so field forensics see it."""
    line = f"{datetime.now():[%Y-%m-%d %H:%M:%S]} {message}\n"
    with open(os.path.join(WITTYPI_DIR, "wittyPi.log"), "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def decide(reason: Optional[int], boot: float, log_text: str, settings: dict) -> Tuple[bool, str]:
    """(defer, why) for this boot."""
    if not settings["enabled"]:
        return False, "recovery boot disabled in runtime_config.json"
    if reason not in POWER_RESTORE_REASONS:
        return False, f"start-up reason {reason if reason is None else hex(reason)} is not a power restore"
    armed = previously_armed_startup(log_text, boot)
    if is_scheduled_wake(boot, armed, settings["scheduled_wake_window_minutes"] * 60):
        return False, (f"{POWER_RESTORE_REASONS[reason]}, but within the window after the wake armed for "
                       f"{datetime.fromtimestamp(armed):%Y-%m-%d %H:%M:%S}: a scheduled wake that browned out")
    armed_text = "none logged" if armed is None else f"{datetime.fromtimestamp(armed):%Y-%m-%d %H:%M:%S}"
    return True, f"{POWER_RESTORE_REASONS[reason]}, previously armed wake {armed_text}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="report the decision, change nothing")
    args = p.parse_args(argv)

    settings = load_settings()
    try:
        reason = i2c_get(I2C_ACTION_REASON)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"Can't read the WittyPi start-up reason ({e}); running the normal cycle")
        return 0

    log_path = os.path.join(WITTYPI_DIR, "wittyPi.log")
    defer, why = decide(reason, ws.boot_time(), ws.read_tail(log_path), settings)
    if not defer:
        print(f"Normal cycle: {why}")
        return 0

    # Let the daemon finish arming its own alarms first, or it would overwrite ours.
    ws.wait_for_schedule(log_path)
    schedule_text = None
    try:
        with open(os.path.join(WITTYPI_DIR, "schedule.wpi")) as f:
            schedule_text = f.read()
    except OSError:
        pass
    now = time.time()
    wake, source = choose_wake(now, settings, schedule_text)
    wake_text = f"{datetime.fromtimestamp(wake):%Y-%m-%d %H:%M:%S}"
    print(f"Recovery boot: {why}. Next wake {wake_text} ({source}).")
    if args.dry_run:
        print("Dry run: alarm not armed, not shutting down")
        return 0

    try:
        arm_startup(wake)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as e:
        # Without a confirmed alarm, shutting down could strand the node.
        print(f"Could not arm the startup alarm ({e}); running the normal cycle instead")
        return 0
    log_to_wittypi(f"Recovery boot: {why}; shutting down until the battery has recharged.")
    # Same wording as runScript.sh, so the next boot's previously_armed_startup()
    # finds this alarm and treats a brownout at it as a scheduled wake.
    log_to_wittypi(f"Schedule next startup at:  {wake_text}")
    subprocess.run(["systemctl", "poweroff"], check=False)
    # Hold this oneshot until shutdown kills it, so ticktalk.service (ordered
    # after us) never starts.
    time.sleep(300)
    return 0


if __name__ == "__main__":
    sys.exit(main())
