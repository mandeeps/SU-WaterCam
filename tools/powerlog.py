#!/usr/bin/env python3
"""
Log the node's power supply once a minute, in a form that survives power cuts.

Each line records the WittyPi's input voltage, output voltage and current,
the Pi's own throttle flags (under-voltage shows up here even when the
WittyPi's averaged readings look fine), CPU clock and temperature, load,
and the WittyPi RTC time. Every line is fsynced, so the last reading before
a brownout is on disk, and the gap to the next boot shows how long the
power was off.

A line starting with '#' marks each start of the logger, with the boot id
and the WittyPi's start-up reason (I2C_ACTION_REASON, e.g. 0x01 alarm,
0x03 button, 0x0a power newly connected). Timestamps right after boot come
from a stale system clock until the WittyPi daemon sets it, so use the
rtc_time column for timing.

Run by config/powerlog.service. Standard library only, so it runs under the
system python3. Summarise a log with tools/power_test_report.py.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional

DEFAULT_LOG = "/home/pi/powerlog/powerlog.csv"
INTERVAL_S = 60
WITTYPI_ADDR = "0x08"
HEADER = "time,uptime_s,boot_id,vin,vout,iout,power_mode,throttled,arm_mhz,temp_c,load1,rtc_time\n"

# WittyPi 4 registers: voltage/current as integer + hundredths pairs.
REG_VIN, REG_VOUT, REG_IOUT = 1, 3, 5
REG_POWER_MODE = 7
REG_ACTION_REASON = 11
REG_RTC_FIRST = 58  # seconds, minutes, hours, day, weekday, month, year (BCD)


def i2c(reg: int) -> Optional[int]:
    try:
        out = subprocess.run(["/usr/sbin/i2cget", "-y", "1", WITTYPI_ADDR, str(reg)],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.strip(), 16)
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def reading(int_reg: int) -> str:
    """A WittyPi voltage or current as text ('4.86'), or '' if unreadable.

    The integer and hundredths are separate registers, so the WittyPi can
    update between the two reads (4.99 -> 5.00 read as 4.00). Read the integer
    again afterwards and retry the pair if it moved.
    """
    for _ in range(3):
        i, d, again = i2c(int_reg), i2c(int_reg + 1), i2c(int_reg)
        if None in (i, d, again):
            return ""
        if i == again:
            return f"{i}.{d:02d}"
    return ""


def decode_rtc(regs: List[Optional[int]]) -> str:
    """ISO time from the seven BCD RTC registers, or '' if any is missing."""
    if len(regs) != 7 or None in regs:
        return ""
    regs = list(regs)
    regs[0] &= 0x7F  # top bit of the seconds register is a status flag
    sec, mi, hr, day, _, mon, yr = ((v >> 4) * 10 + (v & 0x0F) for v in regs)
    return f"20{yr:02d}-{mon:02d}-{day:02d}T{hr:02d}:{mi:02d}:{sec:02d}"


def rtc_time() -> str:
    """WittyPi RTC time; right even before the system clock is set at boot."""
    return decode_rtc([i2c(r) for r in range(REG_RTC_FIRST, REG_RTC_FIRST + 7)])


def vcgencmd(*args: str) -> str:
    try:
        out = subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=5).stdout
        return out.strip().split("=", 1)[-1]
    except (subprocess.SubprocessError, OSError):
        return ""


def write(path: str, line: str) -> None:
    """Append a line and fsync it, keeping the file parseable after a power cut.

    - The header goes in whenever the file is empty, not only when it is new:
      a cut before the first fsync can leave a zero-length file behind.
    - If a cut tore the last line (no trailing newline), finish it first, or
      this line, often the next boot's '#' marker, would be glued onto it.
    """
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        prefix = ""
        if size == 0:
            prefix = HEADER
        elif os.pread(fd, 1, size - 1) != b"\n":
            prefix = "\n"
        os.write(fd, (prefix + line).encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def sample_line(boot_id: str) -> str:
    with open("/proc/uptime") as f:
        uptime = float(f.read().split()[0])
    hz = vcgencmd("measure_clock", "arm")
    mode = i2c(REG_POWER_MODE)
    return ",".join([
        datetime.now().isoformat(timespec="seconds"), f"{uptime:.0f}", boot_id,
        reading(REG_VIN), reading(REG_VOUT), reading(REG_IOUT),
        "" if mode is None else str(mode),
        vcgencmd("get_throttled"), f"{int(hz) // 1_000_000}" if hz.isdigit() else "",
        vcgencmd("measure_temp").rstrip("'C"), f"{os.getloadavg()[0]:.2f}", rtc_time(),
    ]) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Log WittyPi power readings once a minute.")
    p.add_argument("--log", default=DEFAULT_LOG, help=f"CSV to append to (default {DEFAULT_LOG})")
    p.add_argument("--interval", type=float, default=INTERVAL_S, help="seconds between readings")
    args = p.parse_args()
    if args.interval <= 0:
        p.error("--interval must be greater than 0")

    os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
    with open("/proc/sys/kernel/random/boot_id") as f:
        boot_id = f.read().strip()[:8]
    reason = i2c(REG_ACTION_REASON)
    write(args.log, f"# {datetime.now().isoformat(timespec='seconds')} (rtc {rtc_time()}) "
                    f"logger start, boot {boot_id}, wittypi action reason "
                    f"{'' if reason is None else hex(reason)}\n")
    while True:
        try:
            write(args.log, sample_line(boot_id))
        except OSError as e:
            # Disk full, read-only after fsck, card hiccup: keep sampling rather
            # than exit, since each systemd restart writes a marker that would
            # look like a reboot in the log.
            print(f"powerlog: could not write {args.log}: {e}", file=sys.stderr)
        time.sleep(args.interval - time.time() % args.interval)


if __name__ == "__main__":
    main()
