"""tools/rtc_early_clock.py: which RTC readings may set the clock at boot."""
from datetime import datetime
from unittest.mock import patch

import pytest

from tools import rtc_early_clock as rec


def _bcd(v):
    return (v // 10) << 4 | v % 10


def regs(dt, weekday=3, os_flag=False):
    return [_bcd(dt.second) | (0x80 if os_flag else 0), _bcd(dt.minute), _bcd(dt.hour),
            _bcd(dt.day), weekday, _bcd(dt.month), _bcd(dt.year - 2000)]


T = datetime(2026, 10, 16, 18, 0, 15)


def test_decode_valid():
    assert rec.decode(regs(T)) == T


@pytest.mark.parametrize("weekday", range(8))
def test_decode_accepts_every_weekday_convention(weekday):
    # `date +%u` (the Witty Pi scripts, watercam-set-clock) writes Sunday as 7:
    # rejecting it made the RTC "unreadable" every Sunday
    assert rec.decode(regs(T, weekday=weekday)) == T


@pytest.mark.parametrize("bad", [
    regs(T, os_flag=True),                      # oscillator stopped
    regs(T)[:6] + [None],                       # a failed read
    regs(T)[:6],                                # too few
    [0x5A, 0x00, 0x00, 0x01, 0, 0x01, 0x26],    # 0x5A: not BCD
    [0x00, 0x00, 0x24, 0x01, 0, 0x01, 0x26],    # hour 24
    [0x00, 0x00, 0x00, 0x31, 0, 0x02, 0x26],    # 31 February
    [0x00, 0x00, 0x00, 0x00, 0, 0x01, 0x26],    # day 0
])
def test_decode_rejects(bad):
    assert rec.decode(bad) is None


def test_decide():
    rtc = T.timestamp()
    assert rec.decide(rtc, rtc - 28 * 60) == "set"                 # booted at the last shutdown's time
    assert rec.decide(None, rtc).startswith("skip")
    assert rec.decide(rtc, rtc).startswith("skip: system clock already")
    assert "never moved back" in rec.decide(rtc - 3600, rtc)
    dead_battery = datetime(2000, 1, 1).timestamp()
    assert "implausible" in rec.decide(dead_battery, dead_battery - 100)
    assert "implausible" in rec.decide(datetime(2040, 1, 1).timestamp(), rtc)


def test_main_sets_only_a_plausible_time(monkeypatch):
    monkeypatch.setattr(rec.os.path, "exists", lambda p: True)
    monkeypatch.setattr(rec, "read_registers", lambda: regs(T))
    monkeypatch.setattr(rec.time, "time", lambda: T.timestamp() - 1700)
    with patch.object(rec.time, "clock_settime") as settime:
        assert rec.main() == 0
    settime.assert_called_once_with(rec.time.CLOCK_REALTIME, T.timestamp())
    monkeypatch.setattr(rec, "read_registers", lambda: regs(datetime(2000, 1, 1)))
    with patch.object(rec.time, "clock_settime") as settime:
        assert rec.main() == 0
    settime.assert_not_called()


def test_read_straddling_a_tick_is_rejected(monkeypatch):
    reads = iter([regs(datetime(2026, 10, 16, 10, 59, 59)), regs(datetime(2026, 10, 16, 10, 0, 0))])
    monkeypatch.setattr(rec, "read_registers", lambda: next(reads))
    assert rec.rtc_timestamp() is None


@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr(rec, "READ_RETRY_S", 0.2)
    monkeypatch.setattr(rec, "READ_RETRY_INTERVAL_S", 0.01)


def test_no_wittypi_gives_up_after_the_retry_window(monkeypatch, fast_retry):
    calls = []
    monkeypatch.setattr(rec, "_i2cget", lambda r: calls.append(r))
    assert rec.read_registers() == [None] * 7
    assert len(calls) > 1 and set(calls) == {rec.REG_RTC_FIRST}   # retried the first register only


def test_a_wittypi_slow_to_answer_after_power_on_is_read(monkeypatch, fast_retry):
    # The first reads fail at once ("Read failed"), then it answers (seen on a unit at boot)
    answers = iter([None, None, None] + regs(T))
    monkeypatch.setattr(rec, "_i2cget", lambda r: next(answers))
    assert rec.decode(rec.read_registers()) == T


def test_the_retry_window_is_shared_by_all_registers(monkeypatch, fast_retry):
    # A register that never answers stops the read: the window isn't renewed per register
    calls = []

    def flaky(r):
        calls.append(r)
        return None if r == rec.REG_RTC_FIRST + 2 else 0x10
    monkeypatch.setattr(rec, "_i2cget", flaky)
    assert rec.read_registers()[2:] == [None] * 5
    assert max(calls) == rec.REG_RTC_FIRST + 2


def test_writes_no_time_source_marker():
    src = open(rec.__file__).read()
    assert "watercam-time-source" not in src and "mark_clock_verified" not in src
