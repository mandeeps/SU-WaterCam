"""tools/usb_power.py: USB on through doas, once per process."""
from unittest.mock import MagicMock, patch

import pytest

from tools import usb_power


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(usb_power, "_powered", False)


def test_turns_both_hubs_on_through_doas():
    with patch("tools.usb_power.subprocess.run", return_value=MagicMock(returncode=0)) as run:
        assert usb_power.usb_power_on() is True
        assert usb_power.usb_power_on() is True   # second call is free
    assert [c.args[0] for c in run.call_args_list] == [
        ["doas", "/usr/sbin/uhubctl", "-l", "1-1", "-a", "on"],
        ["doas", "/usr/sbin/uhubctl", "-l", "2", "-a", "on"],
    ]


def test_missing_doas_rule_or_uhubctl_is_harmless():
    with patch("tools.usb_power.subprocess.run", return_value=MagicMock(returncode=1)):
        assert usb_power.usb_power_on() is False
    with patch("tools.usb_power.subprocess.run", side_effect=FileNotFoundError("doas")):
        assert usb_power.usb_power_on() is False


def test_wait_for(tmp_path):
    (tmp_path / "card0").touch()
    assert usb_power.wait_for(str(tmp_path / "card*"), 0) is True
    assert usb_power.wait_for(str(tmp_path / "nope*"), 0) is False
