"""tools/lora_time.py: the clock from LoRa network time (DeviceTimeReq, AT+GPSTIME)."""
import json
import os
import subprocess

import pytest

from tools import gps_window, lora_daemon, lora_time, transmit_ip
from tools.lora_handler_concurrent import LoRaHandler, gps_ms_to_unix

#: 005's answer at 2026-11-10 05:18:16.388 UTC (measured)
ANSWER_MS = "1478323114388"
ANSWER_UNIX = 1794287896.388


@pytest.fixture
def lt(tmp_path, monkeypatch):
    helper = tmp_path / "watercam-set-clock"
    helper.write_text("#!/bin/sh\n")
    monkeypatch.setattr(lora_time, "SET_CLOCK", str(helper))
    monkeypatch.setattr(lora_time, "TIME_SOURCE", str(tmp_path / "time-source"))
    monkeypatch.setattr(lora_time, "RTC_UNTRUSTED", str(tmp_path / "rtc-untrusted"))
    monkeypatch.setattr(lora_time, "DONE_PATH", str(tmp_path / "lora-time-done"))
    monkeypatch.setattr(lora_time, "LOG_PATH", str(tmp_path / "time_sync_log.jsonl"))
    monkeypatch.setattr(lora_time, "CONFIG", str(tmp_path / "runtime_config.json"))
    monkeypatch.setattr(lora_time, "STATE_PATH", str(tmp_path / "lora_time.json"))
    monkeypatch.setattr(lora_time, "_boot_id", lambda: "boot-1")
    monkeypatch.setattr(lora_time, "_mark_verified", lambda: None)
    monkeypatch.setattr(lora_time, "BIAS_DEFAULT_S", 0.0)   # the bias has its own tests
    lora_time.tmp = tmp_path
    return lora_time


def set_mode(m, value):
    (m.tmp / "runtime_config.json").write_text(json.dumps({"lora_time_sync": value}))


class Handler:
    def __init__(self, offset=0.3, ok=True, joined=True):
        self.offset, self.ok, self.joined, self.asked = offset, ok, joined, 0

    def is_joined(self):
        return self.joined

    def network_time(self):
        self.asked += 1
        if not self.ok:
            return {"ok": False, "error": "no answer"}
        t = 1794287896.0
        return {"ok": True, "unix": t + self.offset, "answered_at": t, "rtt_s": 1.2}


HEALTHY = lambda: {"healthy": True, "joined": True, "margin_db": 15, "gateways": 1, "reason": "15 dB"}  # noqa: E731
WEAK = lambda: {"healthy": False, "joined": True, "margin_db": 2, "gateways": 1, "reason": "weak link"}  # noqa: E731


class Run:
    def __init__(self, rc=0):
        self.rc, self.calls = rc, []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, "", "bad" if self.rc else "")


def logged(m):
    return [json.loads(line) for line in (m.tmp / "time_sync_log.jsonl").read_text().splitlines()]


def test_the_mdots_answer_is_gps_milliseconds():
    assert gps_ms_to_unix(ANSWER_MS) == pytest.approx(ANSWER_UNIX)
    for bad in ("", "OK", "PACKET:PORT=0,SIZE=0,DATA=", "12", "99999999999999999"):
        assert gps_ms_to_unix(bad) is None


def test_the_handler_reads_the_time_line_and_skips_the_rest():
    h = LoRaHandler.__new__(LoRaHandler)
    h._at_query = lambda cmd, timeout, done: (["AT+GPSTIME", ANSWER_MS, "OK"] if cmd == b"AT+GPSTIME\r\n" else [])
    r = h.network_time()
    assert r["ok"] and r["unix"] == pytest.approx(ANSWER_UNIX) and r["rtt_s"] is not None
    h._at_query = lambda cmd, timeout, done: ["ERROR"]
    assert h.network_time() == {"ok": False, "unix": None, "answered_at": None, "rtt_s": None, "error": "ERROR"}


def test_the_daemon_serves_it():
    assert lora_daemon.ACTIONS["network_time"] is lora_daemon._do_network_time


