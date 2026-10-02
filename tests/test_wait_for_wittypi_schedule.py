"""Tests for tools/wait_for_wittypi_schedule.py (ticktalk's wait for the next WittyPi wake)."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.wait_for_wittypi_schedule as ws

BOOT = datetime(2026, 10, 2, 18, 5, 8).timestamp()

PREVIOUS_BOOT = """\
[2026-10-02 16:00:41] System starts up because scheduled startup is due.
[2026-10-02 16:01:35] Schedule next shutdown at:  2026-10-02 16:15:00
[2026-10-02 16:01:35] Schedule next startup at:  2026-10-02 18:00:00
"""

THIS_BOOT_STARTED = """\
[xxxx-xx-xx xx:xx:xx] Witty Pi daemon (v4.21) is started.
[2026-10-02 18:05:19] System starts up because scheduled startup is due.
"""


def test_finds_this_boots_startup_alarm():
    log = PREVIOUS_BOOT + THIS_BOOT_STARTED + (
        "[2026-10-02 18:05:24] Schedule next shutdown at:  2026-10-02 18:20:00\n"
        "[2026-10-02 18:05:24] Schedule next startup at:  2026-10-02 20:00:00\n")
    assert ws.find_schedule_result(log, BOOT) == "next startup armed for 2026-10-02 20:00:00"


def test_ignores_alarm_logged_by_an_earlier_boot():
    assert ws.find_schedule_result(PREVIOUS_BOOT + THIS_BOOT_STARTED, BOOT) is None


def test_missing_schedule_file_counts_as_ready():
    log = THIS_BOOT_STARTED + (
        '[2026-10-02 18:05:24] File "schedule.wpi" not found, skip running schedule script.\n')
    assert ws.find_schedule_result(log, BOOT) == "no schedule.wpi, nothing to arm"


def test_tolerates_null_bytes_from_an_earlier_power_cut():
    log = PREVIOUS_BOOT + "\x00" * 668 + THIS_BOOT_STARTED + (
        "[2026-10-02 18:05:24] Schedule next startup at:  2026-10-02 20:00:00\n")
    assert ws.find_schedule_result(log, BOOT) == "next startup armed for 2026-10-02 20:00:00"


def test_accepts_lines_stamped_when_time_is_uncertain():
    log = THIS_BOOT_STARTED + "<2026-10-02 18:05:24> Schedule next startup at:  2026-10-02 20:00:00\n"
    assert ws.find_schedule_result(log, BOOT) == "next startup armed for 2026-10-02 20:00:00"


def test_wait_returns_as_soon_as_the_alarm_is_logged(tmp_path, monkeypatch):
    log = tmp_path / "wittyPi.log"
    log.write_text(THIS_BOOT_STARTED)
    monkeypatch.setattr(ws, "boot_time", lambda: BOOT)
    polls = []

    def fake_sleep(_):
        polls.append(1)
        if len(polls) == 2:
            with open(log, "a") as f:
                f.write("[2026-10-02 18:05:24] Schedule next startup at:  2026-10-02 20:00:00\n")

    monkeypatch.setattr(ws.time, "sleep", fake_sleep)
    assert ws.wait_for_schedule(str(log), timeout_s=60) == "next startup armed for 2026-10-02 20:00:00"
    assert len(polls) == 2


def test_wait_gives_up_after_timeout(tmp_path, monkeypatch):
    log = tmp_path / "wittyPi.log"
    log.write_text(THIS_BOOT_STARTED)
    monkeypatch.setattr(ws, "boot_time", lambda: BOOT)
    assert ws.wait_for_schedule(str(log), timeout_s=0.05, poll_s=0.01) is None


def test_missing_log_file_never_blocks_forever(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "boot_time", lambda: BOOT)
    assert ws.wait_for_schedule(str(tmp_path / "absent.log"), timeout_s=0.05, poll_s=0.01) is None


def test_main_always_exits_zero(monkeypatch):
    monkeypatch.setattr(ws, "wait_for_schedule", lambda: None)
    assert ws.main() == 0
