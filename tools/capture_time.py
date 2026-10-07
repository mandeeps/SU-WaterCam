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


def write_capture_time(directory: str, when: Optional[float] = None) -> int:
    """Record the capture time (Unix seconds) in the capture directory and return it."""
    ts = int(when if when is not None else time.time())
    try:
        with open(os.path.join(directory, FILENAME), "w") as fh:
            fh.write(f"{ts}\n")
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
