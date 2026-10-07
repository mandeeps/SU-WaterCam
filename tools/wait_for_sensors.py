"""Wait until the capture hardware and daemons are up, before the first capture.

runrtm.py calls wait_for_sensors() before it starts the graph, and the capture
loop (get_time, TTStartOnArrival) then fires immediately instead of waiting for
the next period boundary. Each check is a device node or a daemon socket; a
daemon is only waited for if its unit is installed. Whatever isn't ready by the
timeout is reported and the capture goes ahead: each step already copes with a
missing sensor. GPS is not waited for: a fix can take minutes, and
get_location_with_retry handles it.
"""

import os
import time
from typing import Dict, List, Tuple

UNIT_DIR = "/etc/systemd/system"

# (name, path that must exist, unit that must be installed for it to count or None)
CHECKS: List[Tuple[str, str, object]] = [
    ("camera", "/dev/video0", None),
    ("lepton", "/dev/spidev0.0", None),
    ("i2c", "/dev/i2c-1", None),
    ("segformer daemon", "/run/segformer/segformer.sock", "segformer_daemon.service"),
    ("lora daemon", "/run/lora/lora.sock", "lora_daemon.service"),
]


def pending(checks=CHECKS, unit_dir: str = UNIT_DIR) -> List[str]:
    """Names of the checks that aren't satisfied yet."""
    out = []
    for name, path, unit in checks:
        if unit and not os.path.exists(os.path.join(unit_dir, unit)):
            continue
        if not os.path.exists(path):
            out.append(name)
    return out


def wait_for_sensors(timeout_s: float = 60, poll_s: float = 0.5, checks=CHECKS,
                     unit_dir: str = UNIT_DIR) -> Dict[str, object]:
    """Block until every check passes or timeout_s passes; never raises."""
    t0 = time.monotonic()
    missing = pending(checks, unit_dir)
    while missing and time.monotonic() - t0 < timeout_s:
        time.sleep(poll_s)
        missing = pending(checks, unit_dir)
    return {"ready": not missing, "missing": missing, "waited_s": round(time.monotonic() - t0, 1)}
