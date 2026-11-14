"""Guards and helpers for the bench-unit hardware tests (docs/BENCH_HARDWARE_TESTS.md).

These tests drive the real devices, so they refuse to run anywhere but a Raspberry
Pi that has been marked as a bench unit, and never while ticktalk runs (it owns the
camera, the Witty Pi and the IR-cut pin). They send no LoRa or IP traffic, and
anything they write goes into pytest's tmp_path.
"""
import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Made by hand, once, on each bench unit: `sudo touch /etc/watercam/bench-unit`.
# A deployed unit never has it, so a stray --hardware run there stops at once.
BENCH_FLAG = "/etc/watercam/bench-unit"
I2CGET = "/usr/sbin/i2cget"           # not on pi's PATH
I2C_BUS = "1"


def _on_a_pi() -> bool:
    try:
        with open("/proc/device-tree/model") as f:
            return "Raspberry Pi" in f.read()
    except OSError:
        return False


def _active(unit: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0


@pytest.fixture(scope="session", autouse=True)
def bench_unit():
    """Stop the whole run unless this is a bench unit that is free to test."""
    if not _on_a_pi():
        pytest.exit("--hardware runs only on a Raspberry Pi bench unit", returncode=4)
    if not os.path.exists(BENCH_FLAG):
        pytest.exit(f"not a bench unit: {BENCH_FLAG} is missing. Never run these on a "
                    "deployed unit; on a bench unit create it with `sudo touch "
                    f"{BENCH_FLAG}`", returncode=4)
    if _active("ticktalk.service"):
        pytest.exit("ticktalk is running and owns the camera, Witty Pi and IR-cut pin: "
                    "stop it first (sudo systemctl stop ticktalk)", returncode=4)
    return os.uname().nodename


def i2c_read(addr: int, reg=None):
    """One byte from an I2C register, or a plain read when reg is None (for chips
    without registers, like the AHT20). Read only; None if nothing answers."""
    cmd = [I2CGET, "-y", I2C_BUS, hex(addr)] + ([hex(reg)] if reg is not None else [])
    try:
        r = subprocess.run(cmd,
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip(), 16)
    except ValueError:
        return None


@pytest.fixture(scope="session")
def require_i2c_tools():
    if not os.path.exists(I2CGET):
        pytest.fail(f"{I2CGET} missing: install i2c-tools (FIELD_DEPLOYMENT_GUIDE.md 1.4)")


def need(condition: bool, what: str):
    """Skip when a device isn't fitted to this unit (skipped, not passed, so the
    report says what wasn't tested)."""
    if not condition:
        pytest.skip(f"not fitted on this unit: {what}")


def which(cmd: str) -> bool:
    return shutil.which(cmd) is not None
