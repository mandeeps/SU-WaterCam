"""tools/wittypi_patch.py: skip the Witty Pi daemon's redundant RTC-to-system step."""
import os
import shutil
import subprocess

import pytest

from tools import wittypi_patch as wp

BEFORE = "#!/bin/bash\n\nlog()\n{\n  echo \"$1\"\n}\n\n"
AFTER = "\nsystem_to_rtc()\n{\n  :\n}\n"


@pytest.fixture
def util(tmp_path):
    p = tmp_path / "utilities.sh"
    p.write_text(BEFORE + wp.UPSTREAM + AFTER)
    os.chmod(p, 0o755)
    return str(p)


def test_states(util):
    assert wp.state(open(util).read()) == "upstream"
    assert wp.state(BEFORE + wp.PATCHED + AFTER) == "patched"
    assert wp.state(BEFORE + AFTER) == "unknown"
    assert wp.state(BEFORE + wp.UPSTREAM.replace("date -s", "date --set") + AFTER) == "unknown"


def test_apply_keeps_the_rest_and_a_backup(util):
    assert wp.apply(util) == 0
    text = open(util).read()
    assert text == BEFORE + wp.PATCHED + AFTER
    assert open(util + wp.BACKUP_SUFFIX).read() == BEFORE + wp.UPSTREAM + AFTER
    assert os.stat(util).st_mode & 0o777 == 0o755
    assert wp.apply(util) == 0                      # second run: already patched, unchanged
    assert open(util).read() == text


def test_unknown_file_is_left_alone(tmp_path):
    p = tmp_path / "utilities.sh"
    p.write_text(BEFORE + "rtc_to_system()\n{\n  something_else\n}\n")
    before = p.read_text()
    assert wp.apply(str(p)) == 1
    assert p.read_text() == before
    assert not os.path.exists(str(p) + wp.BACKUP_SUFFIX)


def test_revert(util):
    wp.apply(util)
    assert wp.revert(util) == 0
    assert open(util).read() == BEFORE + wp.UPSTREAM + AFTER
    assert wp.revert(util) == 0                     # not patched: nothing to do


def test_cli(util, capsys):
    assert wp.main(["--check", "--path", util]) == 0
    assert "upstream" in capsys.readouterr().out
    assert wp.main(["--apply", "--path", util]) == 0
    assert wp.main(["--check", "--path", util]) == 0
    assert "patched" in capsys.readouterr().out
    assert wp.main(["--check", "--path", util + ".missing"]) == 1


# The patched function itself, run in bash with the Witty Pi helpers stubbed

def _run(rtc_ts_expr: str):
    script = wp.PATCHED + r'''
log() { echo "LOG $1"; }
sudo() { echo "SUDO $*" >&2; }   # the real calls send stdout to /dev/null
get_rtc_timestamp() { %s; }
TIME_UNKNOWN=1
rtc_to_system
echo "TIME_UNKNOWN=$TIME_UNKNOWN"
''' % rtc_ts_expr
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_close_clock_is_not_set():
    out = _run("echo $(( $(date +%s) + 1 ))")     # RTC 1 s ahead: within 2 s
    assert "already matches RTC (1 s apart), not set" in out
    assert "SUDO" not in out
    assert "TIME_UNKNOWN=0" in out


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_far_clock_is_set_as_upstream():
    out = _run("echo $(( $(date +%s) - 3600 ))")  # an hour off: set it, as UUGear's code does
    assert "SUDO timedatectl set-ntp 0" in out
    assert "SUDO date -s @" in out
    assert "TIME_UNKNOWN=0" in out


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_unreadable_rtc_falls_through_to_upstream():
    out = _run("echo ''")                          # failed I2C read: behave as upstream
    assert "Writing RTC time to system" in out
    assert "SUDO date -s @" in out
