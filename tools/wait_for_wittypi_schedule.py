#!/usr/bin/env python3
"""
Hold the main program until the WittyPi daemon has armed the next wake.

The WittyPi daemon sets the next startup and shutdown alarms from
schedule.wpi a few seconds after boot. ticktalk.service only waits for
multi-user.target, so without this gate the cameras, modem and inference
start alongside the daemon. If the supply browns out in that window the
Pi dies with no startup alarm armed, and the unit never wakes again on its
own: 006 was stranded this way on 2026-09-14.

Runs as ticktalk.service's ExecStartPre. It always exits 0: a missing or
late schedule is logged, but it must never stop data collection.
"""

import re
import sys
import time
from datetime import datetime
from typing import Optional

WITTYPI_LOG = "/home/pi/wittypi/wittyPi.log"
TIMEOUT_S = 120
POLL_S = 1.0
# Only the tail is read; one boot writes a few hundred bytes.
TAIL_BYTES = 64 * 1024
# Log timestamps have one-second resolution and the boot time is derived
# from /proc/uptime, so allow a little slack when comparing the two.
BOOT_SLACK_S = 5

# runScript.sh logs one of these once it has finished with the alarms.
_SCHEDULED = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] Schedule next startup at:\s+(.+)$")
_NO_SCHEDULE = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] File "schedule\.wpi" not found')


def boot_time() -> float:
    """Wall-clock time of this boot, in seconds since the epoch.

    Recomputed on every poll: right after boot the system clock may still be
    stale until the WittyPi daemon copies the RTC time into it.
    """
    with open("/proc/uptime") as f:
        return time.time() - float(f.read().split()[0])


def read_tail(path: str, nbytes: int = TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - nbytes))
            return f.read().decode(errors="replace")
    except OSError:
        return ""


def find_schedule_result(log_text: str, since: float) -> Optional[str]:
    """Return a description of this boot's schedule result, or None if not logged yet.

    Lines stamped before `since` belong to earlier boots and are ignored.
    """
    for line in reversed(log_text.splitlines()):
        for pattern, describe in (
            (_SCHEDULED, lambda m: f"next startup armed for {m.group(2).strip()}"),
            (_NO_SCHEDULE, lambda m: "no schedule.wpi, nothing to arm"),
        ):
            m = pattern.match(line)
            if not m:
                continue
            stamp = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            if stamp >= since - BOOT_SLACK_S:
                return describe(m)
    return None


def wait_for_schedule(log_path: str = WITTYPI_LOG, timeout_s: float = TIMEOUT_S,
                      poll_s: float = POLL_S) -> Optional[str]:
    deadline = time.monotonic() + timeout_s
    while True:
        result = find_schedule_result(read_tail(log_path), boot_time())
        if result or time.monotonic() >= deadline:
            return result
        time.sleep(poll_s)


def main() -> int:
    start = time.monotonic()
    result = wait_for_schedule()
    waited = time.monotonic() - start
    if result:
        print(f"WittyPi schedule ready after {waited:.0f} s: {result}")
    else:
        print(f"WARNING: WittyPi schedule not logged within {waited:.0f} s; "
              "starting anyway, the next wake may not be armed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
