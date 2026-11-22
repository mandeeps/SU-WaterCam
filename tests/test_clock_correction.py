"""tools/transmit_ip.py correct_clock: set the clock from the API's Date header."""
from email.utils import formatdate
from unittest.mock import MagicMock, patch

import pytest

from tools import transmit_ip


@pytest.fixture(autouse=True)
def fresh_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(transmit_ip, "TIME_SOURCE", str(tmp_path / "time-source"))
    monkeypatch.setattr(transmit_ip, "CLOCK_CHECKED", str(tmp_path / "clock-checked"))
    monkeypatch.setattr(transmit_ip, "_boot_id", lambda: "boot-1")
    return tmp_path


def _result(server_ts, rtt=0.4):
    return {"success": True, "server_date": formatdate(server_ts, usegmt=True), "rtt_s": rtt}


def test_small_offset_left_alone():
    with patch("tools.transmit_ip.os.path.exists", return_value=True), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run") as run:
        assert transmit_ip.correct_clock(_result(2_000_000_001)) is None    # 1.7 s: within 2 s
    run.assert_not_called()


def test_large_offset_corrected_through_the_helper():
    with patch("tools.transmit_ip.os.path.exists", side_effect=lambda p: p == transmit_ip.SET_CLOCK), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run", return_value=MagicMock(returncode=0)) as run:
        offset = transmit_ip.correct_clock(_result(2_000_003_600))      # an hour ahead (DST)
    assert abs(offset - 3600.7) < 0.01                                  # +0.5 floored second, +0.2 half rtt
    assert run.call_args[0][0] == ["doas", "/usr/local/sbin/watercam-set-clock", "2000003600.700"]


def test_no_helper_or_no_header():
    with patch("tools.transmit_ip.os.path.exists", return_value=False):
        assert transmit_ip.correct_clock(_result(2_000_003_600)) is None
    assert transmit_ip.correct_clock({"success": True}) is None
    with patch("tools.transmit_ip.os.path.exists", return_value=True):
        assert transmit_ip.correct_clock({"server_date": "not a date"}) is None


def test_helper_failure_reported_not_raised():
    with patch("tools.transmit_ip.os.path.exists", side_effect=lambda p: p == transmit_ip.SET_CLOCK), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run", return_value=MagicMock(returncode=1, stderr="no", stdout="")):
        assert transmit_ip.correct_clock(_result(2_000_003_600)) is None


def _run_check(server_ts=2_000_003_600):
    with patch("tools.transmit_ip.os.path.exists", side_effect=lambda p: p == transmit_ip.SET_CLOCK), \
         patch("tools.transmit_ip._time_source", return_value=_time_source[0]), \
         patch("tools.transmit_ip.time.time", return_value=2_000_000_000.0), \
         patch("tools.transmit_ip.subprocess.run", return_value=MagicMock(returncode=0)) as run:
        transmit_ip.correct_clock(_result(server_ts))
    return run.call_count


_time_source = [""]


def test_only_once_per_boot(monkeypatch):
    _time_source[0] = ""
    assert _run_check() == 1
    assert _run_check() == 0                       # same boot: not again
    monkeypatch.setattr(transmit_ip, "_boot_id", lambda: "boot-2")
    assert _run_check() == 1                       # next boot


def test_skipped_when_network_or_gps_set_the_time(monkeypatch):
    try:
        for i, source in enumerate(("network", "gps", "server")):
            monkeypatch.setattr(transmit_ip, "_boot_id", lambda i=i: f"boot-{i}")
            _time_source[0] = source
            assert _run_check() == 0
        monkeypatch.setattr(transmit_ip, "_boot_id", lambda: "boot-lora")
        _time_source[0] = "lora"
        assert _run_check() == 1                   # a clock from LoRa time is still checked
    finally:
        _time_source[0] = ""


def _correct(result, now=2_000_000_000.0, returncodes=(0,)):
    codes = iter(returncodes)
    with patch("tools.transmit_ip.os.path.exists", side_effect=lambda p: p == transmit_ip.SET_CLOCK), \
         patch("tools.transmit_ip.time.time", return_value=now), \
         patch("tools.transmit_ip.subprocess.run",
               side_effect=lambda *a, **k: MagicMock(returncode=next(codes), stderr="", stdout="")) as run, \
         patch("tools.capture_time.mark_clock_verified"):
        offset = transmit_ip.correct_clock(result)
    return offset, [c[0][0][2] for c in run.call_args_list]


def test_x_server_time_is_preferred_and_set_to_the_millisecond():
    offset, args = _correct({"server_time": "2000000003.250", "server_date": formatdate(2_000_000_009, usegmt=True),
                             "rtt_s": 0.5})
    assert offset == pytest.approx(3.5) and args == ["2000000003.500"]


def test_a_bad_x_server_time_falls_back_to_date():
    for bad in ("soon", "nan", "inf", ""):
        transmit_ip_reset()
        offset, _ = _correct({"server_time": bad, "server_date": formatdate(2_000_000_003, usegmt=True), "rtt_s": 0})
        assert offset == pytest.approx(3.5)


def transmit_ip_reset():
    import os
    try:
        os.unlink(transmit_ip.CLOCK_CHECKED)
    except OSError:
        pass


def test_within_2_s_by_x_server_time_is_left_alone():
    offset, args = _correct({"server_time": "2000000001.900", "rtt_s": 0.1})
    assert offset is None and args == []


def test_the_offset_is_from_when_the_reply_arrived():
    # The reply arrived 7 s before correct_clock ran: the offset is from then
    offset, args = _correct({"server_time": "2000000010.000", "received_at": 1_999_999_993.0, "rtt_s": 0},
                            now=2_000_000_000.0)
    assert offset == pytest.approx(17.0) and args == ["2000000017.000"]


def test_an_old_helper_gets_whole_seconds():
    offset, args = _correct({"server_time": "2000000010.600", "rtt_s": 0}, returncodes=(2, 0))
    assert offset == pytest.approx(10.6) and args == ["2000000010.600", "2000000011"]


def test_a_slow_round_trip_adds_at_most_the_return_leg_cap():
    # 5.75 s round trip (a fresh cellular link): the reply's way back counts as
    # RETURN_LEG_MAX_S, not half the round trip (docs/TIME_SYNC.md)
    offset, args = _correct({"server_time": "2000000010.000", "rtt_s": 5.75})
    assert offset == pytest.approx(10.25) and args == ["2000000010.250"]
    transmit_ip_reset()
    offset, args = _correct({"server_date": formatdate(2_000_000_010, usegmt=True), "rtt_s": 6.0})
    assert offset == pytest.approx(10.75)                     # Date: +0.5 floored second, +0.25


def test_a_fast_round_trip_still_adds_half_of_it():
    offset, args = _correct({"server_time": "2000000010.000", "rtt_s": 0.3})
    assert offset == pytest.approx(10.15) and args == ["2000000010.150"]


def test_a_slow_upload_does_not_move_a_clock_that_is_right():
    # The clock is 0.1 s behind the server; a 5.75 s round trip used to make
    # that look like 2.98 s, over the 2 s tolerance, and set the clock ahead
    offset, args = _correct({"server_time": "2000000000.100", "rtt_s": 5.75})
    assert offset is None and args == []
