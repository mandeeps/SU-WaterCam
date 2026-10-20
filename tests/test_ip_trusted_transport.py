"""Commands and the server's time only over the tailnet or verified HTTPS (#203),
and a bounded clock correction."""
import os
import time
from unittest.mock import patch

import pytest

import tools.transmit_ip as tip


@pytest.mark.parametrize("url,ok", [
    ("http://ist-rs-ccaicedo.taild7822.ts.net:8000", True),
    ("http://100.101.102.103:8000", True),
    ("http://[fd7a:115c:a1e0::1]:8000", True),
    ("https://api.example.org", True),
    ("http://localhost:8000", True),
    ("http://127.0.0.1:8000", True),
    ("http://192.168.1.20:8000", False),
    ("http://api.example.org", False),
    ("http://evil.ts.net.example.org", False),
    ("", False),
])
def test_trusted_server(url, ok):
    assert tip.trusted_server(url) is ok


def _tx(url):
    tx = tip.IPTransmitter.__new__(tip.IPTransmitter)
    tx.device_id, tx.server_url = "watercam-005", url
    return tx


def test_untrusted_server_gets_the_upload_but_no_command_request():
    tx = _tx("http://192.168.1.20:8000")
    with patch.object(tip.IPTransmitter, "_post_with_retry", return_value={"success": True}) as post:
        tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1, want_command=True)
    assert post.called and "want_command" not in post.call_args.args[1]


def test_untrusted_server_is_never_polled():
    tx = _tx("http://192.168.1.20:8000")
    tx._session = None                      # any request would fail loudly
    out = tx.poll_downlink()
    assert out["success"] and out["command"] is None and out["untrusted"]


def _correct(offset_s, tmp_path, monkeypatch, rtc_untrusted=False):
    """Run correct_clock with the server `offset_s` ahead; return the time it would set, or None."""
    monkeypatch.setattr(tip, "SET_CLOCK", str(tmp_path / "set-clock"))
    (tmp_path / "set-clock").write_text("")
    monkeypatch.setattr(tip, "TIME_SOURCE", str(tmp_path / "time-source"))
    monkeypatch.setattr(tip, "CLOCK_CHECKED", str(tmp_path / "checked"))
    monkeypatch.setattr(tip, "RTC_UNTRUSTED", str(tmp_path / "rtc-untrusted"))
    if rtc_untrusted:
        (tmp_path / "rtc-untrusted").write_text("skip: RTC unreadable or not valid\n")
    from email.utils import formatdate
    calls = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(tip.subprocess, "run", lambda cmd, **k: calls.append(cmd) or R())
    tip.correct_clock({"server_date": formatdate(time.time() + offset_s, usegmt=True), "rtt_s": 0})
    return int(calls[0][2]) if calls else None


def test_small_corrections_still_apply(tmp_path, monkeypatch):
    assert _correct(3600, tmp_path, monkeypatch) is not None


@pytest.mark.parametrize("offset", [3 * 86400, -3 * 86400])
def test_a_day_or_more_is_refused_with_a_valid_rtc(offset, tmp_path, monkeypatch):
    # (each case gets its own tmp_path: correct_clock runs once per boot)
    assert _correct(offset, tmp_path, monkeypatch) is None


def test_big_corrections_apply_when_the_rtc_is_untrusted(tmp_path, monkeypatch):
    assert _correct(30 * 86400, tmp_path, monkeypatch, rtc_untrusted=True) is not None


def test_rtc_early_clock_marks_an_untrusted_rtc(tmp_path, monkeypatch):
    import tools.rtc_early_clock as rec
    monkeypatch.setattr(rec, "RTC_UNTRUSTED", str(tmp_path / "rtc-untrusted"))
    monkeypatch.setattr(rec, "I2C_DEVICE", str(tmp_path))         # exists
    monkeypatch.setattr(rec, "rtc_timestamp", lambda: None)
    rec.main([])
    assert "RTC unreadable" in (tmp_path / "rtc-untrusted").read_text()
