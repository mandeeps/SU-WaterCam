"""Max-awake decision, startup-health setup warnings, drop counter, clock flag."""
import os
import struct

import pytest

from tools import capture_time, drop_counter, initial_health_check as hc, max_awake
from tools import lora_store_forward as lsf


# --- max awake -------------------------------------------------------------

def test_decide_shuts_down_only_past_limit_without_exemptions():
    assert max_awake.decide(61, 60, False, False, False) == "shutdown"
    assert max_awake.decide(60, 60, False, False, False).startswith("stay")
    assert max_awake.decide(None, 60, False, False, False).startswith("stay")


@pytest.mark.parametrize("session,hold,emergency", [(True, False, False), (False, True, False), (False, False, True)])
def test_decide_exemptions(session, hold, emergency):
    assert max_awake.decide(500, 60, session, hold, emergency).startswith("stay")


def test_decide_zero_disables():
    assert max_awake.decide(5000, 0, False, False, False).startswith("stay")


def test_max_minutes_default_and_bad_values():
    assert max_awake.max_minutes({}) == 60
    assert max_awake.max_minutes({"max_awake_minutes": 30}) == 30
    assert max_awake.max_minutes({"max_awake_minutes": 0}) == 0
    assert max_awake.max_minutes({"max_awake_minutes": -5}) == 60
    assert max_awake.max_minutes({"max_awake_minutes": "x"}) == 60


def _setup_check(monkeypatch, tmp_path, uptime, cfg=None, session=None, hold=False):
    import json
    (tmp_path / "rc.json").write_text(json.dumps(cfg or {}))
    monkeypatch.setattr(max_awake, "RUNTIME_CONFIG", str(tmp_path / "rc.json"))
    (tmp_path / "s.json").write_text(json.dumps(session or {}))
    monkeypatch.setattr(max_awake, "SESSION_STATE", str(tmp_path / "s.json"))
    flag = tmp_path / "hold"
    if hold:
        flag.write_text("")
    monkeypatch.setattr(max_awake, "HOLD_FLAG", str(flag))
    monkeypatch.setattr(max_awake, "uptime_minutes", lambda: uptime)
    calls = []
    monkeypatch.setattr(max_awake.subprocess, "call", lambda cmd, timeout=None: calls.append(cmd) or 0)
    return calls


def test_check_uses_the_app_shutdown_command(monkeypatch, tmp_path):
    calls = _setup_check(monkeypatch, tmp_path, 90)
    assert max_awake.check() == "shutdown"
    assert calls == [["doas", "/usr/sbin/shutdown", "-h", "now"]]


def test_check_dry_run_and_session_and_hold(monkeypatch, tmp_path):
    calls = _setup_check(monkeypatch, tmp_path, 90)
    max_awake.check(dry_run=True)
    assert calls == []
    calls = _setup_check(monkeypatch, tmp_path, 90, session={"active": True})
    assert max_awake.check().startswith("stay")
    calls = _setup_check(monkeypatch, tmp_path, 90, hold=True)
    assert max_awake.check().startswith("stay")
    calls = _setup_check(monkeypatch, tmp_path, 90, cfg={"emergency_mode": True})
    assert max_awake.check().startswith("stay")
    assert calls == []


def test_emergency_exempt_only_until_deadline():
    now = 1_800_000_000.0
    cfg = {"emergency_mode": True, "emergency_since": now - 3600, "emergency_max_hours": 2}
    assert not max_awake.emergency_expired(cfg, now=now, uptime_min=90)
    cfg["emergency_since"] = now - (2 * 3600 + 14 * 60)        # inside the 15 min margin
    assert not max_awake.emergency_expired(cfg, now=now, uptime_min=90)
    cfg["emergency_since"] = now - (2 * 3600 + 16 * 60)
    assert max_awake.emergency_expired(cfg, now=now, uptime_min=90)
    assert max_awake.decide(500, 60, False, False, True, emergency_over=True) == "shutdown"
    # the session and the hold flag still keep it up
    assert max_awake.decide(500, 60, True, False, True, emergency_over=True).startswith("stay")
    assert max_awake.decide(500, 60, False, True, True, emergency_over=True).startswith("stay")
    assert not max_awake.emergency_expired({"emergency_mode": False, "emergency_since": 1}, now=now)


def test_emergency_default_hours_and_no_expiry():
    now = 1_800_000_000.0
    # 21 91 00 leaves emergency_max_hours unset: 24 h
    cfg = {"emergency_mode": True, "emergency_since": now - 23 * 3600}
    assert not max_awake.emergency_expired(cfg, now=now, uptime_min=90)
    cfg["emergency_since"] = now - 25 * 3600
    assert max_awake.emergency_expired(cfg, now=now, uptime_min=90)
    cfg["emergency_max_hours"] = "x"                          # garbled: the default
    assert max_awake.emergency_expired(cfg, now=now, uptime_min=90)
    cfg["emergency_max_hours"] = 0                            # 0 = never, as call_shutdown()
    assert not max_awake.emergency_expired(cfg, now=now, uptime_min=10 ** 6)


@pytest.mark.parametrize("since", [None, "garbage", True, float("nan"), 1_800_000_000.0 + 86400])
def test_emergency_bad_since_counts_from_boot(since):
    now = 1_800_000_000.0
    cfg = {"emergency_mode": True, "emergency_since": since, "emergency_max_hours": 1}
    assert not max_awake.emergency_expired(cfg, now=now, uptime_min=70)
    assert max_awake.emergency_expired(cfg, now=now, uptime_min=80)
    assert not max_awake.emergency_expired(cfg, now=now, uptime_min=None)