def test_healthy_link_and_a_clock_that_agrees_is_verified_not_set(lt):
    run = Run()
    ev = lt.sync(Handler(offset=0.3), run=run, assess=HEALTHY)
    assert ev["asked"] and ev["verified"] and not ev["applied"] and ev["offset_s"] == 0.3
    assert run.calls == []
    assert lt.synced_this_boot()
    assert logged(lt)[-1]["source"] == "lora"


def test_healthy_link_and_a_clock_off_is_set_through_the_helper(lt):
    run = Run()
    ev = lt.sync(Handler(offset=30.0), run=run, now=lambda: 1794287896.0, assess=HEALTHY)
    assert ev["applied"] and ev["verified"]
    argv = run.calls[0]
    assert argv[:2] == ["doas", lt.SET_CLOCK] and argv[3] == "lora"
    assert int(argv[2]) == 1794287926                     # network time now, whole seconds


def test_ip_time_steps_aside_once_lora_has_the_clock(lt, monkeypatch, tmp_path):
    lt.sync(Handler(offset=0.3), run=Run(), assess=HEALTHY)
    helper = tmp_path / "set-clock-ip"
    helper.write_text("")
    monkeypatch.setattr(transmit_ip, "SET_CLOCK", str(helper))
    monkeypatch.setattr(transmit_ip, "TIME_SOURCE", str(tmp_path / "no-source"))
    monkeypatch.setattr(transmit_ip, "CLOCK_CHECKED", str(tmp_path / "checked"))
    called = []
    monkeypatch.setattr(transmit_ip.subprocess, "run", lambda *a, **k: called.append(a) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    assert transmit_ip.correct_clock({"server_date": "Tue, 10 Nov 2026 05:18:16 GMT", "rtt_s": 0.2}) is None
    assert not called


def test_a_weak_link_is_left_to_ip_and_gps_by_default(lt):
    h, run = Handler(offset=30.0), Run()
    ev = lt.sync(h, run=run, assess=WEAK)
    assert not ev["asked"] and h.asked == 0 and not run.calls
    assert "weak link" in ev["reason"] and not lt.synced_this_boot()


def test_measure_asks_on_a_weak_link_but_only_logs(lt):
    set_mode(lt, "measure")
    h, run = Handler(offset=30.0), Run()
    ev = lt.sync(h, run=run, assess=WEAK)
    assert ev["asked"] and ev["ok"] and not ev["applied"] and not run.calls
    assert logged(lt)[-1]["offset_s"] == 30.0 and logged(lt)[-1]["link"]["margin_db"] == 2


def test_measure_still_sets_the_clock_on_a_healthy_link(lt):
    set_mode(lt, "measure")
    run = Run()
    assert lt.sync(Handler(offset=30.0), run=run, assess=HEALTHY)["applied"]


def test_always_uses_a_weak_link_too(lt):
    set_mode(lt, "always")
    run = Run()
    assert lt.sync(Handler(offset=30.0), run=run, assess=WEAK)["applied"]


def test_off_never_asks(lt):
    set_mode(lt, "off")
    h = Handler()
    assert not lt.sync(h, run=Run(), assess=HEALTHY)["asked"] and h.asked == 0


def test_unknown_mode_means_healthy(lt):
    set_mode(lt, "sometimes")
    assert lt.mode() == "healthy"


def test_gps_time_this_boot_wins(lt):
    (lt.tmp / "time-source").write_text("gps\n")
    h = Handler(offset=30.0)
    ev = lt.sync(h, run=Run(), assess=HEALTHY)
    assert not ev["asked"] and "GPS" in ev["reason"]


def test_network_time_this_boot_is_refined_by_lora(lt):
    (lt.tmp / "time-source").write_text("network\n")
    assert lt.sync(Handler(offset=3.0), run=Run(), assess=HEALTHY)["applied"]


def test_a_day_off_is_not_trusted_unless_the_rtc_is_bad(lt):
    run = Run()
    ev = lt.sync(Handler(offset=3 * 86400), run=run, now=lambda: 1794287896.0, assess=HEALTHY)
    assert not ev["applied"] and not run.calls and "more than a day" in ev["reason"]
    (lt.tmp / "rtc-untrusted").write_text("")
    os.remove(lt.DONE_PATH)
    assert lt.sync(Handler(offset=3 * 86400), run=run, now=lambda: 1794287896.0, assess=HEALTHY)["applied"]


def test_failures_are_logged_not_raised(lt):
    ev = lt.sync(Handler(ok=False), run=Run(), assess=HEALTHY)
    assert ev["asked"] and not ev["ok"] and "no network time" in ev["reason"]
    ev = lt.sync(Handler(offset=30.0), run=Run(rc=1), assess=HEALTHY)
    assert not ev["applied"] and "not corrected" in ev["reason"]
    os.remove(lt.SET_CLOCK)
    ev = lt.sync(Handler(offset=30.0), run=Run(), assess=HEALTHY)
    assert not ev["applied"] and "isn't installed" in ev["reason"]


def test_not_joined_is_not_asked(lt):
    h = Handler()
    ev = lt.sync(h, run=Run(), assess=lambda: {"healthy": False, "joined": False, "reason": "not joined"})
    assert not ev["asked"] and ev["reason"] == "not joined"


def test_the_daemon_tries_once_per_boot_after_the_join(lt, monkeypatch):
    joins = iter([False, False, True])

    class Late(Handler):
        def is_joined(self):
            return next(joins, True)
    calls = []
    monkeypatch.setattr(lt, "sync", lambda h: calls.append(h) or {"reason": "synced"})
    assert lt.sync_when_joined(Late(), sleep=lambda s: None)["reason"] == "synced"
    (lt.tmp / "lora-time-done").write_text(json.dumps({"boot": "boot-1"}))
    assert lt.sync_when_joined(Late(), sleep=lambda s: None)["reason"] == "already tried this boot"
    assert len(calls) == 1


def test_at_most_once_a_day_across_boots(lt, monkeypatch):
    assert lt.due(now=1_000_000)
    lt.sync(Handler(offset=0.3), run=Run(), now=lambda: 1_000_000, assess=HEALTHY)
    assert not lt.due(now=1_000_000 + 3600)              # an hourly wake later: not yet
    assert lt.due(now=1_000_000 + 24 * 3600)             # a day later
    assert lt.due(now=1_000_000 - 60)                    # the clock went back: ask
    monkeypatch.setattr(lt, "_boot_id", lambda: "boot-2")  # a new boot an hour later
    monkeypatch.setattr(lt.time, "time", lambda: 1_000_000 + 3600)
    assert lt.sync_when_joined(Handler())["reason"] == "asked within the last day"


def test_a_weak_link_skip_doesnt_count_as_asking(lt):
    lt.sync(Handler(), run=Run(), now=lambda: 1_000_000, assess=WEAK)
    assert lt.due(now=1_000_000 + 60)


def test_the_daemon_gives_up_without_a_join(lt, monkeypatch):
    monkeypatch.setattr(lt.time, "monotonic", iter([0, 0, 10_000]).__next__)
    assert "not joined" in lt.sync_when_joined(Handler(joined=False), wait_s=60, sleep=lambda s: None)["reason"]


def test_the_log_keeps_the_newest_lines(lt, monkeypatch):
    monkeypatch.setattr(lt, "LOG_MAX_LINES", 5)
    for i in range(8):
        lt.log_event({"i": i})
    assert [e["i"] for e in logged(lt)] == [3, 4, 5, 6, 7]


def test_the_gps_window_logs_its_offset_even_when_it_doesnt_set_the_clock(lt, monkeypatch, tmp_path):
    source = tmp_path / "gps-time-source"
    source.write_text("lora\n")
    monkeypatch.setattr(gps_window, "TIME_SOURCE", str(source))
    monkeypatch.setattr(gps_window.time, "time", lambda: 1794287896.0)
    ran = []
    monkeypatch.setattr(gps_window.subprocess, "run", lambda *a, **k: ran.append(a))

    class Packet:
        time = "2026-11-10T05:18:16.250Z"
    gps_window._set_clock_from(Packet())
    ev = logged(lt)[-1]
    assert ev["source"] == "gps" and ev["clock_from"] == "lora" and not ev["applied"]
    assert ev["offset_s"] == pytest.approx(0.25, abs=1e-3)
    assert not ran                                       # LoRa set it this boot: GPS only compares


def test_the_helper_takes_only_known_sources():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "watercam-set-clock")
    text = open(path).read()
    assert 'source=${2:-server}' in text and '^(server|lora|gps)$' in text
    r = subprocess.run(["bash", path, "1794287896", "'; rm -rf /'"], capture_output=True, text=True)
    assert r.returncode == 2 and "source must be" in r.stderr


