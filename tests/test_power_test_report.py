"""Tests for tools/power_test_report.py (per-phase summary and the mV/A fit)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.power_test_report as ptr

LOAD_TEST = """\
t,phase,throttled,arm_hz,vin,vout,iout,temp_c
1.0,idle,0x0,600000000,4.52,4.96,0.40,40.0
1.3,idle,0x0,600000000,4.52,4.96,0.40,40.1
1.6,stress,0x50005,600000000,4.40,4.70,1.00,55.0
1.9,stress,0x50000,1800000000,4.34,4.60,1.20,56.0
"""

POWERLOG = """\
time,uptime_s,boot_id,vin,vout,iout,power_mode,throttled,arm_mhz,temp_c,load1,rtc_time
# 2026-10-02T20:00:05 (rtc 2026-10-02T20:06:00) logger start, boot e4df28dc, wittypi action reason 0xa
2026-10-02T20:00:05,6,e4df28dc,4.42,4.85,1.35,0,0x0,2000,46.7,0.56,2026-10-02T20:06:00
2026-10-02T20:01:00,61,e4df28dc,,,,0,0x0,2000,46.7,0.56,
2026-10-02T20:02:00,121,e4df28dc,4.49,4.95,0.35,0,0x50005,600,40.1,0.10,2026-10-02T20:08:00
"""


def _csv(tmp_path, text, name="run.csv"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_fit_recovers_intercept_and_slope():
    xs = [0.4, 0.8, 1.2]
    intercept, slope = ptr.fit(xs, [4.5 - 0.3 * x for x in xs])
    assert intercept == pytest.approx(4.5)
    assert slope == pytest.approx(-0.3)


def test_fit_needs_current_to_vary():
    assert ptr.fit([0.5, 0.5], [4.4, 4.5]) is None
    assert ptr.fit([0.5], [4.4]) is None


def test_load_test_summary_per_phase(tmp_path):
    s = ptr.summarise(ptr.read_rows(_csv(tmp_path, LOAD_TEST)))
    assert list(s) == ["idle", "stress"]
    assert s["stress"]["uv"] == 1                  # only 0x50005 has the 'now' bit
    assert s["stress"]["vout_min"] == pytest.approx(4.60)
    assert s["stress"]["mhz_med"] == pytest.approx(1200)


def test_powerlog_skips_markers_and_blank_readings(tmp_path):
    rows = ptr.read_rows(_csv(tmp_path, POWERLOG))
    assert len(rows) == 2
    s = ptr.summarise(rows)
    assert list(s) == ["all"] and s["all"]["uv"] == 1
    assert s["all"]["mhz_med"] == pytest.approx(1300)   # arm_mhz column


def test_report_prints_the_slope_in_mv_per_amp(tmp_path):
    text = ptr.report(ptr.read_rows(_csv(tmp_path, LOAD_TEST)))
    assert "vin:" in text and "mV/A" in text and "stress" in text


def test_main_fails_cleanly_on_an_empty_file(tmp_path, capsys):
    assert ptr.main([_csv(tmp_path, "t,phase,throttled,arm_hz,vin,vout,iout,temp_c\n")]) == 1
    assert "No readings" in capsys.readouterr().err


def test_powerlog_fit_carries_a_caveat(tmp_path):
    text = ptr.report(ptr.read_rows(_csv(tmp_path, POWERLOG)))
    assert "isn't comparable to a load-test run" in text
    assert "isn't comparable" not in ptr.report(ptr.read_rows(_csv(tmp_path, LOAD_TEST, "lt.csv")))
