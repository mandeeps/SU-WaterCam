"""USB power for the cellular modem and the USB microphone.

usb-power-off.service turns USB off early in boot, so the modem's start-up
current doesn't add to the boot surge that can brown out a battery-powered
unit (docs/POWER_ANALYSIS.md). The wake cycle turns it back on when it starts
(record_audio in ticktalk_main.py), and cellular_up() and syncTime.sh do too
if they need the modem first. usb-power-on.timer turns it on 60 s after boot
whatever happens, so a unit whose cycle fails early stays reachable over
cellular.

A Pi 4's four USB ports share one power switch, so the modem and microphone
are always on or off together. uhubctl needs root: pi runs it through doas
with the two rules in config/doas.conf.
"""
import glob
import subprocess
import time

UHUBCTL = "/usr/sbin/uhubctl"
# The VL805's USB 2 hub and the USB 3 root hub; both are switched to keep
# their status consistent, though one switch powers all four ports.
HUBS = ("1-1", "2")

_powered = False


def usb_power_on() -> bool:
    """Turn USB power on. Once per process; returns False if uhubctl or its
    doas rule is missing, which is harmless where USB was never turned off."""
    global _powered
    if _powered:
        return True
    ok = True
    for hub in HUBS:
        try:
            r = subprocess.run(["doas", UHUBCTL, "-l", hub, "-a", "on"],
                               capture_output=True, text=True, timeout=10)
            ok = ok and r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    _powered = ok
    return ok


def wait_for(pattern: str, timeout_s: float) -> bool:
    """Wait up to timeout_s for a path matching the glob pattern to appear."""
    end = time.monotonic() + timeout_s
    while True:
        if glob.glob(pattern):
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.25)


def wait_for_usb_mic(timeout_s: float = 5) -> bool:
    """Wait for a USB audio capture device; it enumerates about 2 s after power on."""
    return wait_for("/sys/bus/usb/devices/*/*/sound/card*", timeout_s)
