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
from typing import Optional, Tuple

WITTYPI_LOG = "/home/pi/wittypi/wittyPi.log"
# wittypi-boot-mark.service writes wittyPi.log's size here just before the
# WittyPi daemon starts; /run is cleared every boot, so everything after that
# offset is this boot's. Without it, fall back to comparing timestamps.
BOOT_OFFSET_FILE = "/run/wittypi-log-offset"
TIMEOUT_S = 120
POLL_S = 1.0
# Only the tail is read; one boot writes a few hundred bytes.
TAIL_BYTES = 64 * 1024
# Log timestamps have one-second resolution and the boot time is derived
# from /proc/uptime, so allow a little slack when comparing the two.
BOOT_SLACK_S = 5

# runScript.sh logs one of these once it has finished with the alarms. The
# daemon stamps lines [like this] normally and <like this> when it isn't sure
# the system time is right.
STAMP = r"^[\[<](\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)[\]>] "
_SCHEDULED = re.compile(STAMP + r"Schedule next startup at:\s+(.+)$")
_NO_SCHEDULE = re.compile(STAMP + r'File "schedule\.wpi" not found')


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


def boot_offset(path: str = BOOT_OFFSET_FILE) -> Optional[int]:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def split_log(path: str, offset: int, before_bytes: int = TAIL_BYTES) -> Tuple[str, str]:
    """(the end of earlier boots' lines, this boot's lines), split at `offset`."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return "", ""
    if len(data) < offset:  # the log was truncated or replaced since the mark
        offset = 0
    before = data[max(0, offset - before_bytes):offset]
    return before.decode(errors="replace"), data[offset:].decode(errors="replace")


def find_schedule_result(log_text: str, since: Optional[float]) -> Optional[str]:
    """Return a description of this boot's schedule result, or None if not logged yet.

    With `since`, lines stamped before it belong to earlier boots and are
    ignored; pass None when `log_text` is already only this boot's lines.
    """
    for line in reversed(log_text.splitlines()):
        for pattern, describe in (
            (_SCHEDULED, lambda m: f"next startup armed for {m.group(2).strip()}"),
            (_NO_SCHEDULE, lambda m: "no schedule.wpi, nothing to arm"),
        ):
            m = pattern.match(line)
            if not m:
                continue
            if since is None:
                return describe(m)
            stamp = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
            if stamp >= since - BOOT_SLACK_S:
                return describe(m)
    return None


def this_boots_schedule_result(log_path: str, offset_file: str = BOOT_OFFSET_FILE) -> Optional[str]:
    offset = boot_offset(offset_file)
    if offset is not None:
        return find_schedule_result(split_log(log_path, offset)[1], None)
    return find_schedule_result(read_tail(log_path), boot_time())


def wait_for_schedule(log_path: str = WITTYPI_LOG, timeout_s: float = TIMEOUT_S,
                      poll_s: float = POLL_S, offset_file: str = BOOT_OFFSET_FILE) -> Optional[str]:
    deadline = time.monotonic() + timeout_s
    while True:
        result = this_boots_schedule_result(log_path, offset_file)
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
