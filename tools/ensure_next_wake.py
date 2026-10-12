#!/usr/bin/env python3
"""
At shutdown, make sure the WittyPi will start the Pi at the next schedule slot.

runScript.sh arms the alarms once, at boot, and assumes the Pi then follows
the schedule. A boot between slots (the button, or 03:00 when the wake test
restored the schedule) is treated as the start of the next ON state: it arms
the shutdown for that state's end and the startup for the slot after it. But
ticktalk powers off after one cycle, so the slot in between is never armed.
006 skipped 09:00 and 17:00 on 2026-10-12 that way.

This runs from wittypi-next-wake.service as the system stops, whatever started
the shutdown. If the armed startup is missing, already past, or later than the
first schedule.wpi slot at least `MARGIN_S` away, it arms that slot. It never
moves a wake later: an earlier alarm someone set on purpose stays.

A recovery boot (tools/recovery_boot.py) deliberately skips slots to let the
battery recharge; it leaves RECOVERY_MARK, and this then changes nothing.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recovery_boot as rb  # noqa: E402

# Time the Pi needs to be off before the alarm, or the WittyPi ignores it.
MARGIN_S = 120


def _from_bcd(v: int) -> int:
    return (v >> 4) * 10 + (v & 0x0F)


def armed_startup(regs, now: float) -> Optional[float]:
    """The armed startup from the ALARM1 registers (second, minute, hour, day; BCD).

    The alarm holds a day of the month but no month, so it means the first
    such moment from about now on. All zero, or values no clock can show, mean
    nothing is armed.
    """
    if not any(regs):
        return None
    sec, minute, hour, day = (_from_bcd(v & 0x7F) for v in regs)
    if not (0 <= sec < 60 and 0 <= minute < 60 and 0 <= hour < 24 and 1 <= day <= 31):
        return None
    start = datetime.fromtimestamp(now) - timedelta(days=1)
    year, month = start.year, start.month
    for _ in range(14):
        try:
            t = datetime(year, month, day, hour, minute, sec)
        except ValueError:  # e.g. the 31st in a 30-day month
            t = None
        if t is not None and t >= start:
            return t.timestamp()
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return None


def decide(now: float, armed: Optional[float], schedule_text: Optional[str],
           margin_s: float = MARGIN_S) -> Tuple[Optional[float], str]:
    """(wake to arm, or None to leave the alarm alone; why)."""
    schedule = rb.parse_schedule(schedule_text) if schedule_text else None
    if schedule is None:
        return None, "no usable schedule.wpi; alarm left as it is"
    slot = rb.next_on_start(schedule, now + margin_s)
    if slot is None:
        return None, "schedule.wpi has no slot left; alarm left as it is"
    slot_text = f"{datetime.fromtimestamp(slot):%Y-%m-%d %H:%M:%S}"
    if armed is None:
        return slot, f"no startup armed; arming the next slot, {slot_text}"
    armed_text = f"{datetime.fromtimestamp(armed):%Y-%m-%d %H:%M:%S}"
    if armed < now + margin_s:
        return slot, f"armed startup {armed_text} is past or too soon; arming {slot_text}"
    if armed > slot + 60:
        return slot, f"armed startup {armed_text} skips the slot at {slot_text}; arming it"
    return None, f"armed startup {armed_text} is the next slot or earlier; left as it is"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="report the decision, change nothing")
    args = p.parse_args(argv)

    if os.path.exists(rb.RECOVERY_MARK):
        print("Recovery boot armed this shutdown's wake; leaving it")
        return 0
    try:
        with open(os.path.join(rb.WITTYPI_DIR, "schedule.wpi")) as f:
            schedule_text = f.read()
    except OSError:
        print("No schedule.wpi; leaving the alarm alone")
        return 0
    try:
        now = rb.rtc_now()
        armed = armed_startup([rb.i2c_get(r) for r in rb.ALARM1_REGS], now)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"Can't read the WittyPi ({e}); leaving the alarm alone")
        return 0

    wake, why = decide(now, armed, schedule_text)
    print(why)
    if wake is None or args.dry_run:
        return 0
    try:
        rb.arm_startup(wake)
    except (OSError, subprocess.SubprocessError, ValueError, RuntimeError) as e:
        print(f"Could not arm the startup alarm ({e})")
        return 1
    wake_text = f"{datetime.fromtimestamp(wake):%Y-%m-%d %H:%M:%S}"
    rb.log_to_wittypi(f"Shutdown check: {why}.", now)
    # Same wording as runScript.sh, so the next boot's previously_armed_startup()
    # finds this alarm and treats a brownout at it as a scheduled wake.
    rb.log_to_wittypi(f"Schedule next startup at:  {wake_text}", now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
