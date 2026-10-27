"""USB power for the cellular modem (and its GPS) and the USB microphone.

USB is off unless something needs it: the modem draws 0.6-0.9 W whenever it has
power, even idle, and the microphone about 0.1 W (docs/POWER_ANALYSIS.md).
usb-power-off.service turns it off about 5 s into boot and nothing turns it on
by default. A user takes it with acquire(name) and gives it back with
release(name); the last release turns it off. The users are:

- "gps": one live fix a day, on a knock and on request (tools/gps_window.py);
- "cellular": a cellular upload or remote session (tools/transmit_ip.py,
  tools/remote_debug.py);
- "audio": recording, when audio_recording_enabled (ticktalk_main.py).

Holders are files in HOLDERS_DIR (/tmp, cleared at boot), so processes share
them. Turning USB on after early boot keeps the modem's start-up current away
from the kernel and early userspace.

A Pi 4's four USB ports share one power switch, so the modem and microphone
are always on or off together. uhubctl needs root: pi runs it through doas
with the four rules in config/doas.conf.
"""
import os
import glob
import subprocess
import time

UHUBCTL = "/usr/sbin/uhubctl"
# The VL805's USB 2 hub and the USB 3 root hub; both are switched to keep
# their status consistent, though one switch powers all four ports.
HUBS = ("1-1", "2")

HOLDERS_DIR = "/tmp/watercam-usb"

_powered = False


def _switch(action: str) -> bool:
    ok = True
    for hub in HUBS:
        try:
            r = subprocess.run(["doas", UHUBCTL, "-l", hub, "-a", action],
                               capture_output=True, text=True, timeout=10)
            ok = ok and r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    return ok


def usb_power_on() -> bool:
    """Turn USB power on. Once per process; returns False if uhubctl or its
    doas rule is missing, which is harmless where USB was never turned off."""
    global _powered
    if _powered:
        return True
    _powered = _switch("on")
    return _powered


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:      # another user's process (root's remote session): alive
        return True
    except (OSError, ValueError):
        return False


def holders() -> list:
    """Names holding USB; a holder whose process has ended (it never released,
    e.g. ticktalk stopped mid-wake) no longer counts and is removed."""
    try:
        names = sorted(os.listdir(HOLDERS_DIR))
    except OSError:
        return []
    live = []
    for name in names:
        path = os.path.join(HOLDERS_DIR, name)
        try:
            with open(path) as f:
                pid = int(f.read().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and _alive(pid):
            live.append(name)
        else:
            try:
                os.unlink(path)
            except OSError:
                pass
    return live


def acquire(name: str) -> bool:
    """USB on for `name` until release(name); True if USB is (now) on."""
    try:
        os.makedirs(HOLDERS_DIR, exist_ok=True)
        try:
            os.chmod(HOLDERS_DIR, 0o1777)      # root (remote session) and pi share it
        except OSError:
            pass
        with open(os.path.join(HOLDERS_DIR, name), "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        print(f"⚠️ USB holder {name} not recorded: {e}")
    return usb_power_on()


def release(name: str) -> bool:
    """Give USB back; the last holder turns it off. True if USB went off."""
    global _powered
    try:
        os.unlink(os.path.join(HOLDERS_DIR, name))
    except OSError:
        pass
    if holders():
        return False
    # Powering off removes the modem's interface and its byte counters with it:
    # count them first (tools/cellular_usage.py).
    try:
        from tools.cellular_usage import sample
        sample()
    except Exception as e:               # noqa: BLE001 - never block the power-off
        print(f"⚠️ cellular usage not sampled before USB off: {e}")
    off = _switch("off")
    _powered = False
    if off:
        print(f"🔌 USB off ({name} was the last user)")
    return off


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
