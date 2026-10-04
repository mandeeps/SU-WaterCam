"""Tests for the reading helpers in tools/power_load_test.sh, against a fake I2C bus."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "power_load_test.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _fake_bus(tmp_path, body):
    fake = tmp_path / "i2cget"
    fake.write_text("#!/bin/sh\n# args: -y 1 0x08 <reg>\n" + body)
    fake.chmod(0o755)
    return fake


def _run(fake, expr):
    out = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; {expr}'],
                         env={**os.environ, "I2CGET": str(fake)},
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_good_reading(tmp_path):
    fake = _fake_bus(tmp_path, 'case "$4" in 1) echo 0x04;; 2) echo 0x07;; esac\n')
    assert _run(fake, "reading 1") == "4.07"


@pytest.mark.parametrize("failure", ["exit 1", "echo Error: Read failed", "echo"])
def test_failed_read_is_empty_not_zero(tmp_path, failure):
    # A failed read used to print 0.00, which wrecked Vin min and the mV/A fit.
    fake = _fake_bus(tmp_path, f'case "$4" in 1) echo 0x04;; 2) {failure};; esac\n')
    assert _run(fake, "reading 1") == ""


def test_torn_read_is_retried(tmp_path):
    # First integer read says 4, the re-read says 5: retry, then a stable 5.00.
    state = tmp_path / "n"
    fake = _fake_bus(tmp_path, (
        f'n=$(cat {state} 2>/dev/null || echo 0); echo $((n+1)) > {state}\n'
        'case "$4" in 3) [ "$n" -eq 0 ] && echo 0x04 || echo 0x05;; 4) echo 0x00;; esac\n'))
    assert _run(fake, "reading 3") == "5.00"


def test_unwritable_output_fails_fast(tmp_path):
    out = subprocess.run(["bash", str(SCRIPT), "-o", str(tmp_path / "missing" / "x.csv")],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 2
    assert "Cannot write" in out.stderr