def test_an_unanswered_ask_on_a_healthy_link_retries_sooner(lt):
    lt.sync(Handler(ok=False), run=Run(), now=lambda: 1_000_000, assess=HEALTHY)
    assert not lt.due(now=1_000_000 + 3600)
    assert lt.due(now=1_000_000 + lt.RETRY_S)                      # not a whole day
    lt.sync(Handler(ok=True), run=Run(), now=lambda: 1_100_000, assess=HEALTHY)
    assert not lt.due(now=1_100_000 + lt.RETRY_S)                  # answered: a day again


def test_an_unanswered_ask_on_a_weak_link_waits_a_day(lt):
    set_mode(lt, "measure")
    lt.sync(Handler(ok=False), run=Run(), now=lambda: 1_000_000, assess=WEAK)
    assert not lt.due(now=1_000_000 + lt.RETRY_S)
    assert lt.due(now=1_000_000 + lt.INTERVAL_S)


def test_a_gone_gateway_costs_no_asks(lt):
    # The mDot still says joined, but the link check fails: not healthy, so no ask
    gone = lambda: {"healthy": False, "joined": True, "margin_db": None, "gateways": 0,  # noqa: E731
                    "reason": "link check failed: no gateway answered"}
    h = Handler()
    for t in range(5):
        lt.sync(h, run=Run(), now=lambda: 1_000_000 + t * 3600, assess=gone)
    assert h.asked == 0 and not lt.synced_this_boot()             # server and GPS time still run


