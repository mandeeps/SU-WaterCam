"""Tests for tools/powerlog.py (the once-a-minute power logger)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tools.powerlog as pl


def test_decode_rtc_reads_bcd_and_ignores_the_status_bit():
    # 2026-10-02 20:06:00, seconds register with its status bit set.
    assert pl.decode_rtc([0x80, 0x06, 0x20, 0x02, 0x05, 0x10, 0x26]) == "2026-10-02T20:06:00"


def test_decode_rtc_blank_when_a_register_is_unreadable():
    assert pl.decode_rtc([0x00, 0x06, None, 0x02, 0x05, 0x10, 0x26]) == ""
    assert pl.decode_rtc([]) == ""


def test_reading_formats_integer_and_hundredths(monkeypatch):
    regs = {1: 4, 2: 7, 5: 1, 6: None}
    monkeypatch.setattr(pl, "i2c", regs.get)
    assert pl.reading(1) == "4.07"
    assert pl.reading(5) == ""          # half a reading is no reading


def test_write_adds_the_header_once_and_fsyncs(tmp_path, monkeypatch):
    log = tmp_path / "powerlog.csv"
    synced = []
    real_fsync = os.fsync
    monkeypatch.setattr(pl.os, "fsync", lambda fd: (synced.append(fd), real_fsync(fd)))
    pl.write(str(log), "a\n")
    pl.write(str(log), "b\n")
    assert log.read_text() == pl.HEADER + "a\nb\n"
    assert len(synced) == 2


def test_sample_line_matches_the_header(monkeypatch):
    monkeypatch.setattr(pl, "i2c", lambda reg: 0x26 if reg == 64 else 1)
    monkeypatch.setattr(pl, "vcgencmd", lambda *a: {"measure_clock": "1800000000",
                                                    "get_throttled": "0x50000",
                                                    "measure_temp": "45.2'C"}[a[0]])
    line = pl.sample_line("abcd1234")
    assert line.endswith("\n")
    assert len(line.strip().split(",")) == len(pl.HEADER.strip().split(","))
    assert ",abcd1234,1.01,1.01,1.01,1,0x50000,1800,45.2," in line
