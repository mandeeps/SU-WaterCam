"""Tests for tools/recovery_boot.py (sleep after a power outage until the battery recharges)."""

import os
import sys
import time
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.recovery_boot as rb


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


SETTINGS = dict(rb.DEFAULTS)
POWER = 0x0A

# The field schedule: 15 min on every 2 h from 06:00 to 22:15, off overnight.
FIELD_WPI = """\
BEGIN 2020-01-01 06:00:00
END   2035-01-01 23:59:59
""" + "ON    M15\nOFF   H1 M45\n" * 8 + "ON    M15\nOFF   H7 M45\n"

LOG_BEFORE_OUTAGE = """\
[2026-09-14 21:00:17] System starts up because power supply is newly connected.
[2026-09-14 21:01:13] Schedule next shutdown at: 2026-09-14 21:15:00
[2026-09-14 21:01:14] Schedule next startup at:  2026-09-14 23:00:00
"""


# ── which boots are deferred ───────────────────────────────────────────────

def test_power_restore_long_after_the_armed_wake_is_deferred():
    defer, why = rb.decide(POWER, ts("2026-09-20 20:10:00"), LOG_BEFORE_OUTAGE, SETTINGS)
    assert defer
    assert "2026-09-14 23:00:00" in why


def test_power_restore_with_no_armed_wake_logged_is_deferred():
    log = LOG_BEFORE_OUTAGE.replace("Schedule next startup", "Something else")
    defer, why = rb.decide(POWER, ts("2026-09-20 20:10:00"), log, SETTINGS)
    assert defer
    assert "none logged" in why


@pytest.mark.parametrize("boot", ["2026-09-14 23:00:15", "2026-09-14 23:12:48", "2026-09-14 22:59:00"])
def test_brownout_at_a_scheduled_wake_runs_normally(boot):
    # The WittyPi browns out at the wake's surge and reports "newly connected".
    defer, why = rb.decide(POWER, ts(boot), LOG_BEFORE_OUTAGE, SETTINGS)
    assert not defer
    assert "browned out" in why


def test_power_restore_just_past_the_window_is_deferred():
    defer, _ = rb.decide(POWER, ts("2026-09-14 23:21:00"), LOG_BEFORE_OUTAGE, SETTINGS)
    assert defer


@pytest.mark.parametrize("reason", [0x01, 0x03, 0x08, 0x0B])
def test_alarm_button_and_reboot_boots_run_normally(reason):
    defer, _ = rb.decide(reason, ts("2026-09-20 20:10:00"), LOG_BEFORE_OUTAGE, SETTINGS)
    assert not defer


@pytest.mark.parametrize("reason", [0x05, 0x09])
def test_other_power_restore_reasons_are_deferred(reason):
    defer, _ = rb.decide(reason, ts("2026-09-20 20:10:00"), LOG_BEFORE_OUTAGE, SETTINGS)
    assert defer


def test_disabled_in_config_runs_normally():
    defer, why = rb.decide(POWER, ts("2026-09-20 20:10:00"), LOG_BEFORE_OUTAGE,
                           dict(SETTINGS, enabled=False))
    assert not defer
    assert "disabled" in why


def test_takes_the_armed_wake_from_earlier_boots_only():
    # decide() is handed only the lines before this boot's mark, so the last
    # armed line there is the previous boot's, whatever the clocks said.
    assert rb.previously_armed_startup(LOG_BEFORE_OUTAGE) == ts("2026-09-14 23:00:00")


def test_a_recovery_alarm_counts_as_the_armed_wake():
    log = LOG_BEFORE_OUTAGE + (
        "[2026-09-20 20:10:30] Recovery boot: power supply newly connected; shutting down.\n"
        "[2026-09-20 20:10:30] Schedule next startup at:  2026-09-21 06:00:00\n")
    defer, _ = rb.decide(POWER, ts("2026-09-21 06:00:20"), log, SETTINGS)
    assert not defer


def test_reads_lines_stamped_when_time_is_uncertain():
    log = "<2026-09-14 21:01:14> Schedule next startup at:  2026-09-14 23:00:00\n"
    assert rb.previously_armed_startup(log) == ts("2026-09-14 23:00:00")


# ── choosing the next wake ─────────────────────────────────────────────────

