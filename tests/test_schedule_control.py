"""The wake schedule from downlink 15 95 (tools/schedule_control.py)."""
from datetime import datetime

import pytest

from tools import recovery_boot as rb
from tools import schedule_control as sc
from tools.transmit_ip import apply_downlink_command, parse_downlink_frame


def _s(start="08:00", end="16:15", interval=120, awake=15):
    h, m = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return sc.Schedule(h * 60 + m, eh * 60 + em, interval, awake)


NOW = datetime(2026, 10, 22, 12, 30)


def test_bytes_round_trip_and_label():
    s = _s()
    assert s.to_bytes() == bytes([8, 0, 16, 15, 0, 120, 15])
    assert sc.from_bytes(s.to_bytes()) == s
    assert s.label() == "0800-1615/120/15"


@pytest.mark.parametrize("value", [b"\x08\x00\x10", bytes([24, 0, 16, 0, 0, 120, 15]),
                                   bytes([8, 60, 16, 0, 0, 120, 15])])
def test_bad_bytes(value):
    with pytest.raises(ValueError):
        sc.from_bytes(value)


def test_daylight_window_wakes():
    assert sc.wakes(_s()) == [0, 120, 240, 360, 480]       # 08:00 ... 16:00


def test_all_day():
    assert sc.wakes(_s("00:00", "00:00", 360, 15)) == [0, 360, 720, 1080]


def test_all_day_drops_a_last_wake_with_no_off_time():
    # 1440 isn't a multiple of 100: the wake at 1400 would leave 40 min to the
    # next day's first, more than awake (15), so it stays; with awake 45 it can't
    assert sc.wakes(_s("00:00", "00:00", 100, 15))[-1] == 1400
    assert sc.wakes(_s("00:00", "00:00", 100, 45))[-1] == 1300


def test_window_across_midnight():
    assert sc.wakes(_s("22:00", "02:00", 60, 10)) == [0, 60, 120, 180]


@pytest.mark.parametrize("s", [_s(interval=10), _s(interval=1500), _s(awake=4), _s(awake=61),
                               _s(interval=30, awake=30)])
def test_invalid(s):
    with pytest.raises(ValueError):
        sc.wakes(s)


def test_generated_script_parses_and_wakes_at_local_times():
    text = sc.generate(_s(), NOW)
    assert "BEGIN 2026-10-22 08:00:00" in text
    parsed = rb.parse_schedule(text)
    assert parsed is not None
    begin, end, states = parsed
    assert sum(sec for _, sec in states) == 86400
    after_now = rb.next_on_start(parsed, NOW.timestamp())
    assert datetime.fromtimestamp(after_now) == datetime(2026, 10, 22, 14, 0)
    late = rb.next_on_start(parsed, datetime(2026, 10, 22, 16, 30).timestamp())
    assert datetime.fromtimestamp(late) == datetime(2026, 10, 23, 8, 0)


def test_generated_off_times():
    text = sc.generate(_s(), NOW)
    lines = [l.split(None, 1) for l in text.splitlines() if l and not l.startswith(("#", "BEGIN", "END"))]
    assert lines[0] == ["ON", "M15"] and lines[1] == ["OFF", "H1 M45"]
    assert lines[-1] == ["OFF", "H15 M45"]            # 16:15 to 08:00


def _apply(tmp_path, s, arm=None, existing="OLD\n"):
    if existing is not None:
        (tmp_path / "schedule.wpi").write_text(existing)
    params, armed = {}, []
    ok, msg = sc.apply(s, wittypi_dir=str(tmp_path), now=NOW, arm=arm or armed.append,
                       set_param=params.__setitem__, clock=lambda: NOW.timestamp())
    return ok, msg, params, armed


def test_apply_writes_arms_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "log_to_wittypi", lambda *a: None)
    ok, msg, params, armed = _apply(tmp_path, _s())
    assert ok, msg
    assert (tmp_path / "schedule.wpi").read_text().startswith("# WaterCam schedule 0800-1615/120/15")
    assert (tmp_path / "schedule.wpi.prev").read_text() == "OLD\n"
    assert [datetime.fromtimestamp(t) for t in armed] == [datetime(2026, 10, 22, 14, 0)]
    assert params["schedule"]["interval"] == 120 and "utc_offset_min" in params["schedule"]
    assert params["monitoring_frequency"] == 120


