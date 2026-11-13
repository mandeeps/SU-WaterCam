"""config/wittypi/network_time.py and syncTime.sh: network time at boot, from a source a
captive portal can't answer, with the API server down too, timed out and checked."""
import importlib.util
import json
import os
import shutil
import subprocess
import urllib.error
from email.message import Message
from email.utils import formatdate
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WITTYPI = ROOT / "config" / "wittypi"
spec = importlib.util.spec_from_file_location("network_time", WITTYPI / "network_time.py")
nt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nt)

NOW = 1794287896.0
API = "http://watercam-api.example.ts.net:8000"


def config(tmp_path, url=API):
    p = tmp_path / "runtime_config.json"
    p.write_text(json.dumps({"ip_upload": {"server_url": url}}))
    return str(p)


def run(tmp_path, answers, url=API, trusted=True, untrusted=False, clock=NOW, capsys=None):
    """main() with fetch answering from `answers` {url prefix: unix time or None}."""
    asked = []

    def fetch(u):
        asked.append(u)
        for prefix, ts in answers.items():
            if u.startswith(prefix):
                return ts
        return None

    marker = tmp_path / "rtc-untrusted"
    if untrusted:
        marker.write_text("x")
    nt.RTC_UNTRUSTED = str(marker)
    rc = nt.main(["--config", config(tmp_path, url)], fetch=fetch, is_trusted=lambda u: trusted,
                 now=lambda: clock)
    return rc, asked


def test_the_api_server_comes_first(tmp_path, capsys):
    rc, asked = run(tmp_path, {API: NOW + 2, "https://": NOW + 3})
    assert rc == 0 and asked == [API + "/health"]
    assert capsys.readouterr().out.split() == [f"{NOW + 2:.3f}", "api"]


def test_api_server_down_falls_back_to_public_https(tmp_path, capsys):
    rc, asked = run(tmp_path, {"https://www.google.com": NOW + 3})
    assert rc == 0 and asked == [API + "/health", "https://www.google.com"]
    assert capsys.readouterr().out.split()[1] == "www.google.com"
    rc, asked = run(tmp_path, {"https://www.cloudflare.com": NOW + 3})
    assert rc == 0 and len(asked) == 3


def test_nothing_reachable_is_no_time(tmp_path, capsys):
    rc, asked = run(tmp_path, {})
    assert rc == 1 and capsys.readouterr().out == ""


def test_an_untrusted_api_url_is_skipped(tmp_path):
    # Plain http outside the tailnet (or a .ts.net name not resolving into it)
    rc, asked = run(tmp_path, {"https://www.google.com": NOW}, url="http://203.0.113.5:8000", trusted=False)
    assert rc == 0 and asked == ["https://www.google.com"]
    assert all(u.startswith("https://") for u in nt.PUBLIC)


def _status(running=True, peers=(("api.example.ts.net.", ["100.101.102.103"]),)):
    return json.dumps({"BackendState": "Running" if running else "Stopped",
                       "Peer": {str(i): {"DNSName": n, "TailscaleIPs": ips} for i, (n, ips) in enumerate(peers)}})


def _tailscale(monkeypatch, out):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(cmd, 0, out, "")
    monkeypatch.setattr(nt.subprocess, "run", run)
    return calls


def _resolve(monkeypatch, addrs):
    def getaddrinfo(host, port):          # a literal address resolves to itself
        try:
            found = [str(nt.ipaddress.ip_address(host))]
        except ValueError:
            found = addrs
        return [(0, 0, 0, "", (a, 0)) for a in found]
    monkeypatch.setattr(nt.socket, "getaddrinfo", getaddrinfo)


def test_trusted_matches_the_uploads_rule(monkeypatch):
    _tailscale(monkeypatch, _status())
    _resolve(monkeypatch, ["100.101.102.103"])
    assert nt.trusted("https://api.example.org")
    assert nt.trusted("http://100.101.102.103:8000")          # a current peer
    assert nt.trusted("http://api.example.ts.net:8000")       # resolves to that peer
    assert not nt.trusted("http://100.64.0.9:8000")           # tailnet range, but no peer's
    assert not nt.trusted("http://other.example.ts.net:8000")  # no peer of that name
    assert not nt.trusted("http://192.168.1.5:8000")
    assert not nt.trusted("http://google.com")
    _resolve(monkeypatch, ["100.101.102.103", "203.0.113.5"])
    assert not nt.trusted("http://api.example.ts.net:8000")   # partly outside the tailnet


@pytest.mark.parametrize("out", [_status(running=False), "not json", OSError("no tailscale")])
def test_http_is_untrusted_without_a_running_tailscale(monkeypatch, out):
    _tailscale(monkeypatch, out)
    _resolve(monkeypatch, ["100.101.102.103"])
    assert not nt.trusted("http://100.101.102.103:8000")
    assert not nt.trusted("http://api.example.ts.net:8000")
    assert nt.trusted("https://api.example.org")


