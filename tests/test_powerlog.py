"""Tests for tools/powerlog.py (the once-a-minute power logger)."""

import os
import sys

import pytest

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


# ── surviving power cuts ────────────────────────────────────────────────────

def test_header_goes_into_a_zero_length_file(tmp_path):
    # A cut before the first fsync can leave the file created but empty.
    log = tmp_path / "powerlog.csv"
    log.write_text("")
    pl.write(str(log), "a\n")
    assert log.read_text() == pl.HEADER + "a\n"


def test_a_torn_last_line_is_finished_before_the_next_write(tmp_path):
    log = tmp_path / "powerlog.csv"
    log.write_text(pl.HEADER + "2026-10-02T20:00:05,6,e4df28dc,4.4")   # cut mid-line
    pl.write(str(log), "# 2026-10-02T20:06:00 logger start, boot f4689a24\n")
    lines = log.read_text().splitlines()
    assert lines[-1].startswith("# ")           # the boot marker keeps its own line
    assert lines[-2].endswith(",4.4")


def test_reading_retries_when_the_integer_moves_mid_read(monkeypatch):
    # 4.99 -> 5.00 between reads: integer 4, hundredths 00, integer again 5.
    seq = iter([4, 0, 5, 5, 0, 5])
    monkeypatch.setattr(pl, "i2c", lambda reg: next(seq))
    assert pl.reading(3) == "5.00"


def test_reading_gives_up_rather_than_guess(monkeypatch):
    seq = iter([4, 99, 5] * 3)
    monkeypatch.setattr(pl, "i2c", lambda reg: next(seq))
    assert pl.reading(3) == ""


def test_interval_must_be_positive(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["powerlog.py", "--interval", "0"])
    with pytest.raises(SystemExit) as e:
        pl.main()
    assert e.value.code == 2
    assert "--interval must be greater than 0" in capsys.readouterr().err


def test_a_failed_write_does_not_stop_the_logger(tmp_path, monkeypatch, capsys):
    log = tmp_path / "powerlog.csv"
    monkeypatch.setattr(sys, "argv", ["powerlog.py", "--log", str(log), "--interval", "60"])
    monkeypatch.setattr(pl, "i2c", lambda reg: None)
    monkeypatch.setattr(pl, "sample_line", lambda boot_id: (_ for _ in ()).throw(OSError(28, "No space left")))

    class Stop(Exception):
        pass

    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:
            raise Stop

    monkeypatch.setattr(pl.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        pl.main()
    assert len(sleeps) == 2                       # kept going after two failed writes
    assert "No space left" in capsys.readouterr().err
    assert log.read_text().startswith(pl.HEADER + "# ")   # the start marker still landed
