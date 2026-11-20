#!/usr/bin/env python3
"""LoRa network time for syncTime.sh: the time lora_daemon received, carried to now.

    lora_answer.py     prints "UNIX_TIME OFFSET" (the time now, and how far the clock is from it)

lora_daemon (tools/lora_time.py) asks the network for the time once the mDot has
joined and writes the answer to ANSWER: the network's time at the moment it
arrived (its bias already subtracted) and the time since boot (CLOCK_BOOTTIME)
at that moment. Adding the time since boot that has passed since then gives the
time now to within milliseconds, even if something stepped the clock in between.
The daemon runs sandboxed and never sets the clock; syncTime.sh, which runs as
root from the Witty Pi daemon, applies the answer (docs/TIME_SYNC.md).

Refused: an answer from another boot or older than MAX_AGE_S, a time before 2026
or after 2100, or one more than MAX_STEP_S (a day) from the clock while the RTC
looks valid (the rule of network_time.py and tools/transmit_ip.py correct_clock).

Exit status:
  0  "UNIX_TIME OFFSET" on stdout
  1  an answer, refused; the reason on stderr
  3  the daemon is still asking (wait and try again)
  4  no answer is coming this boot (off, asked within the day, no daemon, or not this boot)

Standard library only: it runs as root from the Witty Pi daemon, outside the venv.
"""
import json
import math
import os
import stat
import sys
import time

ANSWER = os.environ.get("WATERCAM_LORA_ANSWER", "/tmp/watercam-lora-answer.json")
RTC_UNTRUSTED = os.environ.get("WATERCAM_RTC_UNTRUSTED", "/run/watercam-rtc-untrusted")
CLOCK_FLOOR = 1767225600       # 2026-01-01, as watercam-set-clock
CLOCK_CEILING = 4102444800     # 2100-01-01
MAX_STEP_S = 24 * 3600
#: Long enough for a slow join; the crystal drifts well under 0.1 s in that time
MAX_AGE_S = 30 * 60


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def boottime():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def read_answer(path=ANSWER):
    """The daemon's file, or None. Only a regular file owned by root or by the
    owner of this script's folder (pi), and never through a symlink: /tmp is
    shared."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        allowed = {0, os.stat(os.path.dirname(os.path.abspath(__file__))).st_uid}
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in allowed or st.st_size > 4096:
            return None
        with os.fdopen(fd, "r") as f:
            fd = None
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def main(path=ANSWER, boot=None, now_bt=None, clock=None, rtc_untrusted=None):
    boot = boot_id() if boot is None else boot
    data = read_answer(path)
    if not data or not boot or data.get("boot") != boot:
        return 4
    status = data.get("status")
    if status == "asking":
        return 3
    if status != "answer":
        return 4
    unix, at = data.get("unix"), data.get("boottime")
    if not (_number(unix) and _number(at)):
        print("LoRa time: unreadable answer", file=sys.stderr)
        return 1
    now_bt = boottime() if now_bt is None else now_bt
    age = now_bt - at
    if not 0 <= age <= MAX_AGE_S:
        print(f"LoRa time: the answer is {age:.0f} s old", file=sys.stderr)
        return 1
    target = unix + age
    clock = time.time() if clock is None else clock
    rtc_untrusted = os.path.exists(RTC_UNTRUSTED) if rtc_untrusted is None else rtc_untrusted
    if not CLOCK_FLOOR <= target < CLOCK_CEILING:
        print("LoRa time: out of range", file=sys.stderr)
        return 1
    if abs(target - clock) > MAX_STEP_S and not rtc_untrusted and clock >= CLOCK_FLOOR:
        print(f"LoRa time is {abs(target - clock) / 3600:.1f} h off the clock: more than a day, "
              "not applied (the RTC looks valid)", file=sys.stderr)
        return 1
    print(f"{target:.3f} {target - clock:+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
