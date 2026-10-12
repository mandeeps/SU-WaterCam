"""Tests for tools/ensure_next_wake.py (arm the next schedule slot at shutdown)."""

import os
import sys
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.ensure_next_wake as nw

# The module ensure_next_wake imported (as plain `recovery_boot`), so patches reach it.
rb = nw.rb


@pytest.fixture(autouse=True)
def syracuse_time():
    """The nodes run on America/New_York, and slot times depend on daylight saving."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ")
    else:
        os.environ["TZ"] = old
    time.tzset()


def ts(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()


def stamp(t):
    return f"{datetime.fromtimestamp(t):%Y-%m-%d %H:%M:%S}"


def bcd(n):
    return (n // 10) << 4 | (n % 10)


def regs(text):
    t = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return [bcd(t.second), bcd(t.minute), bcd(t.hour), bcd(t.day)]


# 006's bench schedule on 2026-10-12: five 15-minute slots two hours apart from
# BEGIN, then off overnight. BEGIN is in standard time, so in October the slots
# are 09:00, 11:00 ... 17:00.
BENCH_WPI = """\
BEGIN 2026-01-01 08:00:00
END   2035-01-01 23:59:59
""" + "ON    M15\nOFF   H1 M45\n" * 4 + "ON    M15\nOFF   H15 M45\n"


# ── what happened on 006 ───────────────────────────────────────────────────

def test_button_boot_between_slots_gets_the_next_slot_back():
    # Button at 15:38: runScript armed 2026-10-13 09:00, skipping 17:00, and
    # ticktalk shut down at 15:40.
    wake, why = nw.decide(ts("2026-10-12 15:40:00"), ts("2026-10-13 09:00:00"), BENCH_WPI)
    assert stamp(wake) == "2026-10-12 17:00:00"
    assert "skips" in why


def test_overnight_restore_boot_gets_the_morning_slot_back():
    # The 03:00 restore boot armed 11:00, skipping 09:00.
    wake, _ = nw.decide(ts("2026-10-12 03:02:00"), ts("2026-10-12 11:00:00"), BENCH_WPI)
    assert stamp(wake) == "2026-10-12 09:00:00"


def test_scheduled_wake_already_has_the_next_slot():
    # A normal 15:00 wake: runScript armed 17:00, which is right.
    wake, why = nw.decide(ts("2026-10-12 15:01:40"), ts("2026-10-12 17:00:00"), BENCH_WPI)
    assert wake is None
    assert "left as it is" in why


def test_last_slot_of_the_day_leads_to_tomorrow_morning():
    wake, _ = nw.decide(ts("2026-10-12 17:01:40"), ts("2026-10-13 09:00:00"), BENCH_WPI)
    assert wake is None


# ── the other cases ─────────────────────────────────────────────────────────

def test_nothing_armed_arms_the_next_slot():
    wake, why = nw.decide(ts("2026-10-12 12:30:00"), None, BENCH_WPI)
    assert stamp(wake) == "2026-10-12 13:00:00"
    assert "no startup armed" in why


def test_past_alarm_is_replaced():
    wake, why = nw.decide(ts("2026-10-12 15:40:00"), ts("2026-10-12 15:00:00"), BENCH_WPI)
    assert stamp(wake) == "2026-10-12 17:00:00"
    assert "past" in why


def test_slot_too_close_to_shutdown_is_skipped():
    # The Pi has to be off before the alarm, so 17:00 is too soon at 16:59.
    wake, _ = nw.decide(ts("2026-10-12 16:59:00"), None, BENCH_WPI)
    assert stamp(wake) == "2026-10-13 09:00:00"


def test_an_earlier_wake_is_never_moved_later():
    # Emergency or remote-debug arming sooner than the schedule stays as it is.
    wake, _ = nw.decide(ts("2026-10-12 15:40:00"), ts("2026-10-12 16:10:00"), BENCH_WPI)
    assert wake is None


@pytest.mark.parametrize("text", [None, "", "BEGIN 2026-01-01 08:00:00\nEND 2035-01-01 00:00:00\nON WAIT\nOFF M30\n"])
def test_no_usable_schedule_leaves_the_alarm_alone(text):
    wake, why = nw.decide(ts("2026-10-12 15:40:00"), None, text)
    assert wake is None
    assert "no usable schedule" in why


def test_ended_schedule_leaves_the_alarm_alone():
    ended = "BEGIN 2020-01-01 08:00:00\nEND 2021-01-01 00:00:00\nON M15\nOFF H1 M45\n"
    wake, why = nw.decide(ts("2026-10-12 15:40:00"), None, ended)
    assert wake is None
    assert "no slot left" in why


# ── reading the alarm registers ────────────────────────────────────────────

def test_reads_an_alarm_for_tomorrow():
    now = ts("2026-10-12 15:40:00")
    assert stamp(nw.armed_startup(regs("2026-10-13 09:00:00"), now)) == "2026-10-13 09:00:00"


def test_reads_an_alarm_that_has_passed_as_past():
    now = ts("2026-10-12 15:40:00")
    assert stamp(nw.armed_startup(regs("2026-10-12 15:00:00"), now)) == "2026-10-12 15:00:00"


def test_reads_an_alarm_in_the_next_month():
    now = ts("2026-10-31 20:00:00")
    assert stamp(nw.armed_startup(regs("2026-11-01 09:00:00"), now)) == "2026-11-01 09:00:00"


def test_day_31_skips_short_months():
    now = ts("2026-11-15 12:00:00")
    assert stamp(nw.armed_startup(regs("2026-12-31 09:00:00"), now)) == "2026-12-31 09:00:00"


@pytest.mark.parametrize("raw", [[0, 0, 0, 0], [0x00, 0x00, 0x25, 0x01], [0x00, 0x00, 0x09, 0x00]])
def test_unset_or_impossible_alarm_reads_as_none(raw):
    assert nw.armed_startup(raw, ts("2026-10-12 15:40:00")) is None


# ── main(): the I/O around decide() ────────────────────────────────────────

@pytest.fixture
def node(tmp_path, monkeypatch):
    """A fake WittyPi: RTC, ALARM1 registers and wittyPi.log."""
    (tmp_path / "schedule.wpi").write_text(BENCH_WPI)
    (tmp_path / "wittyPi.log").write_text("")
    state = {"now": ts("2026-10-12 15:40:00"), "regs": regs("2026-10-13 09:00:00"), "armed": []}
    monkeypatch.setattr(rb, "WITTYPI_DIR", str(tmp_path))
    monkeypatch.setattr(rb, "RECOVERY_MARK", str(tmp_path / "recovery-mark"))
    monkeypatch.setattr(rb, "rtc_now", lambda: state["now"])
    monkeypatch.setattr(rb, "i2c_get", lambda reg: state["regs"][rb.ALARM1_REGS.index(reg)])
    monkeypatch.setattr(rb, "arm_startup", lambda wake: state["armed"].append(wake))
    state["dir"] = tmp_path
    return state


def test_main_arms_the_skipped_slot_and_logs_it(node):
    assert nw.main([]) == 0
    assert [stamp(w) for w in node["armed"]] == ["2026-10-12 17:00:00"]
    log = (node["dir"] / "wittyPi.log").read_text()
    assert "Shutdown check:" in log
    # The next boot's brownout check reads this line.
    assert rb.previously_armed_startup(log) == ts("2026-10-12 17:00:00")


def test_main_leaves_a_recovery_wake_alone(node):
    (node["dir"] / "recovery-mark").write_text("2026-10-13 09:00:00\n")
    assert nw.main([]) == 0
    assert node["armed"] == []


def test_main_dry_run_changes_nothing(node):
    assert nw.main(["--dry-run"]) == 0
    assert node["armed"] == []
    assert (node["dir"] / "wittyPi.log").read_text() == ""


def test_main_without_a_schedule_changes_nothing(node):
    (node["dir"] / "schedule.wpi").unlink()
    assert nw.main([]) == 0
    assert node["armed"] == []


def test_main_survives_an_unreadable_wittypi(node, monkeypatch):
    def broken(reg):
        raise OSError("i2c")
    monkeypatch.setattr(rb, "i2c_get", broken)
    assert nw.main([]) == 0
    assert node["armed"] == []