@pytest.mark.parametrize("offset, untrusted, clock, ok", [
    (3, False, NOW, True),
    (23 * 3600, False, NOW, True),
    (3 * 86400, False, NOW, False),           # an access point's stale clock, or a wrong answer
    (-3 * 86400, False, NOW, False),
    (3 * 86400, True, NOW, True),             # the RTC lost its time: a big step is expected
    (3 * 86400, False, 1_000_000_000, True),  # the clock is before 2026
])
def test_a_step_over_a_day_needs_a_bad_rtc(tmp_path, offset, untrusted, clock, ok):
    rc, _ = run(tmp_path, {API: NOW + offset}, untrusted=untrusted, clock=clock)
    assert (rc == 0) == ok


def test_out_of_range_answers_are_refused(tmp_path):
    assert run(tmp_path, {API: 1_000_000_000.0}, untrusted=True)[0] == 1
    assert run(tmp_path, {API: 5_000_000_000.0}, untrusted=True)[0] == 1


def test_fetch_reads_the_date_header_of_any_status():
    def headers(ts):
        m = Message()
        m["Date"] = formatdate(ts, usegmt=True)
        return m

    class Resp:
        def __init__(self, ts):
            self.headers = headers(ts)

        def close(self):
            pass

    t = iter([100.0, 100.4])
    ts = nt.fetch_date("https://x", opener=lambda req, **kw: Resp(NOW), now=lambda: next(t))
    assert ts == pytest.approx(NOW + 0.5 + 0.2)       # floored second + half the round trip

    def refused(req, **kw):
        raise urllib.error.HTTPError(req.full_url, 405, "no", headers(NOW), None)
    assert nt.fetch_date("https://x", opener=refused) == pytest.approx(NOW + 0.5, abs=0.1)

    def down(req, **kw):
        raise urllib.error.URLError("connection refused")
    assert nt.fetch_date("https://x", opener=down) is None

    def kw_check(req, **kw):
        assert kw["timeout"] == nt.REQUEST_TIMEOUT_S and kw["context"].verify_mode.name == "CERT_REQUIRED"
        assert req.get_method() == "HEAD"
        return Resp(NOW)
    assert nt.fetch_date("https://x", opener=kw_check)


# --- syncTime.sh -------------------------------------------------------------

@pytest.fixture
def sync(tmp_path):
    """syncTime.sh in a folder of stand-ins: utilities.sh, network_time.py, sudo."""
    d = tmp_path / "wittypi"
    d.mkdir()
    shutil.copy(WITTYPI / "syncTime.sh", d)
    (d / "utilities.sh").write_text('log() { echo "LOG $*"; }\nsystem_to_rtc() { echo RTC-WRITTEN; }\n')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "sudo").write_text(f'#!/bin/sh\necho "SUDO $*" >> {tmp_path}/sudo.log\n')
    (bin_ / "sudo").chmod(0o755)

    def go(net_out="", net_rc=0, prior=None, witty_log=""):
        (d / "network_time.py").write_text(
            f"import sys\nprint({net_out!r}, end='')\nprint('API down', file=sys.stderr)\nsys.exit({net_rc})\n")
        (d / "wittyPi.log").write_text(witty_log)
        src = tmp_path / "time-source"
        if prior:
            src.write_text(prior + "\n")
        elif src.exists():
            src.unlink()
        env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "SYNC_WAIT_S": "0",
               "WATERCAM_TIME_SOURCE": str(src), "WATERCAM_RTC_UNTRUSTED": str(tmp_path / "rtc-untrusted"),
               "WATERCAM_NET_ERR": str(tmp_path / "net.err")}
        r = subprocess.run(["bash", str(d / "syncTime.sh")], capture_output=True, text=True, env=env, timeout=30)
        sudo_log = tmp_path / "sudo.log"
        out = r.stdout + (sudo_log.read_text() if sudo_log.exists() else "")
        if sudo_log.exists():
            sudo_log.unlink()
        return out, (src.read_text().strip() if src.exists() else None)

    go.tmp = tmp_path
    return go


def test_sync_applies_network_time(sync):
    out, source = sync(net_out="1794287896.700 api\n")
    assert "SUDO date -u -s @1794287896.700" in out and "RTC-WRITTEN" in out and source == "network"


def test_sync_leaves_lora_and_gps_time_alone(sync):
    for prior in ("lora", "gps"):
        out, source = sync(net_out="1794287896.700 api\n", prior=prior)
        assert "SUDO" not in out and source == prior


def test_sync_without_network_time_changes_nothing(sync):
    out, source = sync(net_out="", net_rc=1)
    assert "SUDO" not in out and "RTC-WRITTEN" not in out and source is None
    assert "No network time: API down" in out


def test_sync_marks_a_bad_rtc_for_the_other_sources(sync):
    sync(witty_log="... RTC has good time\n")
    assert not (sync.tmp / "rtc-untrusted").exists()
    sync(witty_log="RTC has good time\n... RTC has bad time\n")
    assert (sync.tmp / "rtc-untrusted").exists()
