"""Sleeping between emergency captures (tools/emergency_sleep.py, call_shutdown)."""
import time
from unittest.mock import MagicMock, patch

import pytest

from tools import config_io
from tools import emergency_sleep as es

NOW = 1_800_000_000.0


def params(**over):
    p = {"emergency_frequency": 10, "emergency_sleep": True, "emergency_mode": True}
    p.update(over)
    return lambda k, d=None: p.get(k, d)


# ── plan ─────────────────────────────────────────────────────────────────────

def test_next_capture_is_one_interval_after_boot():
    wake, why = es.plan(NOW, NOW - 240, 10)          # booted 4 min ago
    assert wake == NOW - 240 + 600 + 1
    assert why.startswith("next capture at")


def test_a_long_wake_still_sleeps_the_minimum():
    wake, _ = es.plan(NOW, NOW - 590, 10)            # 9 min 50 s into a 10-min interval
    assert wake >= NOW + es.SLEEP_MIN_OFF_S


@pytest.mark.parametrize("kw, reason", [
    ({"enabled": False}, "emergency_sleep is off"),
    ({"hold": True}, "remote access session"),
])
def test_reasons_to_stay_up(kw, reason):
    wake, why = es.plan(NOW, NOW - 60, 10, **kw)
    assert wake is None and reason in why


@pytest.mark.parametrize("interval", [1, 2, "x", None])
def test_short_or_bad_intervals_stay_up(interval):
    assert es.plan(NOW, NOW - 60, interval)[0] is None


def test_no_boot_time_counts_from_now():
    assert es.plan(NOW, None, 10)[0] == NOW + 600 + 1


# ── arm ──────────────────────────────────────────────────────────────────────

def test_arm_checks_the_alarm_reads_back():
    assert es.arm(NOW + 600, set_startup=lambda w: True, read_back=lambda now: NOW + 600)
    assert not es.arm(NOW + 600, set_startup=lambda w: True, read_back=lambda now: NOW + 660)
    assert not es.arm(NOW + 600, set_startup=lambda w: True, read_back=lambda now: None)
    assert not es.arm(NOW + 600, set_startup=lambda w: False, read_back=lambda now: NOW + 600)


def test_arm_failure_is_not_raised():
    def boom(w):
        raise OSError("i2c")
    assert not es.arm(NOW + 600, set_startup=boom, read_back=lambda now: NOW + 600)


# ── prepare ──────────────────────────────────────────────────────────────────

def _prepare(get_param, armed=True, remote=(False, False), check=None):
    remote_calls = iter(remote)
    arm = MagicMock(return_value=armed)
    out = es.prepare(get_param=get_param, downlink_check=check or (lambda: {}),
                     remote_active=lambda: next(remote_calls), arm_fn=arm,
                     clock=lambda: NOW, boot=NOW - 200)
    return out, arm


def test_prepare_arms_and_sleeps():
    out, arm = _prepare(params())
    assert out["action"] == "sleep" and out["wake"] == NOW - 200 + 600 + 1
    arm.assert_called_once_with(out["wake"])


def test_commands_are_picked_up_before_sleeping():
    check = MagicMock(return_value={})
    _prepare(params(), check=check)
    check.assert_called_once()


def test_emergency_turned_off_by_a_command_goes_back_to_the_count():
    p = {"emergency_frequency": 10, "emergency_sleep": True, "emergency_mode": True}

    def check():
        p["emergency_mode"] = False
        return {}
    out, arm = _prepare(lambda k, d=None: p.get(k, d), check=check)
    assert out["action"] == "continue"
    arm.assert_not_called()


def test_a_remote_session_started_by_a_command_keeps_it_up():
    out, arm = _prepare(params(), remote=(False, True))
    assert out["action"] == "stay"
    arm.assert_not_called()


def test_an_alarm_that_will_not_arm_keeps_it_up():
    out, _ = _prepare(params(), armed=False)
    assert out["action"] == "stay" and "could not be armed" in out["why"]


def test_sleep_off_or_short_interval_stays_without_polling():
    check = MagicMock()
    for p in (params(emergency_sleep=False), params(emergency_frequency=2)):
        out, arm = _prepare(p, check=check)
        assert out["action"] == "stay"
        arm.assert_not_called()
    check.assert_not_called()


# ── backstop at boot ─────────────────────────────────────────────────────────

def test_backstop_rearmed_at_boot_in_emergency_only():
    backstop = MagicMock(return_value=NOW + 3600)
    assert es.rearm_backstop_at_boot(params(emergency_mode=False), backstop) is None
    backstop.assert_not_called()
    p = params(emergency_since=NOW - 100, emergency_max_hours=24)
    assert "backstop" in es.rearm_backstop_at_boot(p, backstop)
    backstop.assert_called_once_with(NOW - 100, 24)


def test_backstop_without_time_limit():
    assert "no time limit" in es.rearm_backstop_at_boot(params(), lambda since, hours: None)


# ── call_shutdown ────────────────────────────────────────────────────────────

@pytest.fixture
def emergency_shutdown(tmp_path):
    import ticktalk_main as tm
    import tools.lora_runtime_integration as lri
    from tools.lora_runtime_integration import LoRaRuntimeManager
    p = tmp_path / "runtime_config.json"
    config_io.write_json(str(p), {"iteration_count": 0, "shutdown_iteration_limit": 3,
                                  "auto_shutdown_enabled": True, "emergency_mode": True,
                                  "emergency_since": time.time() - 600, "emergency_max_hours": 24})
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        lri._runtime_manager = LoRaRuntimeManager(config_file=str(p))

    def run(decision):
        calls = MagicMock(return_value=0)
        with patch("tools.wittypi_control.arm_emergency_backstop"), \
                patch("tools.emergency_sleep.prepare", return_value=decision), \
                patch("subprocess.call", calls), \
                patch("sys.exit", side_effect=SystemExit):
            try:
                result = tm.call_shutdown.__wrapped__(None)
            except SystemExit:
                result = "shut down"
        return result, calls
    yield run
    lri._runtime_manager = None


def test_emergency_sleep_shuts_down_on_the_first_iteration(emergency_shutdown):
    result, calls = emergency_shutdown({"action": "sleep", "why": "next capture at 12:10:00", "wake": NOW})
    assert result == "shut down"
    assert calls.call_args.args[0][0] == "doas"


def test_emergency_stay_keeps_capturing(emergency_shutdown):
    result, calls = emergency_shutdown({"action": "stay", "why": "a remote access session is active"})
    assert result == "emergency_mode_active"
    calls.assert_not_called()


def test_emergency_ended_by_command_continues(emergency_shutdown):
    result, calls = emergency_shutdown({"action": "continue", "why": "emergency mode was turned off"})
    assert result == "continue"
    calls.assert_not_called()
