"""tools/lora_time.py and config/wittypi/lora_answer.py: LoRa network time
(DeviceTimeReq, AT+GPSTIME), asked by lora_daemon and applied by syncTime.sh."""
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tools import gps_window, lora_daemon, lora_time, transmit_ip
from tools.lora_handler_concurrent import LoRaHandler, gps_ms_to_unix

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("lora_answer", ROOT / "config" / "wittypi" / "lora_answer.py")
la = importlib.util.module_from_spec(spec)
spec.loader.exec_module(la)

#: 005's answer at 2026-11-10 05:18:16.388 UTC (measured)
ANSWER_MS = "1478323114388"
ANSWER_UNIX = 1794287896.388
T = 1794287896.0


@pytest.fixture
def lt(tmp_path, monkeypatch):
    monkeypatch.setattr(lora_time, "TIME_SOURCE", str(tmp_path / "time-source"))
    monkeypatch.setattr(lora_time, "ANSWER_PATH", str(tmp_path / "lora-answer.json"))
    monkeypatch.setattr(lora_time, "TIME_SYNC_DONE", str(tmp_path / "time-sync-done"))
    monkeypatch.setattr(lora_time, "LOG_PATH", str(tmp_path / "time_sync_log.jsonl"))
    monkeypatch.setattr(lora_time, "CONFIG", str(tmp_path / "runtime_config.json"))
    monkeypatch.setattr(lora_time, "STATE_PATH", str(tmp_path / "lora_time.json"))
    monkeypatch.setattr(lora_time, "_boot_id", lambda: "boot-1")
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
        return {"ok": True, "unix": T + self.offset, "answered_at": T, "rtt_s": 1.2}


def synced(m, source):
    """syncTime.sh has finished, the clock from `source` (None: nothing)."""
    if source:
        (m.tmp / "time-source").write_text(source + "\n")
    (m.tmp / "time-sync-done").write_text("")


def answer(m):
    return json.loads((m.tmp / "lora-answer.json").read_text())


def state(m):
    return json.loads((m.tmp / "lora_time.json").read_text())


def logged(m):
    return [json.loads(line) for line in (m.tmp / "time_sync_log.jsonl").read_text().splitlines()]


def ask(m, handler=None, source="lora", now=T + 0.05, boottime=100.0):
    if source is not False:
        synced(m, source)
    return m.sync(handler or Handler(), now=lambda: now, boottime=lambda: boottime,
                  sleep=lambda s: None, outcome_wait_s=0)


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


# --- the daemon: ask, hand over, log ------------------------------------------

def test_the_answer_is_written_for_synctime_with_the_time_since_boot(lt):
    ask(lt, Handler(offset=30.0), now=T + 0.05, boottime=100.0)
    a = answer(lt)
    assert a["boot"] == "boot-1" and a["status"] == "answer"
    assert a["boottime"] == 100.0
    assert a["unix"] == pytest.approx(T + 30.05)          # network time when the boot time was read
    assert a["offset_s"] == 30.0