def test_a_failed_arm_puts_the_old_schedule_back(tmp_path):
    def broken(_):
        raise RuntimeError("startup alarm read back wrong")
    ok, msg, params, _ = _apply(tmp_path, _s(), arm=broken)
    assert not ok and "previous schedule kept" in msg
    assert (tmp_path / "schedule.wpi").read_text() == "OLD\n"
    assert params == {}


def test_an_invalid_schedule_changes_nothing(tmp_path):
    ok, msg, params, armed = _apply(tmp_path, _s(interval=10))
    assert not ok and "rejected" in msg
    assert (tmp_path / "schedule.wpi").read_text() == "OLD\n" and not armed and not params


def test_refresh_only_when_the_offset_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "log_to_wittypi", lambda *a: None)
    stored = {"schedule": {**_s().as_dict(), "utc_offset_min": sc._offset_min(NOW)}}
    get = lambda k, d=None: stored.get(k, d)        # noqa: E731
    assert "up to date" in sc.refresh(now=NOW, get_param=get, wittypi_dir=str(tmp_path),
                                      arm=lambda t: None, set_param=stored.__setitem__)
    stored["schedule"]["utc_offset_min"] = -999
    out = sc.refresh(now=NOW, get_param=get, wittypi_dir=str(tmp_path), arm=lambda t: None,
                     set_param=stored.__setitem__, clock=lambda: NOW.timestamp())
    assert out.startswith("rewritten") and stored["schedule"]["utc_offset_min"] == sc._offset_min(NOW)


def test_downlink_15_95_applies_through_the_shared_decoder(tmp_path, monkeypatch):
    seen = {}

    def fake_apply(s, set_param=None, **kw):
        seen["s"] = s
        set_param("schedule", s.as_dict())
        return True, "applied"
    monkeypatch.setattr(sc, "apply", fake_apply)
    parts = parse_downlink_frame(bytes.fromhex("1595") + _s().to_bytes())
    params = {}
    out = apply_downlink_command({"parts": parts}, set_param_fn=params.__setitem__)
    assert seen["s"] == _s() and out["applied"] and not out["skipped"]


def test_downlink_12_92_is_gone():
    assert parse_downlink_frame(bytes.fromhex("12920168")) is None


# ── following the sun ────────────────────────────────────────────────────────

import os
import time as _time
from datetime import date

WHERE = (51.4769, 0.0)        # a generic reference point (Greenwich), not a site


