"""schedule.wpi: atomic writes and the fallback when none is installed."""
import os
import time
from unittest.mock import patch

import pytest

from tools import witty_pi_4 as wp
from tools import wittypi_control as wc

SCHED = "BEGIN\t2020-01-01 08:00:00\nEND\t2037-12-31 23:59:59\nON\tM15\nOFF\tH23 M45\n"


@pytest.fixture
def wdir(tmp_path, monkeypatch):
    d = tmp_path / "home" / "wittypi"
    d.mkdir(parents=True)
    monkeypatch.setattr(wp, "WITTYPI_DIRECTORY", str(d))
    monkeypatch.setattr(wp, "SCHEDULE_FILE_PATH", str(d / "schedule.wpi"))
    return d


def test_write_is_atomic_and_leaves_no_temp_files(wdir):
    target = wdir / "schedule.wpi"
    target.write_text("old")
    wp.write_schedule_file(SCHED)
    assert target.read_text() == SCHED
    assert os.listdir(wdir) == ["schedule.wpi"]


def test_failed_write_keeps_the_old_schedule(wdir):
    target = wdir / "schedule.wpi"
    target.write_text(SCHED)
    with patch.object(wp.os, "fsync", side_effect=OSError("disk gone")), pytest.raises(OSError):
        wp.write_schedule_file("BEGIN half")
    assert target.read_text() == SCHED
    assert os.listdir(wdir) == ["schedule.wpi"]


def test_generate_schedule_writes_through_the_atomic_writer(wdir):
    with patch.object(wp, "write_schedule_file", wraps=wp.write_schedule_file) as w:
        wp.WittyPi4.generate_schedule(8, 0, 30, 8)
        assert w.call_count == 1
        wp.WittyPi4.generate_schedule(8, 0, 30, 8)
        assert w.call_count == 1               # unchanged: not rewritten
    assert (wdir / "schedule.wpi").read_text().startswith("BEGIN\t2020-01-01 08:00:00")


def _write(p, text=SCHED, age_s=0):
    p.write_text(text)
    t = time.time() - age_s
    os.utime(p, (t, t))
    return str(p)


def test_fallback_order(wdir, tmp_path):
    home = wdir.parent
    preset = _write(tmp_path / "preset.wpi")
    _write(wdir / "schedule.wpi.test-20261015")                       # never used
    bak = _write(wdir / "schedule.wpi.bak-20261001", age_s=50)
    old_dep = _write(home / "schedule.wpi.deploy-20261001", age_s=100)
    new_dep = _write(wdir / "schedule.wpi.deploy-20261012", age_s=10)
    deploy = _write(wdir / "schedule.wpi.deploy")
    assert wp.fallback_schedule_sources(str(wdir), preset)[:5] == [deploy, new_dep, old_dep, bak, preset]
    assert not any(os.path.basename(p).startswith("schedule.wpi.test") for p in wp.fallback_schedule_sources(str(wdir), preset))


def test_install_skips_unusable_candidates_and_uses_the_preset_last(wdir, tmp_path):
    preset = _write(tmp_path / "preset.wpi", "# preset\n" + SCHED)
    _write(wdir / "schedule.wpi.deploy", "")                          # empty: skipped
    _write(wdir / "schedule.wpi.bak-1", "garbage\n")                  # no BEGIN/END: skipped
    assert wp.install_fallback_schedule(str(wdir), preset) == preset
    assert (wdir / "schedule.wpi").read_text() == "# preset\n" + SCHED
    deploy = _write(wdir / "schedule.wpi.deploy")
    assert wp.install_fallback_schedule(str(wdir), preset) == deploy
    assert wp.install_fallback_schedule(str(wdir), str(tmp_path / "missing.wpi")) == deploy


def test_repo_default_preset_is_usable():
    assert os.path.exists(wp.DEFAULT_SCHEDULE) and wp._usable_schedule(wp.DEFAULT_SCHEDULE)


def test_emergency_off_without_schedule_installs_fallback_not_generated(wdir):
    _write(wdir / "schedule.wpi.deploy")
    with patch.object(wc, "rearm_schedule", return_value="2026-10-17 09:00:00") as rearm, \
         patch.object(wc, "set_schedule") as generate:
        result = wc.apply_emergency_schedule(False)
    assert result["action"] == "schedule_installed"
    assert (wdir / "schedule.wpi").read_text() == SCHED
    rearm.assert_called_once()
    generate.assert_not_called()


def test_emergency_off_generates_only_when_nothing_can_be_installed(wdir):
    with patch.object(wc, "install_fallback_schedule", return_value=None), \
         patch.object(wc, "set_schedule", return_value="next") as generate, \
         patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d=None: d):
        result = wc.apply_emergency_schedule(False)
    assert result["action"] == "schedule_regenerated"
    generate.assert_called_once()
