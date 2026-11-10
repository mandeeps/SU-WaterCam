"""tools/lora_health.py and tools/wifi_policy.py: LoRa that really works, WiFi only when needed."""
from unittest.mock import MagicMock, patch

import pytest

from tools import lora_health as lh
from tools import wifi_policy as wp


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(lh, "HISTORY_PATH", str(tmp_path / "lora_health.json"))
    monkeypatch.setattr(lh, "CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(lh, "_boot_id", lambda: "boot-1")
    monkeypatch.setattr(lh, "_min_margin", lambda: 5)
    monkeypatch.setattr(lh.time, "sleep", lambda s: None)


def handler(joined=True, checks=({"ok": True, "margin_db": 14, "gateways": 1},)):
    h = MagicMock()
    h.is_joined.return_value = joined
    h.link_check.side_effect = list(checks) * 5
    return h


def test_healthy_with_an_answered_link_check():
    r = lh.assess(handler())
    assert r["healthy"] and r["margin_db"] == 14 and r["gateways"] == 1


def test_joined_but_no_gateway_is_not_healthy():
    # the false positive: the saved session says joined, nobody answers
    r = lh.assess(handler(checks=({"ok": False, "gateways": 0, "error": "ERROR"},)))
    assert not r["healthy"] and "link check failed" in r["reason"]


def test_second_attempt_can_succeed():
    h = handler(checks=({"ok": False, "error": "ERROR"}, {"ok": True, "margin_db": 9, "gateways": 2}))
    assert lh.assess(h)["healthy"] and h.link_check.call_count == 2


def test_not_joined_and_weak_margin():
    assert lh.assess(handler(joined=False))["reason"] == "not joined"
    r = lh.assess(handler(checks=({"ok": True, "margin_db": 2, "gateways": 1},)), force=True)
    assert not r["healthy"] and "weak link" in r["reason"]


def test_flapping_link_is_not_reliable():
    for ok in (False, False):
        lh.assess(handler(checks=({"ok": ok, "margin_db": 10 if ok else None, "gateways": int(ok)},)), force=True)
    r = lh.assess(handler(), force=True)              # this one works, but 2 of the last 3 failed
    assert not r["healthy"] and "unreliable" in r["reason"]


def test_assessed_once_per_boot(monkeypatch):
    h = handler()
    lh.assess(h)
    lh.assess(h)
    assert h.link_check.call_count == 1
    monkeypatch.setattr(lh, "_boot_id", lambda: "boot-2")
    lh.assess(h)
    assert h.link_check.call_count == 2


@pytest.mark.parametrize("health, session, keep, moved, want", [
    ({"healthy": True}, False, False, False, False),
    ({"healthy": False, "reason": "x"}, False, False, False, True),
    ({"healthy": True}, True, False, False, True),
    ({"healthy": True}, False, True, False, True),
    ({"healthy": True}, False, False, True, True),
])
def test_wifi_policy(monkeypatch, health, session, keep, moved, want):
    monkeypatch.setattr(wp, "_session_active", lambda: session)
    monkeypatch.setattr(wp, "_keep_on_param", lambda: keep)
    monkeypatch.setattr(wp, "_moved", lambda: moved)
    monkeypatch.setattr(wp, "radio_is_on", lambda: not want)
    with patch.object(wp, "radio", return_value=True) as radio:
        wp.apply(health)
    radio.assert_called_once_with(want)


def test_radio_never_touched_off_a_pi(monkeypatch):
    monkeypatch.setattr(wp, "_on_a_pi", lambda: False)
    with patch("tools.wifi_policy.subprocess.run") as run:
        assert wp.radio(False) is False and wp.radio_is_on() is None
    run.assert_not_called()


def _serial_handler(reply: bytes):
    """A LoRaHandler with only what link_check() uses; the mDot answers the AT write."""
    import threading
    from test_lora_reception import FakeMdot
    from tools.lora_handler_concurrent import LoRaHandler
    h = LoRaHandler.__new__(LoRaHandler)
    h.transmit_lock = threading.Lock()
    h._lock_fd = open("/dev/null", "w")
    h.ser = FakeMdot(on_write=lambda data, ser: ser.feed(reply))
    return h


def test_link_check_parses_the_mdot_reply():
    h = _serial_handler(b"AT+NLC\r\nPACKET:PORT=0,SIZE=0,DATA=\r\n14,1\r\n\r\nOK\r\n")
    with patch("tools.lora_handler_concurrent.fcntl.lockf"):
        assert h.link_check() == {"ok": True, "margin_db": 14, "gateways": 1, "error": None}


def test_link_check_error_reply():
    h = _serial_handler(b"AT+NLC\r\nERROR\r\n")
    with patch("tools.lora_handler_concurrent.fcntl.lockf"):
        r = h.link_check()
    assert r["ok"] is False and r["error"] == "ERROR"



def test_cellular_session_leaves_wifi_policy_alone(tmp_path, monkeypatch):
    import json
    state = tmp_path / "remote_debug.json"
    monkeypatch.setattr(wp, "REMOTE_STATE", str(state))
    state.write_text(json.dumps({"active": True, "mode": "cellular"}))
    assert wp._session_active() is False
    state.write_text(json.dumps({"active": True, "mode": "auto"}))
    assert wp._session_active() is True


# ── the check history: 30 days, the verdict and this boot's IP uploads ────────

def _hist():
    import json
    with open(lh.HISTORY_PATH) as f:
        return json.load(f)


def test_history_records_the_verdict_and_reason():
    lh.assess(handler())
    lh.assess(handler(checks=({"ok": True, "margin_db": 2, "gateways": 1},)), force=True)
    healthy, weak = _hist()
    assert healthy["healthy"] is True and "14 dB margin" in healthy["reason"] and healthy["boot"] == "boot-1"
    assert weak["healthy"] is False and "weak link" in weak["reason"] and weak["ok"] is False


def test_history_keeps_30_days_and_at_most_the_cap(monkeypatch):
    import json
    now = 2_000_000_000
    old = [{"t": now - lh.HISTORY_MAX_AGE_S - 60, "ok": False}]
    recent = [{"t": now - 3600 * i, "ok": True} for i in range(50, 0, -1)]
    with open(lh.HISTORY_PATH, "w") as f:
        json.dump(old + recent, f)
    monkeypatch.setattr(lh.time, "time", lambda: now)
    lh.assess(handler(), force=True)
    hist = _hist()
    assert len(hist) == 51 and all(now - h["t"] <= lh.HISTORY_MAX_AGE_S for h in hist)   # 21 more than before
    monkeypatch.setattr(lh, "HISTORY_MAX", 10)
    lh.assess(handler(), force=True)
    assert len(_hist()) == 10 and _hist()[-1]["t"] == now


def test_history_is_written_compact():
    lh.assess(handler())
    with open(lh.HISTORY_PATH) as f:
        text = f.read()
    assert "\n" not in text.strip() and "\": " not in text


def test_note_ip_counts_on_this_boots_entry(monkeypatch):
    lh.note_ip(True)                                  # no history yet: nothing to note on
    lh.assess(handler())
    lh.note_ip(True)
    lh.note_ip(False)
    lh.note_ip(True)
    assert _hist()[-1]["ip_sent"] == 2 and _hist()[-1]["ip_failed"] == 1
    monkeypatch.setattr(lh, "_boot_id", lambda: "boot-2")
    lh.note_ip(True)                                  # a later boot with no link check
    assert _hist()[-1]["ip_sent"] == 2


def test_a_healthy_wake_without_ip_counts_shows_the_skip():
    lh.assess(handler())
    last = _hist()[-1]
    assert last["healthy"] and "ip_sent" not in last and "ip_failed" not in last