def test_the_daemon_never_sets_the_clock(lt, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran a command"))
    ask(lt, Handler(offset=30.0))
    assert "doas" not in Path(lt.__file__).read_text()


def test_applied_by_synctime_is_logged_and_counts_as_done(lt):
    ev = ask(lt, Handler(offset=0.4), source="lora")
    assert ev["applied"] and ev["clock_from"] == "lora" and ev["offset_s"] == 0.4
    assert state(lt)["used"] and not lt.due(now=T + 3600)
    assert logged(lt)[-1]["source"] == "lora"


def test_a_clock_set_by_network_time_counts_as_done_and_logs_the_comparison(lt):
    ev = ask(lt, Handler(offset=3.0), source="network")
    assert not ev["applied"] and ev["clock_from"] == "network" and ev["offset_s"] == 3.0
    assert not lt.due(now=T + 3600)


def test_an_answer_too_late_to_be_used_is_asked_for_again_after_6_h(lt):
    ev = ask(lt, Handler(offset=3.0), source=None)
    assert not ev["applied"] and ev["clock_from"] == "rtc" and "too late" in ev["reason"]
    assert not lt.due(now=T + 5 * 3600) and lt.due(now=T + 6 * 3600)


def test_an_unanswered_ask_waits_a_day(lt):
    ev = ask(lt, Handler(ok=False), source=False)
    assert not ev["ok"] and answer(lt)["status"] == "none"
    assert not lt.due(now=T + 12 * 3600) and lt.due(now=T + 24 * 3600)


def test_a_busy_mdot_refusing_at_once_is_asked_again(lt, monkeypatch):
    class Busy(Handler):
        def network_time(self):
            if self.asked < 2:
                self.asked += 1
                return {"ok": False, "error": "ERROR"}      # at once: nothing was sent
            return super().network_time()
    h, sleeps = Busy(offset=2.0), []
    synced(lt, "lora")
    ev = lt.sync(h, now=lambda: T, boottime=lambda: 1.0, sleep=sleeps.append, outcome_wait_s=0)
    assert ev["ok"] and ev["tries"] == 3 and h.asked == 3 and sleeps[:2] == [lt.ASK_RETRY_S] * 2


def test_an_ask_that_went_out_unanswered_is_not_repeated(lt, monkeypatch):
    clock = iter([0.0, 21.0] * 5)
    monkeypatch.setattr(lt.time, "monotonic", lambda: next(clock))
    h = Handler(ok=False)
    ev = lt.sync(h, now=lambda: T, boottime=lambda: 1.0, sleep=lambda s: None, outcome_wait_s=0)
    assert not ev["ok"] and h.asked == 1 and ev["tries"] == 1


def test_a_mdot_that_keeps_refusing_is_given_up_on(lt):
    h = Handler(ok=False)
    ev = lt.sync(h, now=lambda: T, boottime=lambda: 1.0, sleep=lambda s: None, outcome_wait_s=0)
    assert not ev["ok"] and h.asked == lt.ASK_TRIES and answer(lt)["status"] == "none"


def test_it_waits_for_synctime_to_finish_before_judging(lt, monkeypatch):
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 3:
            synced(lt, "network")        # LoRa first, then network time disagreed and replaced it
    (lt.tmp / "time-source").write_text("lora\n")
    ev = lt.sync(Handler(offset=0.4), now=lambda: T, boottime=lambda: 1.0, sleep=sleep, outcome_wait_s=60)
    assert len(sleeps) == 3 and ev["clock_from"] == "network" and not ev["applied"]


def test_the_daemon_asks_once_per_boot_after_the_join(lt, monkeypatch):
    joins = iter([False, False, True])

    class Late(Handler):
        def is_joined(self):
            return next(joins, True)
    seen = []
    monkeypatch.setattr(lt, "sync", lambda h: seen.append(answer(lt)["status"]) or {"reason": "asked"})
    assert lt.sync_when_joined(Late(), sleep=lambda s: None)["reason"] == "asked"
    assert seen == ["asking"]                            # syncTime.sh waits for it meanwhile
    assert lt.sync_when_joined(Late(), sleep=lambda s: None)["reason"] == "already tried this boot"
    assert len(seen) == 1


def test_off_never_asks_and_says_so(lt):
    set_mode(lt, "off")
    h = Handler()
    assert lt.sync_when_joined(h)["reason"] == "lora_time_sync is off" and h.asked == 0
    assert answer(lt)["status"] == "none"


def test_the_old_modes_mean_on(lt):
    for value in ("healthy", "measure", "always", "on", "ON", "sometimes", None):
        set_mode(lt, value)
        assert lt.mode() == "on"
    set_mode(lt, "off")
    assert lt.mode() == "off"


def test_at_most_once_a_day_across_boots(lt, monkeypatch):
    assert lt.due(now=1_000_000)
    ask(lt, now=1_000_000)
    assert not lt.due(now=1_000_000 + 3600)              # an hourly wake later: not yet
    assert lt.due(now=1_000_000 + 24 * 3600)             # a day later
    assert lt.due(now=1_000_000 - 60)                    # the clock went back: ask
    monkeypatch.setattr(lt, "_boot_id", lambda: "boot-2")  # a new boot an hour later
    monkeypatch.setattr(lt.time, "time", lambda: 1_000_000 + 3600)
    (lt.tmp / "time-sync-done").unlink()
    assert lt.sync_when_joined(Handler())["reason"] == "asked within the last day"
    assert answer(lt) == {"boot": "boot-2", "status": "none", "reason": "asked within the last day"}


def test_no_ask_once_synctime_has_finished(lt):
    synced(lt, None)
    h = Handler()
    assert "too late" in lt.sync_when_joined(h)["reason"] and h.asked == 0


def test_synctime_finishing_during_the_join_wait_stops_it(lt):
    class Never(Handler):
        def is_joined(self):
            synced(lt, "network")
            return False
    h = Never()
    assert "before the join" in lt.sync_when_joined(h, sleep=lambda s: None)["reason"] and h.asked == 0
    assert answer(lt)["status"] == "none"


def test_the_daemon_gives_up_without_a_join(lt, monkeypatch):
    monkeypatch.setattr(lt.time, "monotonic", iter([0, 0, 10_000]).__next__)
    assert "not joined" in lt.sync_when_joined(Handler(joined=False), wait_s=60, sleep=lambda s: None)["reason"]
    assert answer(lt)["status"] == "none"


def test_the_bias_is_subtracted(lt):
    (lt.tmp / "runtime_config.json").write_text(json.dumps({"lora_time_bias_s": 0.21}))
    ev = ask(lt, Handler(offset=5.21), now=T)
    assert ev["offset_s"] == pytest.approx(5.0) and ev["bias_s"] == 0.21
    assert answer(lt)["unix"] == pytest.approx(T + 5.0)


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


def test_the_log_keeps_the_newest_lines(lt, monkeypatch):
    monkeypatch.setattr(lt, "LOG_MAX_LINES", 5)
    for i in range(8):
        lt.log_event({"i": i})
    assert [e["i"] for e in logged(lt)] == [3, 4, 5, 6, 7]


# --- lora_answer.py (syncTime.sh's side, as root) -------------------------------

@pytest.fixture
def ans(tmp_path):
    path = tmp_path / "answer.json"

    def write(**data):
        path.write_text(json.dumps({"boot": "b1", "status": "answer", "unix": T, "boottime": 50.0, **data}))

    def run(now_bt=60.0, clock=T, rtc_untrusted=False, boot="b1"):
        return la.main(str(path), boot=boot, now_bt=now_bt, clock=clock, rtc_untrusted=rtc_untrusted)
    write.run, write.path = run, path
    return write


def test_the_answer_is_carried_to_now_with_the_time_since_boot(ans, capsys):
    ans()
    assert ans.run(now_bt=60.0, clock=T - 3.0) == 0      # 10 s later; the clock 13 s behind
    ts, off = capsys.readouterr().out.split()
    assert float(ts) == pytest.approx(T + 10.0) and float(off) == pytest.approx(13.0)


def test_asking_none_and_other_boots(ans):
    assert ans.run() == 4                                # no file: no daemon, or not started
    ans(status="asking")
    assert ans.run() == 3
    ans(status="none")
    assert ans.run() == 4
    ans()
    assert ans.run(boot="b2") == 4                       # last boot's answer


def test_refused_answers(ans, capsys):
    for data, kw in (({"unix": "x"}, {}),
                     ({}, {"now_bt": 50.0 + 31 * 60}),        # too old
                     ({}, {"now_bt": 40.0}),                  # from before (boot time can't go back)
                     ({"unix": 1700000000.0}, {"clock": 1700000000.0}),   # before 2026
                     ({"unix": T + 2 * 86400}, {})):          # two days off, RTC looks valid
        ans(**data)
        assert ans.run(**kw) == 1, data
    ans(unix=T + 2 * 86400)
    assert ans.run(rtc_untrusted=True) == 0              # the RTC is known to be bad: allowed


def test_only_a_plain_file_is_read(ans, tmp_path):
    ans()
    link = tmp_path / "link.json"
    link.symlink_to(ans.path)
    assert la.read_answer(str(link)) is None
    assert la.read_answer(str(ans.path))["status"] == "answer"


# --- the IP and GPS paths still correct a clock from LoRa time --------------------

@pytest.fixture
def ip(tmp_path, monkeypatch):
    helper = tmp_path / "set-clock"
    helper.write_text("")
    monkeypatch.setattr(transmit_ip, "SET_CLOCK", str(helper))
    monkeypatch.setattr(transmit_ip, "TIME_SOURCE", str(tmp_path / "time-source"))
    monkeypatch.setattr(transmit_ip, "CLOCK_CHECKED", str(tmp_path / "checked"))
    monkeypatch.setattr(transmit_ip.time, "time", lambda: T)
    class Called(list):
        pass
    called = Called()
    monkeypatch.setattr(transmit_ip.subprocess, "run", lambda *a, **k: called.append(a[0]) or
                        subprocess.CompletedProcess(a, 0, "", ""))
    called.tmp = tmp_path
    return called


def test_server_time_replaces_lora_time_only_when_off_by_more_than_the_tolerance(ip, monkeypatch):
    from email.utils import formatdate
    (ip.tmp / "time-source").write_text("lora\n")
    monkeypatch.setattr("tools.capture_time.mark_clock_verified", lambda: None)
    assert transmit_ip.correct_clock({"server_date": formatdate(T + 1, usegmt=True), "rtt_s": 0}) is None
    assert not ip
    (ip.tmp / "checked").unlink()
    transmit_ip.correct_clock({"server_date": formatdate(T + 30, usegmt=True), "rtt_s": 0})
    assert ip and ip[0][:2] == ["doas", str(ip.tmp / "set-clock")]


def test_server_time_leaves_network_and_gps_time(ip):
    from email.utils import formatdate
    for source in ("network", "gps"):
        (ip.tmp / "time-source").write_text(source + "\n")
        transmit_ip.correct_clock({"server_date": formatdate(T + 30, usegmt=True), "rtt_s": 0})
    assert not ip


def gps_fix(lt, monkeypatch, tmp_path, clock_from, fix_offset):
    source = tmp_path / "gps-time-source"
    if clock_from:
        source.write_text(clock_from + "\n")
    monkeypatch.setattr(gps_window, "TIME_SOURCE", str(source))
    monkeypatch.setattr(gps_window.time, "time", lambda: T)
    ran = []
    monkeypatch.setattr(gps_window.subprocess, "run", lambda *a, **k: ran.append(a[0]) or
                        subprocess.CompletedProcess(a, 0, "", ""))

    class Packet:
        time = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(T + fix_offset)) + f".{int(fix_offset * 1000) % 1000:03d}Z"
    gps_window._set_clock_from(Packet())
    return ran, logged(lt)[-1]