def test_check_shuts_down_expired_emergency_and_clears_it(monkeypatch, tmp_path):
    import json
    import time as _time
    cfg = {"emergency_mode": True, "emergency_since": _time.time() - 30 * 3600, "x": 1}
    calls = _setup_check(monkeypatch, tmp_path, 90, cfg=cfg)
    assert max_awake.check(dry_run=True) == "shutdown"
    assert calls == [] and json.loads((tmp_path / "rc.json").read_text())["emergency_mode"] is True
    assert max_awake.check() == "shutdown"
    assert calls == [["doas", "/usr/sbin/shutdown", "-h", "now"]]
    saved = json.loads((tmp_path / "rc.json").read_text())
    assert saved["emergency_mode"] is False and saved["emergency_since"] is None and saved["x"] == 1


# --- startup health setup warnings -----------------------------------------

def test_parse_pinctrl():
    # as read on 005 (trixie) with gpio=5=op,dh, and before it
    assert hc.parse_pinctrl(" 5: op -- pu | hi // GPIO5 = output") == {"func": "op", "level": "hi"}
    assert hc.parse_pinctrl("5: op dh pu | hi // GPIO5 = output") == {"func": "op", "level": "hi"}
    assert hc.parse_pinctrl(" 5: ip    pu | hi // GPIO5 = input") == {"func": "ip", "level": "hi"}
    assert hc.parse_pinctrl(" 5: op -- pu | lo // GPIO5 = output") == {"func": "op", "level": "lo"}
    assert hc.parse_pinctrl("") == {}
    assert hc.parse_pinctrl("garbage") == {}


def test_has_mdot(tmp_path):
    on = tmp_path / "a.txt"
    on.write_text("dtparam=spi=on\ndtoverlay=uart5\n")
    off = tmp_path / "b.txt"
    off.write_text("# dtoverlay=uart5\n")
    assert hc.has_mdot([str(on)]) is True
    assert hc.has_mdot([str(off)]) is False
    assert hc.has_mdot([str(tmp_path / "missing")]) is False


def test_setup_warnings():
    good = {"func": "op", "level": "hi"}
    assert hc.setup_warnings(True, good, 0x26, True) == []
    assert any("gpio5" in w for w in hc.setup_warnings(True, {"func": "ip", "level": "pu"}, 0x26, True))
    assert any("gpio5" in w for w in hc.setup_warnings(True, {}, None, True))
    assert any("l3v7" in w for w in hc.setup_warnings(True, good, 0x37, True))
    assert any("remote_debug_timer" in w for w in hc.setup_warnings(True, good, 0x26, False))
    # a handheld (no mDot) is never asked about GPIO5
    assert hc.setup_warnings(False, {}, 0x37, True) == []


# --- dropped store-and-forward counter -------------------------------------

def test_counter_persists_and_sums(tmp_path):
    path = str(tmp_path / "sf_dropped.json")
    assert drop_counter.total(path) == 0
    drop_counter.record("lora", path=path)
    drop_counter.record("ip", 2, path=path)
    assert drop_counter.counts(path) == {"lora": 1, "ip": 2}
    assert drop_counter.total(path) == 3
    (tmp_path / "sf_dropped.json").write_text("not json")
    assert drop_counter.total(path) == 0


def test_lora_queue_counts_cap_and_age_drops(tmp_path):
    q = str(tmp_path / "lora_pending")
    for i in range(4):
        assert lsf.enqueue("aa", None, 1_000 + i, queue_dir=q, max_depth=2)
    assert drop_counter.counts(drop_counter.path_for(q))["lora"] == 2   # cap
    # age: everything left is older than the limit
    class H:
        def transmit(self, _): return True
    lsf.drain(H(), queue_dir=q, max_age_days=-1)
    assert drop_counter.counts(drop_counter.path_for(q))["lora"] == 4


# --- clock flag --------------------------------------------------------------

def test_capture_flagged_until_clock_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_time, "TIME_SOURCE", str(tmp_path / "source"))
    monkeypatch.setattr(capture_time, "CLOCK_VERIFIED", str(tmp_path / "verified"))
    monkeypatch.setattr(capture_time, "_boot_id", lambda: "boot-1")
    d1 = tmp_path / "c1"; d1.mkdir()
    capture_time.write_capture_time(str(d1), 1_000)
    assert capture_time.clock_unverified(str(d1)) is True

    (tmp_path / "source").write_text("network")          # syncTime.sh ran
    d2 = tmp_path / "c2"; d2.mkdir()
    capture_time.write_capture_time(str(d2), 2_000)
    assert capture_time.clock_unverified(str(d2)) is False

    os.unlink(tmp_path / "source")
    capture_time.mark_clock_verified()                    # server agreed with the clock
    assert capture_time.clock_verified() is True
    monkeypatch.setattr(capture_time, "_boot_id", lambda: "boot-2")
    assert capture_time.clock_verified() is False          # a new boot starts unverified


def test_lora_packet_carries_new_fields():
    from tools.lora_handler_concurrent import _encode_compressed_packet as compressed_encoding
    pkt = compressed_encoding({"timestamp": 1, "sf_dropped": 7, "clock_unverified": 1})
    assert bytes([0x01, 0x0C]) + struct.pack(">I", 7) in pkt
    assert pkt.endswith(bytes([0x01, 0x0D, 0x01]))