# runScript.sh steps through the schedule in epoch seconds from BEGIN, so a
# January BEGIN puts every slot an hour later on the clock during daylight
# saving time: 07:00, 09:00 ... 23:00 in September, as 006's field log shows.
# The recovery wake has to land on those same slots.
@pytest.mark.parametrize("now, expected", [
    ("2026-09-20 20:10:00", "2026-09-20 23:00:00"),   # 21:00 is too soon
    ("2026-09-20 21:30:00", "2026-09-21 07:00:00"),   # nothing left today 2 h out
    ("2026-09-21 05:30:00", "2026-09-21 09:00:00"),   # 07:00 is too soon
    ("2026-09-21 11:00:00", "2026-09-21 13:00:00"),   # exactly 2 h counts
    ("2026-12-21 05:30:00", "2026-12-21 08:00:00"),   # standard time: on the hour again
])
def test_next_field_schedule_slot_at_least_two_hours_out(now, expected):
    wake, source = rb.choose_wake(ts(now), SETTINGS, FIELD_WPI)
    assert datetime.fromtimestamp(wake) == datetime.strptime(expected, "%Y-%m-%d %H:%M:%S")
    assert "schedule" in source


def test_falls_back_to_fixed_delay_without_a_schedule():
    now = ts("2026-09-20 20:10:00")
    wake, source = rb.choose_wake(now, SETTINGS, None)
    assert wake == now + 120 * 60
    assert "fixed delay" in source


@pytest.mark.parametrize("wpi", [
    "BEGIN 2020-01-01 06:00:00\nEND   2026-01-01 00:00:00\nON M15\nOFF H1\n",   # ended
    "BEGIN 2020-01-01 06:00:00\nEND   2035-01-01 00:00:00\nON WAIT\nOFF H1\n",  # WAIT
    "BEGIN 2020-01-01 06:00:00\nEND   2035-01-01 00:00:00\nON M15\n",           # no OFF
    "END   2035-01-01 00:00:00\nON M15\nOFF H1\n",                              # no BEGIN
    "BEGIN 2020-01-01 06:00:00\nEND   2035-01-01 00:00:00\nON M15\nNAP H1\n",   # unknown
])
def test_unusable_schedules_fall_back_to_fixed_delay(wpi):
    now = ts("2026-09-20 20:10:00")
    wake, source = rb.choose_wake(now, SETTINGS, wpi)
    assert wake == now + 120 * 60
    assert "fixed delay" in source


def test_schedule_comments_and_future_begin():
    wpi = "# deploy\nBEGIN 2026-10-05 07:00:00  # first day\nEND 2035-01-01 00:00:00\nON M30\nOFF H23 M30\n"
    wake, _ = rb.choose_wake(ts("2026-10-02 12:00:00"), SETTINGS, wpi)
    assert datetime.fromtimestamp(wake) == datetime(2026, 10, 5, 7, 0, 0)


def test_rtc_now_decodes_the_wittypi_clock(monkeypatch):
    # 2026-10-02 20:06:00, with the seconds register's status bit set.
    regs = {58: 0x80, 59: 0x06, 60: 0x20, 61: 0x02, 62: 0x05, 63: 0x10, 64: 0x26}
    monkeypatch.setattr(rb, "i2c_get", lambda reg: regs[reg])
    assert rb.rtc_now() == ts("2026-10-02 20:06:00")


def test_bcd():
    assert [rb._bcd(n) for n in (0, 9, 10, 23, 31, 59)] == [0x00, 0x09, 0x10, 0x23, 0x31, 0x59]


def test_settings_merge_with_defaults(tmp_path):
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text('{"recovery_boot": {"min_delay_minutes": 5}}')
    s = rb.load_settings(str(cfg))
    assert s["min_delay_minutes"] == 5 and s["enabled"] is True
    assert rb.load_settings(str(tmp_path / "missing.json")) == rb.DEFAULTS


# ── main(): never strand the node ──────────────────────────────────────────

DAEMON_STARTING = """\
[xxxx-xx-xx xx:xx:xx] Witty Pi daemon (v4.21) is started.
[xxxx-xx-xx xx:xx:xx] Seems RTC has good time, write RTC time into system
[2026-09-20 20:10:18] System starts up because power supply is newly connected.
"""
DAEMON_READY = DAEMON_STARTING + """\
[2026-09-20 20:10:23] Send out the SYS_UP signal via GPIO-17 pin.
[2026-09-20 20:10:23] Pending for incoming shutdown command...
[2026-09-20 20:10:24] File "schedule.wpi" not found, skip running schedule script.
"""