def test_the_bias_is_subtracted_before_deciding_and_setting(lt, monkeypatch):
    (lt.tmp / "runtime_config.json").write_text(json.dumps({"lora_time_bias_s": 0.21}))
    # LoRa time 1.1 s ahead of the clock is 0.89 s after the bias: within the tolerance
    e = lt.sync(Handler(offset=1.1), run=Run(), now=lambda: 1794287896.0, assess=HEALTHY)
    assert e["verified"] and not e["applied"] and e["offset_s"] == pytest.approx(0.89) and e["bias_s"] == 0.21
    os.unlink(lt.DONE_PATH)
    run = Run()
    e = lt.sync(Handler(offset=5.21), run=run, now=lambda: 1794287896.0, assess=HEALTHY)
    assert e["applied"] and run.calls[0][2] == str(1794287896 + 5)   # 5.21 - 0.21


def test_the_bias_setting_falls_back_to_the_default(lt, monkeypatch):
    monkeypatch.setattr(lt, "BIAS_DEFAULT_S", 0.21)
    assert lt.bias_s() == 0.21                                         # no setting
    for bad in (5.0, -3, "x", None):
        (lt.tmp / "runtime_config.json").write_text(json.dumps({"lora_time_bias_s": bad}))
        assert lt.bias_s() == 0.21
    (lt.tmp / "runtime_config.json").write_text(json.dumps({"lora_time_bias_s": 0}))
    assert lt.bias_s() == 0.0                                          # a gateway with GPS timestamps
    (lt.tmp / "runtime_config.json").write_text(json.dumps({"lora_time_bias_s": -0.05}))
    assert lt.bias_s() == -0.05
