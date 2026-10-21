"""Emergency mode's hardware backstop (#209): instead of clearing the Witty Pi's
shutdown alarm, emergency mode moves it to an hour after its own end, and sets the
startup alarm shortly after, so a run that hangs is still powered down and comes
back by itself."""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools import witty_pi_4 as wp
from tools import wittypi_control as wc

H = 3600
NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def fresh():
    wc._backstop_armed_for['deadline'] = None
    yield
    wc._backstop_armed_for['deadline'] = None


def test_deadline_is_the_emergency_end_plus_the_margin():
    assert wc.emergency_backstop_deadline(NOW - 2 * H, 24, now=NOW) == NOW - 2 * H + 24 * H + wc.EMERGENCY_BACKSTOP_MARGIN_S


@pytest.mark.parametrize("since", [None, "x", NOW - 30 * H, NOW + 5 * H])
def test_a_missing_stale_or_future_start_counts_from_now(since):
    # an earlier emergency's start (already ended) would put the alarm in the past
    assert wc.emergency_backstop_deadline(since, 24, now=NOW) == NOW + 24 * H + wc.EMERGENCY_BACKSTOP_MARGIN_S


def test_no_time_limit_means_no_backstop_and_bad_hours_mean_24():
    assert wc.emergency_backstop_deadline(NOW, 0, now=NOW) is None
    assert wc.emergency_backstop_deadline(NOW, "?", now=NOW) == NOW + 24 * H + wc.EMERGENCY_BACKSTOP_MARGIN_S


def test_arm_sets_both_alarms_once_and_moves_them_on_a_resend():
    clock = [NOW]
    with patch.object(wc, "set_shutdown_at", return_value=True) as down, \
         patch.object(wc, "set_startup_at", return_value=True) as up, \
         patch("time.time", side_effect=lambda: clock[0]):
        d1 = wc.arm_emergency_backstop(NOW, 24)
        clock[0] += 120
        wc.arm_emergency_backstop(NOW, 24)              # next iteration: no new writes
        assert down.call_count == 1 and up.call_count == 1
        assert down.call_args.args[0] == d1
        assert up.call_args.args[0] == d1 + wc.EMERGENCY_BACKSTOP_RESTART_S
        clock[0] = NOW + 2 * H                          # two hours on, a resend restarts the duration
        d2 = wc.arm_emergency_backstop(NOW + 2 * H, 24)
    assert d2 == d1 + 2 * H and down.call_count == 2 and down.call_args.args[0] == d2


def test_a_failed_write_is_retried_next_iteration():
    with patch.object(wc, "set_shutdown_at", side_effect=[False, True]) as down, \
         patch.object(wc, "set_startup_at", return_value=True), \
         patch("time.time", return_value=NOW):
        wc.arm_emergency_backstop(NOW, 24)
        wc.arm_emergency_backstop(NOW, 24)
    assert down.call_count == 2


def test_no_time_limit_clears_the_shutdown_alarm_once():
    with patch.object(wc, "clear_shutdown_time") as clear, patch.object(wc, "set_shutdown_at") as down:
        assert wc.arm_emergency_backstop(NOW, 0) is None
        wc.arm_emergency_backstop(NOW, 0)
    assert clear.call_count == 1 and not down.called


def test_emergency_on_sets_the_backstop_instead_of_clearing():
    params = {"emergency_since": NOW - 60, "emergency_max_hours": 12}
    with patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d=None: params.get(k, d)), \
         patch.object(wc, "set_shutdown_at", return_value=True) as down, \
         patch.object(wc, "set_startup_at", return_value=True), \
         patch.object(wc, "clear_shutdown_time") as clear, \
         patch("time.time", return_value=NOW):
        result = wc.apply_emergency_schedule(True)
    assert result["action"] == "shutdown_backstop_set"
    assert down.call_args.args[0] == NOW - 60 + 12 * H + wc.EMERGENCY_BACKSTOP_MARGIN_S
    clear.assert_not_called()


def test_emergency_off_forgets_the_backstop():
    wc._backstop_armed_for['deadline'] = NOW
    with patch.object(wc, "installed_schedule_exists", return_value=True), \
         patch.object(wc, "rearm_schedule", return_value="2026-10-22 09:00:00"):
        assert wc.apply_emergency_schedule(False)["action"] == "schedule_rearmed"
    assert wc._backstop_armed_for['deadline'] is None


def test_witty_pi_alarm_commands():
    w = wp.WittyPi4()
    with patch.object(w, "run_command", return_value="") as run:
        assert w.set_shutdown_time(22, 9, 5, 0) and w.set_startup_time(22, 9, 15, 0)
    assert [c.args[0] for c in run.call_args_list] == ["set_shutdown_time 22 9 5 0", "set_startup_time 22 9 15 0"]
    with patch.object(w, "run_command", return_value="ERROR"):
        assert w.set_shutdown_time(1, 0, 0, 0) is False
    with pytest.raises(ValueError):
        w.set_shutdown_time(0, 25, 0, 0)


def test_alarm_parts_are_local_time():
    t = time.localtime(NOW)
    assert wc._alarm_parts(NOW) == (t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def test_each_emergency_iteration_rearms_the_backstop():
    src = (Path(__file__).resolve().parent.parent / "ticktalk_main.py").read_text()
    body = src[src.index("    if emergency_mode:\n        # Keep the hardware backstop"):]
    body = body[:body.index('return "emergency_mode_active"')]
    assert "arm_emergency_backstop(emergency_since, emergency_max_hours)" in body