@pytest.fixture
def node(tmp_path, monkeypatch):
    """A fake node: wittyPi.log, the boot mark, a WittyPi that reports a power restore."""
    monkeypatch.setattr(rb, "WITTYPI_DIR", str(tmp_path))
    mark = tmp_path / "wittypi-log-offset"
    monkeypatch.setattr(rb.ws, "BOOT_OFFSET_FILE", str(mark))
    monkeypatch.setattr(rb.ws.boot_offset, "__defaults__", (str(mark),))
    monkeypatch.setattr(rb, "load_settings", lambda: dict(SETTINGS))
    monkeypatch.setattr(rb, "i2c_get", lambda reg: POWER)
    monkeypatch.setattr(rb, "boot_time_from_rtc", lambda: ts("2026-09-20 20:10:08"))
    monkeypatch.setattr(rb, "rtc_now", lambda: ts("2026-09-20 20:10:30"))
    commands = []
    monkeypatch.setattr(rb.subprocess, "run", lambda *a, **k: commands.append(a[0]))
    monkeypatch.setattr(rb.time, "sleep", lambda s: None)
    armed = []
    monkeypatch.setattr(rb, "arm_startup", armed.append)

    def boot(this_boot_lines, mark_written=True):
        (tmp_path / "wittyPi.log").write_text(LOG_BEFORE_OUTAGE + this_boot_lines)
        if mark_written:
            mark.write_text(str(len(LOG_BEFORE_OUTAGE.encode())))
        return armed, commands
    return boot


def test_recovery_arms_a_wake_and_powers_off(node, tmp_path, capsys):
    armed, commands = node(DAEMON_READY)
    assert rb.main([]) == 0
    assert armed == [ts("2026-09-20 20:10:30") + 120 * 60]
    assert commands == [["systemctl", "poweroff"]]
    log = (tmp_path / "wittyPi.log").read_text()
    # Stamped from the RTC, and worded so the next boot finds the alarm.
    assert "[2026-09-20 20:10:30] Schedule next startup at:  2026-09-20 22:10:30" in log


def test_never_powers_off_before_the_daemon_sends_sys_up(node, monkeypatch, capsys):
    # 006 on 2026-10-02 20:29: powered off before SYS_UP, the WittyPi never cut
    # the power, and the node sat halted through its alarm.
    monkeypatch.setattr(rb.ws, "TIMEOUT_S", 0)
    monkeypatch.setattr(rb.wait_until_daemon_ready, "__defaults__", (0, 0))
    armed, commands = node(DAEMON_STARTING)
    assert rb.main([]) == 0
    assert armed == [] and commands == []
    assert "never reported ready" in capsys.readouterr().out


def test_a_previous_boots_ready_lines_do_not_count(node, monkeypatch, tmp_path):
    # 006 on 2026-10-02 20:29: with a stale clock, the previous boot's lines
    # looked like this boot's. Lines before the mark must never count.
    monkeypatch.setattr(rb.wait_until_daemon_ready, "__defaults__", (0, 0))
    armed, commands = node(DAEMON_STARTING)
    earlier = LOG_BEFORE_OUTAGE + DAEMON_READY.replace("2026-09-20", "2026-09-14")
    (tmp_path / "wittyPi.log").write_text(earlier + DAEMON_STARTING)
    (tmp_path / "wittypi-log-offset").write_text(str(len(earlier.encode())))
    assert rb.main([]) == 0
    assert armed == [] and commands == []


def test_without_the_boot_mark_runs_normally(node, capsys):
    armed, commands = node(DAEMON_READY, mark_written=False)
    assert rb.main([]) == 0
    assert armed == [] and commands == []
    assert "wittypi-boot-mark" in capsys.readouterr().out


def test_unreadable_reason_runs_normally(node, monkeypatch, capsys):
    node(DAEMON_READY)

    def boom(_):
        raise OSError("bus error")
    monkeypatch.setattr(rb, "i2c_get", boom)
    assert rb.main([]) == 0
    assert "normal cycle" in capsys.readouterr().out


def test_unreadable_rtc_runs_normally(node, monkeypatch, capsys):
    armed, commands = node(DAEMON_READY)

    def no_rtc():
        raise OSError("bus error")
    monkeypatch.setattr(rb, "boot_time_from_rtc", no_rtc)
    assert rb.main([]) == 0
    assert armed == [] and commands == []


def test_failed_alarm_write_does_not_shut_down(node, monkeypatch, capsys):
    _, commands = node(DAEMON_READY)

    def fail(_):
        raise RuntimeError("readback mismatch")
    monkeypatch.setattr(rb, "arm_startup", fail)
    assert rb.main([]) == 0
    assert commands == []
    assert "normal cycle instead" in capsys.readouterr().out


def test_dry_run_changes_nothing(node, capsys):
    armed, commands = node(DAEMON_READY)
    assert rb.main(["--dry-run"]) == 0
    assert armed == [] and commands == []
    assert "Dry run" in capsys.readouterr().out
