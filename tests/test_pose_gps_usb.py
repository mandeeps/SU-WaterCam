"""Pose levels (tools/pose_monitor.py), the GPS window (tools/gps_window.py) and
USB holders (tools/usb_power.py)."""
import math
import time
from unittest.mock import MagicMock, patch

import pytest

from tools import gps_window, pose_monitor as pm, usb_power
from tools.transmit_ip import apply_downlink_command, parse_downlink_frame

G0 = [0.0, 0.0, 9.81]
# The real functions, kept before conftest's autouse fixture stubs them per test
REAL_RUN_WINDOW = gps_window.run_window
REAL_RELEASE = usb_power.release


def _g(tilt_deg, noise=0.0):
    t = math.radians(tilt_deg + noise)
    return [9.81 * math.sin(t), 0.0, 9.81 * math.cos(t)]


def _samples(tilt_deg, spread=0.0, mag=0, q=(1.0, 0.0, 0.0, 0.0)):
    return [{"gravity": _g(tilt_deg, spread * (i / 4)), "quaternion": q, "calibration_status": (3, 3, 3, mag)}
            for i in range(5)]


def _with_reference():
    state = {}
    for _ in range(pm.REFERENCE_CAPTURES):
        r = pm.assess(_samples(0), state)
    assert r["note"] == "reference set" and state["level"] == pm.OK
    return state


def test_reference_needs_stable_captures():
    state = {}
    assert pm.assess(_samples(0), state)["level"] == pm.NO_REFERENCE
    assert pm.assess(_samples(2), state)["level"] == pm.NO_REFERENCE      # not stable yet
    pm.assess(_samples(2), state)
    assert pm.assess(_samples(2), state)["level"] == pm.OK                 # 3 stable in a row


def test_small_noise_stays_ok():
    state = _with_reference()
    for _ in range(10):
        assert pm.assess(_samples(0.6), state)["level"] == pm.OK


def test_one_degree_needs_three_captures_in_a_row():
    state = _with_reference()
    assert pm.assess(_samples(1.2), state)["level"] == pm.OK
    assert pm.assess(_samples(1.2), state)["level"] == pm.OK
    assert pm.assess(_samples(0.2), state)["level"] == pm.OK              # the run is broken
    for _ in range(2):
        pm.assess(_samples(1.2), state)
    assert pm.assess(_samples(1.2), state)["level"] == pm.GEOREF


def test_a_single_glitch_raises_nothing():
    state = _with_reference()
    assert pm.assess(_samples(12), state)["level"] == pm.OK               # one reading 12 deg off
    assert pm.assess(_samples(0.1), state)["level"] == pm.OK


def test_knock_needs_two_captures_and_asks_for_a_gps_fix():
    state = _with_reference()
    assert pm.assess(_samples(7), state)["level"] == pm.OK
    assert pm.assess(_samples(7), state)["level"] == pm.KNOCKED
    assert state["gps_wakes"] == 1


def test_removal_is_immediate():
    state = _with_reference()
    r = pm.assess(_samples(25), state)
    assert r["level"] == pm.REMOVED and state["gps_wakes"] == pm.GPS_WAKES_AFTER_REMOVED


def test_handling_spread_is_removal():
    state = _with_reference()
    assert pm.assess(_samples(0, spread=4), state)["level"] == pm.REMOVED


def test_gps_move_is_removal():
    state = _with_reference()
    assert pm.assess(_samples(0), state, gps_moved=True)["level"] == pm.REMOVED


def test_levels_are_sticky_until_reset(tmp_path):
    state = _with_reference()
    pm.assess(_samples(25), state)
    assert pm.assess(_samples(0), state)["level"] == pm.REMOVED


def test_heading_only_counts_with_full_magnetometer():
    state = {}
    q0 = (1.0, 0.0, 0.0, 0.0)
    for _ in range(3):
        pm.assess(_samples(0, mag=3, q=q0), state)
    turned = (math.cos(math.radians(2.5)), 0.0, 0.0, math.sin(math.radians(2.5)))   # 5 deg about z
    for _ in range(3):
        r = pm.assess(_samples(0, mag=3, q=turned), state)
    assert r["level"] == pm.GEOREF
    state2 = {}
    for _ in range(3):
        pm.assess(_samples(0, mag=1, q=q0), state2)
    for _ in range(3):
        r = pm.assess(_samples(0, mag=1, q=turned), state2)
    assert r["level"] == pm.OK and r["rotation"] is None


def test_check_reads_once_per_capture_and_reset(tmp_path):
    path = str(tmp_path / "pose.json")
    for i in range(3):
        pm.check(f"cap{i}", path=path, samples=_samples(0))
    first = pm.check("cap9", path=path, samples=_samples(0))
    assert pm.check("cap9", path=path, samples=_samples(40)) is first     # same capture: same result
    pm.reset(path=path)
    assert pm._load(path)["level"] == pm.NO_REFERENCE


