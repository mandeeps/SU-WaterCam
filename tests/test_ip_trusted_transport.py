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
    with patch.object(tip, "_resolves_into_tailnet", return_value=True):
        assert tip.trusted_server(url) is ok


def _resolve(*addrs):
    import socket
    return patch.object(tip.socket, "getaddrinfo",
                        return_value=[(socket.AF_INET, 0, 0, "", (a, 0)) for a in addrs])


def _status(running=True, peers=(("server.example.ts.net.", ["100.101.102.103"]),)):
    """A `tailscale status --json` with the given peers (DNSName, TailscaleIPs)."""
    return patch.object(tip, "_tailscale_status", return_value={
        "BackendState": "Running" if running else "Stopped",
        "Peer": {f"k{i}": {"DNSName": n, "TailscaleIPs": ips} for i, (n, ips) in enumerate(peers)},
    })


def test_a_ts_net_name_must_resolve_into_the_tailnet():
    url = "http://server.example.ts.net:8000"
    with _status(), _resolve("100.101.102.103"):
        assert tip.trusted_server(url)
    with _resolve("100.101.102.103", "192.0.2.7"):
        assert not tip.trusted_server(url)
    with _resolve("203.0.113.9"):
        assert not tip.trusted_server(url)
    with patch.object(tip.socket, "getaddrinfo", side_effect=OSError("no dns")):
        assert not tip.trusted_server(url)
    with _resolve("203.0.113.9"):
        assert tip.trusted_server("https://x.ts.net")       # https verifies the certificate itself


def test_a_tailnet_dns_answer_is_not_enough_when_tailscale_is_down():
    # A hostile WiFi network's DNS can answer with a 100.x address: with Tailscale
    # down the request would leave over that network, key and all
    url = "http://server.example.ts.net:8000"
    with _resolve("100.101.102.103"):
        with _status(running=False):
            assert not tip.trusted_server(url)
        with patch.object(tip, "_tailscale_status", return_value=None):     # unreadable
            assert not tip.trusted_server(url)
        with _status(peers=()):                                             # not a peer
            assert not tip.trusted_server(url)


def test_the_address_must_be_the_named_peers():
    url = "http://server.example.ts.net:8000"
    other = (("server.example.ts.net.", ["100.101.102.103"]), ("laptop.example.ts.net.", ["100.90.1.2"]))
    with _status(peers=other):
        with _resolve("100.90.1.2"):           # another peer's address
            assert not tip.trusted_server(url)
        with _resolve("100.101.102.103"):
            assert tip.trusted_server(url)
        with _resolve("100.64.9.9"):           # in the range, but nobody's
            assert not tip.trusted_server(url)


def test_a_literal_tailnet_address_must_be_a_running_peers():
    with _status(peers=(("server.example.ts.net.", ["100.101.102.103", "fd7a:115c:a1e0::1"]),)):
        with _resolve("100.101.102.103"):
            assert tip.trusted_server("http://100.101.102.103:8000")
        with _resolve("fd7a:115c:a1e0::1"):
            assert tip.trusted_server("http://[fd7a:115c:a1e0::1]:8000")
        with _resolve("100.64.9.9"):
            assert not tip.trusted_server("http://100.64.9.9:8000")
    with _status(running=False), _resolve("100.101.102.103"):
        assert not tip.trusted_server("http://100.101.102.103:8000")
    with patch.object(tip, "_tailscale_status", return_value=None), _resolve("100.101.102.103"):
        assert tip.trusted_server("http://127.0.0.1:8000")   # loopback needs no tailnet


def test_the_api_key_stays_home_when_tailscale_is_down():
    tx = _keyed("http://server.example.ts.net:8000")
    with _resolve("100.101.102.103"), _status(running=False):
        assert "Authorization" not in tx._auth_headers()
    with _resolve("100.101.102.103"), _status():
        assert tx._auth_headers() == {"Authorization": "Bearer secret"}


def test_tailscale_status_is_cached_but_online_check_is_fresh(monkeypatch):
    calls = []

    class R:
        stdout = '{"BackendState": "Running", "Self": {"Online": true}, "Peer": {}}'

    def run(*a, **k):
        calls.append(1)
        return R()
    monkeypatch.setattr(tip, "_status_cache", (float("-inf"), None))
    monkeypatch.setattr(tip.subprocess, "run", run)
    tip._tailscale_status(); tip._tailscale_status()
    assert len(calls) == 1
    tip.tailscale_online("server.example.ts.net")
    assert len(calls) == 2


def _keyed(url):
    tx = _tx(url)
    tx.api_key, tx._key_refused_logged = "secret", False
    return tx


def test_api_key_goes_only_to_a_trusted_server():
    with _resolve("100.101.102.103"), _status(peers=(("a.ts.net.", ["100.101.102.103"]),)):
        assert _keyed("http://a.ts.net:8000")._auth_headers() == {"Authorization": "Bearer secret"}
    with _resolve("203.0.113.9"):
        assert _keyed("http://a.ts.net:8000")._auth_headers() == {}
    assert _keyed("http://192.168.1.20:8000")._auth_headers() == {}
    assert _keyed("https://api.example.org")._auth_headers() == {"Authorization": "Bearer secret"}


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
    assert out["success"] and out["commands"] == [] and out["untrusted"]


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


@pytest.mark.parametrize("url,status,why", [
    ("http://192.168.1.20:8000", None, "outside the tailnet"),
    ("http://server.example.ts.net:8000", "unreadable", "can't be read"),
    ("http://server.example.ts.net:8000", "stopped", "isn't running (Stopped)"),
    ("http://other.example.ts.net:8000", "running", "isn't a current Tailscale peer"),
    ("http://server.example.ts.net:8000", "running", "doesn't resolve to its Tailscale peer"),
])
def test_why_untrusted_names_the_actual_reason(url, status, why):
    st = {"unreadable": None,
          "stopped": {"BackendState": "Stopped", "Peer": {}},
          "running": {"BackendState": "Running", "Peer": {"k": {
              "DNSName": "server.example.ts.net.", "TailscaleIPs": ["100.101.102.103"]}}}}.get(status)
    with patch.object(tip, "_tailscale_status", return_value=st), _resolve("100.90.9.9"):
        assert not tip.trusted_server(url)
        assert why in tip.why_untrusted(url)


def test_the_key_refusal_says_why(caplog):
    tx = _keyed("http://server.example.ts.net:8000")
    with _resolve("100.101.102.103"), _status(running=False):
        assert tx._auth_headers() == {}
    assert "Tailscale isn't running" in caplog.text
    assert "not a tailnet address" not in caplog.text