@pytest.fixture
def utc(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    _time.tzset()
    yield
    monkeypatch.delenv("TZ", raising=False)
    _time.tzset()


SUN = sc.Schedule(30, -15, 60, 10, "sunrise", "sunset")


def test_sun_bytes_round_trip_and_label():
    assert SUN.to_bytes() == bytes([0xF0, 30, 0xF1, 0xF1, 0, 60, 10])   # -15 as a signed byte
    assert sc.from_bytes(SUN.to_bytes()) == SUN
    assert SUN.label() == "SR+30-SS-15/60/10"


def test_sun_offset_out_of_range():
    with pytest.raises(ValueError):
        sc.from_bytes(bytes([0xF0, 125, 16, 0, 0, 60, 10]))


def test_resolve_follows_the_seasons(utc):
    summer, _ = sc.resolve(SUN, date(2026, 6, 21), WHERE)
    winter, _ = sc.resolve(SUN, date(2026, 12, 21), WHERE)
    # sunrise + 30 to the nearest hour, not before sunrise + 20 (on_grid): 04:12 -> 04:00 is 18 min
    # after a 03:42 sunrise, so 05:00; 08:32 -> 09:00
    assert (summer.start, summer.end) == (5 * 60, 20 * 60 + 20 - 15)
    assert (winter.start, winter.end) == (9 * 60, 15 * 60 + 52 - 15)
    assert len(sc.wakes(summer)) > len(sc.wakes(winter))


def test_sun_start_on_the_interval_grid():
    assert sc.on_grid(7 * 60 + 57, 60) == 8 * 60           # 006, October: 07:57 -> 08:00
    assert sc.on_grid(8 * 60 + 2, 60) == 8 * 60            # nearest, not up: no wake lost
    assert sc.on_grid(8 * 60 + 30, 60) == 9 * 60           # halves round up
    assert sc.on_grid(7 * 60 + 10, 30) == 7 * 60
    assert sc.on_grid(7 * 60 + 20, 90) == 7 * 60 + 30      # gcd(90, 60) = 30
    assert sc.on_grid(7 * 60 + 10, 120) == 7 * 60
    assert sc.on_grid(23 * 60 + 50, 60) == 0               # past midnight wraps


def test_sun_start_leaves_charging_time_after_sunrise(utc):
    rise = lambda h, m: h * 60 + m + sc.MIN_AFTER_SUNRISE          # noqa: E731
    assert sc.MIN_AFTER_SUNRISE == 20
    assert sc.on_grid(7 * 60 + 57, 60, not_before=rise(7, 27)) == 8 * 60     # 006: 33 min after sunrise
    assert sc.on_grid(8 * 60 + 2, 60, not_before=rise(7, 32)) == 8 * 60      # 28 min after: kept
    assert sc.on_grid(8 * 60 + 20, 60, not_before=rise(7, 50)) == 9 * 60     # 08:00 is only 10 min after
    assert sc.on_grid(8 * 60 + 20, 30, not_before=rise(7, 50)) == 8 * 60 + 30
    assert sc.on_grid(0 * 60 + 10, 60, not_before=rise(23, 55)) == 1 * 60    # across midnight
    for rise_min in range(0, 24 * 60, 7):                  # always in [SR+20, SR+80)
        t = sc.on_grid(rise_min + 30, 60, not_before=(rise_min + sc.MIN_AFTER_SUNRISE) % sc.DAY)
        after = (t - rise_min) % sc.DAY
        assert sc.MIN_AFTER_SUNRISE <= after < sc.MIN_AFTER_SUNRISE + 60, (rise_min, t)


def test_clock_start_is_left_alone(utc):
    s = sc.Schedule(7 * 60 + 55, -15, 60, 10, "clock", "sunset")
    assert sc.resolve(s, date(2026, 6, 21), WHERE)[0].start == 7 * 60 + 55


def test_resolve_without_a_location_uses_the_fallback(utc):
    s, why = sc.resolve(SUN, date(2026, 6, 21), None)
    assert (s.start, s.end) == sc.FALLBACK_WINDOW and "no location" in why


def test_sun_schedule_applies_with_todays_window(tmp_path, monkeypatch, utc):
    monkeypatch.setattr(rb, "log_to_wittypi", lambda *a: None)
    now = datetime(2026, 6, 21, 12, 0)
    params, armed = {}, []
    ok, msg = sc.apply(SUN, wittypi_dir=str(tmp_path), now=now, arm=armed.append,
                       set_param=params.__setitem__, clock=lambda: now.timestamp(), where=WHERE)
    assert ok, msg
    assert params["schedule"]["window"] == [5 * 60, 20 * 60 + 5]
    assert params["schedule"]["start_ref"] == "sunrise"
    assert "BEGIN 2026-06-21 05:00:00" in (tmp_path / "schedule.wpi").read_text()
    assert datetime.fromtimestamp(armed[0]) == datetime(2026, 6, 21, 13, 0)
    assert sc.status_label(lambda k, d=None: params.get(k, d)) == "SR+30-SS-15/60/10 0500-2005"


def test_sun_schedule_rewritten_only_when_the_window_moves_10_min(tmp_path, monkeypatch, utc):
    monkeypatch.setattr(rb, "log_to_wittypi", lambda *a: None)
    stored = {}
    get = lambda k, d=None: stored.get(k, d)        # noqa: E731
    kw = dict(wittypi_dir=str(tmp_path), arm=lambda t: None, set_param=stored.__setitem__, where=WHERE)
    day0 = datetime(2026, 3, 20, 12, 0)             # around the equinox the window moves fastest
    assert sc.apply(SUN, now=day0, clock=lambda: day0.timestamp(), **kw)[0]
    day2 = datetime(2026, 3, 22, 12, 0)
    assert "up to date" in sc.refresh(now=day2, get_param=get, clock=lambda: day2.timestamp(), **kw)
    day7 = datetime(2026, 3, 27, 12, 0)
    out = sc.refresh(now=day7, get_param=get, clock=lambda: day7.timestamp(), **kw)
    assert out.startswith("rewritten (the sun window moved"), out
