"""The capture time of one capture cycle: its ID on every transport.

get_time writes the time it fired into the capture directory. The LoRa packet
(channel 00 01), the IP uplink (device_ts) and both store-and-forward queues
carry this same value, so the node and the server can tell that two payloads
are the same capture whichever way they arrived. Captures are at least 30 s
apart, so a unit never reuses one.
"""

import os
import time
from datetime import datetime
from typing import Optional

FILENAME = "capture_time"
#: Present in a capture directory when the capture was taken before the clock was
#: known to be right. Sent as `clock_unverified` (channel 01 0D, 1 = unverified).
UNVERIFIED_FILENAME = "clock_unverified"
#: Written by syncTime.sh (network or GPS time) or watercam-set-clock (server time);
#: /run is cleared at every boot.
TIME_SOURCE = "/run/watercam-time-source"
#: Boot id, written by transmit_ip.correct_clock when the server's Date header
#: agreed with the clock (so nothing needed setting and no TIME_SOURCE was written).
CLOCK_VERIFIED = "/tmp/watercam-clock-verified"


def _boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def clock_verified() -> bool:
    """True once this boot's clock has come from, or been checked against, a real
    time source (network, GPS or the server). Until then the RTC alone is trusted,
    which can be hours or days off after a long outage."""
    if os.path.exists(TIME_SOURCE):
        return True
    boot = _boot_id()
    try:
        with open(CLOCK_VERIFIED) as fh:
            return bool(boot) and fh.read().strip() == boot
    except OSError:
        return False


def mark_clock_verified() -> None:
    try:
        with open(CLOCK_VERIFIED, "w") as fh:
            fh.write(_boot_id())
    except OSError:
        pass


def clock_unverified(directory: Optional[str]) -> bool:
    """True if this capture was taken before the clock was verified."""
    return bool(directory) and os.path.exists(os.path.join(directory, UNVERIFIED_FILENAME))


def write_capture_time(directory: str, when: Optional[float] = None) -> int:
    """Record the capture time (Unix seconds) in the capture directory and return it."""
    ts = int(when if when is not None else time.time())
    try:
        with open(os.path.join(directory, FILENAME), "w") as fh:
            fh.write(f"{ts}\n")
        if not clock_verified():
            open(os.path.join(directory, UNVERIFIED_FILENAME), "w").close()
    except OSError:
        pass
    return ts


def capture_time(directory: Optional[str]) -> int:
    """The capture time for a capture directory.

    Falls back to parsing the directory name (YYYYmmdd-HHMMSS, local time;
    ambiguous only in the repeated hour when clocks go back), then to now.
    """
    if directory:
        try:
            with open(os.path.join(directory, FILENAME)) as fh:
                return int(fh.read().strip())
        except (OSError, ValueError):
            pass
        try:
            return int(datetime.strptime(os.path.basename(directory.rstrip("/")),
                                         "%Y%m%d-%H%M%S").timestamp())
        except ValueError:
            pass
    return int(time.time())