def test_the_gps_window_logs_its_offset_and_leaves_a_lora_clock_that_agrees(lt, monkeypatch, tmp_path):
    ran, ev = gps_fix(lt, monkeypatch, tmp_path, "lora", 0.25)
    assert ev["source"] == "gps" and ev["clock_from"] == "lora" and not ev["applied"]
    assert ev["offset_s"] == pytest.approx(0.25, abs=1e-3) and not ran


def test_the_gps_window_corrects_a_lora_clock_that_is_off(lt, monkeypatch, tmp_path):
    ran, ev = gps_fix(lt, monkeypatch, tmp_path, "lora", 5.0)
    assert ev["applied"] and ran[0][-1] == "gps"


def test_the_gps_window_leaves_network_time(lt, monkeypatch, tmp_path):
    ran, ev = gps_fix(lt, monkeypatch, tmp_path, "network", 5.0)
    assert not ev["applied"] and not ran


def test_the_helper_takes_only_known_sources():
    path = os.path.join(os.path.dirname(__file__), "..", "config", "watercam-set-clock")
    text = open(path).read()
    assert 'source=${2:-server}' in text and '^(server|gps)$' in text
    for bad in ("'; rm -rf /'", "lora"):
        r = subprocess.run(["bash", path, "1794287896", bad], capture_output=True, text=True)
        assert r.returncode == 2 and "source must be" in r.stderr


@pytest.mark.parametrize("ts", ["1794287896.1234567", "1794287896.", ".5", "1e9", "-1794287896",
                                "1794287896.5; reboot", "179428789", ""])
def test_the_helper_refuses_anything_but_a_plain_unix_time(ts):
    path = os.path.join(os.path.dirname(__file__), "..", "config", "watercam-set-clock")
    r = subprocess.run(["bash", path, ts, "server"], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stderr


@pytest.mark.parametrize("ts", ["1000000000.5", "4102444800.000"])
def test_the_helper_refuses_fractional_times_out_of_range(ts):
    path = os.path.join(os.path.dirname(__file__), "..", "config", "watercam-set-clock")
    r = subprocess.run(["bash", path, ts, "server"], capture_output=True, text=True)
    assert r.returncode == 2 and "out of range" in r.stderr
