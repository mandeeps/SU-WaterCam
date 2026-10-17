#!/usr/bin/env python3
"""Set the system clock from the Witty Pi RTC early in boot.

Each boot the system clock starts at the time saved at the previous shutdown
(systemd-timesyncd's clock file or fake-hwclock), so a unit that slept for half
an hour boots half an hour behind the Witty Pi RTC. The Witty Pi daemon and
syncTime.sh correct it, but only seconds to a minute later: tailscaled starts
with the wrong time (TLS errors on its bootstrap DNS, so the unit can be
unreachable for part of a short wake) and early timestamps are off.
rtc-early-clock.service runs this before tailscaled, the LoRa daemon, ticktalk
and time-sync.target.

It only ever moves the clock forward, and only to a plausible RTC time:
- every register is readable and valid BCD for its field (a missing Witty Pi
  or a failed read changes nothing);
- the RTC's oscillator-stop flag (bit 7 of the seconds register) is clear: set,
  the RTC lost power and its time can't be trusted (a dead backup battery
  restarts it at 2000-01-01);
- the time is after 2026-01-01 and before 2038-01-01 (the Witty Pi's schedule
  limit is 2037);
- it is ahead of the system clock by more than MIN_STEP_S. The saved shutdown
  time is a lower bound on the real time, so an RTC behind it is wrong.

The RTC holds local time (the Witty Pi scripts read it with `date --date`), so
it is converted with the system time zone, as they do.

It writes no time-source marker: an RTC-set clock still counts as unverified
(tools/capture_time.py) until syncTime.sh (network or GPS) or the server's Date
header confirms it, and it doesn't touch NTP or the RTC.
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional, Sequence

WITTYPI_BUS = "1"
WITTYPI_ADDR = "0x08"
REG_RTC_FIRST = 58      # seconds, minutes, hours, day, weekday, month, year (BCD)
I2C_DEVICE = "/dev/i2c-1"
DEVICE_WAIT_S = 5       # for i2c-dev to load; a unit without a Witty Pi then just goes on
READ_TIMEOUT_S = 1
EARLIEST = datetime(2026, 1, 1).timestamp()
LATEST = datetime(2038, 1, 1).timestamp()
MIN_STEP_S = 2

# (field, low, high) for the 7 registers after the BCD decode
_FIELDS = (("second", 0, 59), ("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31),
           ("weekday", 0, 6), ("month", 1, 12), ("year", 0, 99))


def _bcd(v: int) -> Optional[int]:
    hi, lo = v >> 4, v & 0x0F
    if hi > 9 or lo > 9:
        return None
    return hi * 10 + lo


def decode(regs: Sequence[Optional[int]]) -> Optional[datetime]:
    """The RTC time as a naive local datetime, or None if it can't be trusted."""
    if len(regs) != 7 or any(r is None or not 0 <= r <= 0xFF for r in regs):
        return None
    if regs[0] & 0x80:              # oscillator stopped: the time is not valid
        return None
    raw = [regs[0] & 0x7F, regs[1] & 0x7F, regs[2] & 0x3F, regs[3] & 0x3F,
           regs[4] & 0x07, regs[5] & 0x1F, regs[6]]
    vals = {}
    for (name, lo, hi), r in zip(_FIELDS, raw):
        v = _bcd(r)
        if v is None or not lo <= v <= hi:
            return None
        vals[name] = v
    try:
        return datetime(2000 + vals["year"], vals["month"], vals["day"],
                        vals["hour"], vals["minute"], vals["second"])
    except ValueError:              # e.g. 31 February
        return None


def decide(rtc_ts: Optional[float], system_ts: float) -> str:
    """'set' or 'skip: <reason>'."""
    if rtc_ts is None:
        return "skip: RTC unreadable or not valid"
    if not EARLIEST <= rtc_ts < LATEST:
        return f"skip: RTC time {datetime.fromtimestamp(rtc_ts):%Y-%m-%d %H:%M:%S} is implausible"
    step = rtc_ts - system_ts
    if step <= MIN_STEP_S:
        if step < -MIN_STEP_S:
            return f"skip: RTC is {-step:.0f} s behind the system clock (never moved back)"
        return "skip: system clock already agrees with the RTC"
    return "set"


def _i2cget(reg: int) -> Optional[int]:
    try:
        out = subprocess.run(["/usr/sbin/i2cget", "-y", WITTYPI_BUS, WITTYPI_ADDR, str(reg)],
                             capture_output=True, text=True, timeout=READ_TIMEOUT_S)
        return int(out.stdout.strip(), 16) if out.returncode == 0 else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def read_registers() -> List[Optional[int]]:
    regs: List[Optional[int]] = []
    for r in range(REG_RTC_FIRST, REG_RTC_FIRST + 7):
        v = _i2cget(r)
        regs.append(v)
        if v is None:               # no Witty Pi: don't spend a timeout per register
            break
    return regs + [None] * (7 - len(regs))


def rtc_timestamp() -> Optional[float]:
    """Read twice and accept only two reads that agree (a read can straddle a
    tick, e.g. 10:59:59 -> 11:00:00 read as 10:00:00)."""
    a = decode(read_registers())
    if a is None:
        return None
    b = decode(read_registers())
    if b is None or not 0 <= (b - a).total_seconds() <= 2:
        return None
    return b.timestamp()            # naive datetime: the local time zone, as the RTC holds


def main(argv=None) -> int:
    deadline = time.monotonic() + DEVICE_WAIT_S
    while not os.path.exists(I2C_DEVICE) and time.monotonic() < deadline:
        time.sleep(0.1)
    rtc_ts = rtc_timestamp() if os.path.exists(I2C_DEVICE) else None
    now = time.time()
    verdict = decide(rtc_ts, now)
    if verdict == "set":
        try:
            time.clock_settime(time.CLOCK_REALTIME, rtc_ts)
        except (OSError, PermissionError) as exc:
            print(f"rtc_early_clock: could not set the clock: {exc}", flush=True)
            return 0
        print(f"rtc_early_clock: system clock {datetime.fromtimestamp(now):%Y-%m-%d %H:%M:%S} -> "
              f"RTC {datetime.fromtimestamp(rtc_ts):%Y-%m-%d %H:%M:%S} (+{rtc_ts - now:.0f} s)", flush=True)
    else:
        print(f"rtc_early_clock: {verdict}", flush=True)
    return 0                         # never fail the boot


if __name__ == "__main__":
    sys.exit(main())
