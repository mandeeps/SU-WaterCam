"""Turning emergency mode off replaces the backstop's alarms (tools/wittypi_control.py)."""
from unittest.mock import MagicMock, patch

from tools import wittypi_control as wc
from tools.transmit_ip import apply_downlink_command, parse_downlink_frame


def test_with_a_schedule_the_schedule_is_rearmed(monkeypatch):
    monkeypatch.setattr(wc, "installed_schedule_exists", lambda: True)
    monkeypatch.setattr(wc, "rearm_schedule", lambda: "2026-10-23 08:00:00")
    assert wc.end_emergency_backstop() == "schedule re-armed, next startup 2026-10-23 08:00:00"


def test_without_a_schedule_both_alarms_are_cleared(monkeypatch):
    wp = MagicMock()
    monkeypatch.setattr(wc, "witty_pi_4", wp)
    monkeypatch.setattr(wc, "installed_schedule_exists", lambda: False)
    assert wc.end_emergency_backstop() == "alarms cleared (no schedule installed)"
    wp.clear_shutdown_time.assert_called_once()
    wp.clear_startup_time.assert_called_once()


def test_a_failure_never_raises(monkeypatch):
    monkeypatch.setattr(wc, "installed_schedule_exists", MagicMock(side_effect=OSError("i2c")))
    assert wc.end_emergency_backstop().startswith("backstop not replaced")


def test_99_99_ends_the_backstop():
    with patch("tools.wittypi_control.end_emergency_backstop", return_value="done") as end:
        out = apply_downlink_command({"parts": parse_downlink_frame(bytes.fromhex("9999"))},
                                     set_param_fn=lambda k, v: None)
    end.assert_called_once()
    assert out["applied"] == ["emergency_mode=False"]