def test_fields():
    assert pm.fields({"level": 1, "tilt": 1.234}) == {"pose_level": 1, "pose_tilt_tenths": 12}
    assert pm.fields({"level": 255, "tilt": None}) == {"pose_level": 255}


# ── GPS window ───────────────────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    from tools import get_gps
    entry = {}
    monkeypatch.setattr(get_gps, "_read_cache", lambda: dict(entry) if entry else None)
    monkeypatch.setattr(gps_window, "_interval_h", lambda: 24)
    return entry


def test_due_reasons(cache):
    assert gps_window.reasons() == ["no fix yet"]
    cache.update(gps_lat=1.0, gps_lon=2.0, fix_time=time.time() - 3600)
    assert gps_window.reasons() == []
    assert gps_window.reasons(requested=True) == ["requested by the server"]
    assert gps_window.reasons(pose_wake=True) == ["knocked or removed"]
    cache["fix_time"] = time.time() - 25 * 3600
    assert gps_window.reasons()[0].startswith("last fix 25 h ago")


def test_window_holds_usb_only_while_it_runs(monkeypatch):
    monkeypatch.setattr(gps_window, "run_window", REAL_RUN_WINDOW)
    calls = []
    monkeypatch.setattr(usb_power, "acquire", lambda n: calls.append(("on", n)) or True)
    monkeypatch.setattr(usb_power, "release", lambda n: calls.append(("off", n)) or True)
    monkeypatch.setattr(usb_power, "wait_for", lambda *a: True)
    monkeypatch.setattr(gps_window, "_wait_for_fix", lambda t: {"location": {"gps_lat": 1.0, "gps_lon": 2.0},
                                                                  "packet": MagicMock(time=None)})
    assert gps_window.run_window(["test"])
    assert calls == [("on", "gps"), ("off", "gps")]


def test_window_runs_once_per_boot(monkeypatch, tmp_path, cache):
    monkeypatch.setattr(gps_window, "BOOT_MARK", str(tmp_path / "mark"))
    monkeypatch.setattr(gps_window, "REQUEST_FILE", str(tmp_path / "req.json"))
    ran = []
    monkeypatch.setattr(gps_window, "run_window", lambda why, **kw: ran.append(why) or True)
    with patch("tools.pose_monitor.take_gps_wake", return_value=False):
        assert gps_window.start_if_due(background=False) == ["no fix yet"]
        assert gps_window.start_if_due(background=False) is None
    assert len(ran) == 1


def test_gps_skips_gpsd_with_the_modem_off(monkeypatch):
    from tools import get_gps
    monkeypatch.setattr(get_gps, "GNSS_DEVICE", "/nonexistent/ttyUSB1")
    monkeypatch.setattr(get_gps, "_ensure_connected", MagicMock(side_effect=AssertionError("asked gpsd")))
    monkeypatch.setattr(get_gps, "cached_fix", lambda: ({"gps_lat": 1.0, "gps_lon": 2.0}, 7200.0))
    loc, packet = get_gps.get_location_with_retry()
    assert loc["gps_lat"] == 1.0 and packet is None and get_gps.last_fix["source"] == "cached"


# ── USB holders ──────────────────────────────────────────────────────────────

def test_last_release_turns_usb_off(monkeypatch, tmp_path):
    monkeypatch.setattr(usb_power, "HOLDERS_DIR", str(tmp_path / "usb"))
    monkeypatch.setattr(usb_power, "release", REAL_RELEASE)
    monkeypatch.setattr(usb_power, "_powered", False)
    switched = []
    monkeypatch.setattr(usb_power, "_switch", lambda a: switched.append(a) or True)
    usb_power.acquire("gps")
    usb_power.acquire("cellular")
    assert switched == ["on"]                       # once per process
    assert usb_power.release("gps") is False        # cellular still holds it
    assert usb_power.release("cellular") is True
    assert switched == ["on", "off"]


# ── downlinks ────────────────────────────────────────────────────────────────

def test_gps_fix_and_pose_reset_downlinks(monkeypatch):
    with patch("tools.gps_window.request") as req, patch("tools.pose_monitor.reset") as reset:
        parts = parse_downlink_frame(bytes.fromhex("1999199a"))
        out = apply_downlink_command({"parts": parts}, set_param_fn=lambda k, v: None)
    req.assert_called_once()
    reset.assert_called_once()
    assert out["applied"] == ["gps_fix_requested", "pose_reference_reset"] and not out["skipped"]


def test_a_holder_whose_process_ended_does_not_keep_usb_on(monkeypatch, tmp_path):
    monkeypatch.setattr(usb_power, "HOLDERS_DIR", str(tmp_path / "usb"))
    monkeypatch.setattr(usb_power, "release", REAL_RELEASE)
    switched = []
    monkeypatch.setattr(usb_power, "_switch", lambda a: switched.append(a) or True)
    (tmp_path / "usb").mkdir()
    (tmp_path / "usb" / "audio").write_text("999999999")      # a pid that can't exist
    usb_power.acquire("gps")
    assert usb_power.holders() == ["gps"]
    assert usb_power.release("gps") is True and switched[-1] == "off"
