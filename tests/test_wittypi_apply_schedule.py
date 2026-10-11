"""witty_pi_4: no retries or clock syncs without a schedule, and an internet check
that reads the right way round."""
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.witty_pi_4 as wp  # noqa: E402


def test_no_schedule_means_no_retries_and_no_sync():
    w = wp.WittyPi4()
    with patch.object(wp.path, "exists", return_value=False), \
         patch.object(wp, "check_output") as run, \
         patch.object(wp.WittyPi4, "sync_time_with_network") as sync:
        assert w.apply_schedule() == "-"
    run.assert_not_called()
    sync.assert_not_called()


def test_applied_schedule_returns_next_startup():
    w = wp.WittyPi4()
    out = "header\nSchedule next shutdown at: 2026-10-11 08:28:00\nSchedule next startup at: 2026-10-12 08:00:00\n"
    with patch.object(wp.path, "exists", return_value=True), \
         patch.object(wp, "check_output", return_value=out):
        assert w.apply_schedule() == "2026-10-12 08:00:00"


def test_failed_apply_syncs_the_clock_once():
    w = wp.WittyPi4()
    with patch.object(wp.path, "exists", return_value=True), \
         patch.object(wp, "check_output", return_value="x\nSchedule script is interrupted\n"), \
         patch.object(wp.WittyPi4, "sync_time_with_network") as sync:
        assert w.apply_schedule(max_retries=5) == "-"
    sync.assert_called_once()


def test_has_internet_reads_the_exit_status():
    w = wp.WittyPi4()
    with patch.object(wp, "check_output", return_value=""):
        assert w.has_internet() is True              # exit 0, no output
    with patch.object(wp, "check_output", side_effect=subprocess.CalledProcessError(1, "x")):
        assert w.has_internet() is False


def test_sync_runs_only_with_internet():
    w = wp.WittyPi4()
    with patch.object(wp.WittyPi4, "has_internet", return_value=True), \
         patch.object(wp.WittyPi4, "run_command", return_value="ok") as run:
        w.sync_time_with_network()
    run.assert_called_once_with("net_to_system && system_to_rtc")
    with patch.object(wp.WittyPi4, "has_internet", return_value=False), \
         patch.object(wp.WittyPi4, "run_command") as run:
        w.sync_time_with_network()
    run.assert_not_called()
