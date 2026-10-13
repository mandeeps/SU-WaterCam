"""tools/transmit_ip.py correct_clock: set the clock from the API's Date header."""
from email.utils import formatdate
from unittest.mock import MagicMock, patch

from tools import transmit_ip


def _result(server_ts, rtt=0.4):
    return {"success": True, "server_date": formatdate(server_ts, usegmt=True), "rtt_s": rtt}


def test_small_offset_left_alone():
    with patch("tools.transmit_ip.os.path.exists", return_value=True), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run") as run:
        assert transmit_ip.correct_clock(_result(2_000_000_003)) is None
    run.assert_not_called()


def test_large_offset_corrected_through_the_helper():
    with patch("tools.transmit_ip.os.path.exists", return_value=True), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run", return_value=MagicMock(returncode=0)) as run:
        offset = transmit_ip.correct_clock(_result(2_000_003_600))      # an hour ahead (DST)
    assert abs(offset - 3600.2) < 0.01
    assert run.call_args[0][0] == ["doas", "/usr/local/sbin/watercam-set-clock", "2000003600"]


def test_no_helper_or_no_header():
    with patch("tools.transmit_ip.os.path.exists", return_value=False):
        assert transmit_ip.correct_clock(_result(2_000_003_600)) is None
    assert transmit_ip.correct_clock({"success": True}) is None
    with patch("tools.transmit_ip.os.path.exists", return_value=True):
        assert transmit_ip.correct_clock({"server_date": "not a date"}) is None


def test_helper_failure_reported_not_raised():
    with patch("tools.transmit_ip.os.path.exists", return_value=True), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run", return_value=MagicMock(returncode=1, stderr="no", stdout="")):
        assert transmit_ip.correct_clock(_result(2_000_003_600)) is None
