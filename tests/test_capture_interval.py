"""Capture interval: emergency_frequency drives get_time's period (tools/photo_interval_watcher.py).

Normal wakes keep photo_interval (60 s): three captures, then shutdown. Emergency
mode and keep-awake holds capture every emergency_frequency minutes (default 10).
The period is changed with update_periodicity, so no tick is skipped: skipped
ticks can't happen and iteration counting is unchanged.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.photo_interval_watcher as piw
import tools.systemd_watchdog as wd
from tools import lora_runtime_integration as lri
from ticktalkpython.FiringRule import TTFiringRuleType


def _graph():
    sqs = [SimpleNamespace(sq_name="get_time-4", firing_rule_type=TTFiringRuleType.TimedRetrigger)]
    return SimpleNamespace(graph_name="ttmain", sqs=sqs)


class _Rtm:
    def __init__(self):
        self.calls = []

    def update_periodicity(self, graph, sq, period, phase):
        self.calls.append((graph, sq, period // 1_000_000, phase))


class _Env:
    """A config and remote-session file in tmp_path, and a watcher over them."""

    def __init__(self, tmp_path, **cfg):
        self.cfg = tmp_path / "runtime_config.json"
        (tmp_path / "data").mkdir()
        self.remote = tmp_path / "data" / "remote_debug.json"
        self.rtm = _Rtm()
        self.write(**cfg)
        self.watcher = piw.PhotoIntervalWatcher(self.rtm, _graph(), str(self.cfg))

    def _bump(self, path):
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    def write(self, **cfg):
        self.cfg.write_text(json.dumps(cfg))
        self._bump(self.cfg)

    def set_remote(self, active):
        self.remote.write_text(json.dumps({"active": active}))
        self._bump(self.remote)


def test_defaults_are_ten_minutes_and_three_captures():
    assert lri.DEFAULT_EMERGENCY_FREQUENCY == 10
    assert lri.DEFAULT_SHUTDOWN_LIMIT == 3
    assert piw.DEFAULT_EMERGENCY_MIN == lri.DEFAULT_EMERGENCY_FREQUENCY
    assert piw.DEFAULT_SHUTDOWN_LIMIT == lri.DEFAULT_SHUTDOWN_LIMIT
    assert (piw.MIN_EMERGENCY_MIN, piw.MAX_EMERGENCY_MIN) == lri.LoRaRuntimeManager._PARAM_RANGES["emergency_frequency"]


def test_normal_wake_is_unchanged():
    cfg = {"photo_interval": 60, "emergency_frequency": 10, "iteration_count": 1}
    assert piw.wanted_period_s(cfg) == 60
    # a remote session doesn't slow the wake's own three captures...
    assert piw.wanted_period_s(dict(cfg, iteration_count=2), remote_active=True) == 60
    # ...only the time it holds the node up after them
    assert piw.wanted_period_s(dict(cfg, iteration_count=3), remote_active=True) == 600


def test_emergency_uses_emergency_frequency_in_minutes():
    assert piw.wanted_period_s({"emergency_mode": True, "photo_interval": 60}) == 600
    assert piw.wanted_period_s({"emergency_mode": True, "emergency_frequency": 3}) == 180


@pytest.mark.parametrize("value, seconds", [(0, 60), (1, 60), (240, 14400), (999, 14400), ("15", 900), ("x", 600)])
def test_emergency_frequency_is_clamped(value, seconds):
    assert piw.wanted_period_s({"emergency_mode": True, "emergency_frequency": value}) == seconds


def test_entering_and_leaving_emergency(tmp_path):
    env = _Env(tmp_path, photo_interval=60, emergency_frequency=10)
    assert env.watcher.check_once() is None            # 60 s is what the graph was compiled with
    env.write(photo_interval=60, emergency_frequency=10, emergency_mode=True)
    assert env.watcher.check_once() == 600
    env.write(photo_interval=60, emergency_frequency=10, emergency_mode=False)
    assert env.watcher.check_once() == 60
    # a phase of None keeps the capture loop's own phase
    assert env.rtm.calls == [("ttmain", "get_time-4", 600, None), ("ttmain", "get_time-4", 60, None)]


def test_changing_emergency_frequency_applies_once(tmp_path):
    env = _Env(tmp_path, emergency_mode=True, emergency_frequency=10)
    assert env.watcher.check_once() == 600
    env.write(emergency_mode=True, emergency_frequency=20)
    assert env.watcher.check_once() == 1200
    assert env.watcher.check_once() is None             # unchanged file: no second update
    env.write(emergency_mode=True, emergency_frequency=20, area_threshold=40)
    assert env.watcher.check_once() is None             # file changed, period didn't
    assert [c[2] for c in env.rtm.calls] == [600, 1200]


def test_remote_session_hold_follows_the_iteration_count(tmp_path):
    env = _Env(tmp_path, photo_interval=60, iteration_count=3)
    assert env.watcher.check_once() is None
    env.set_remote(True)
    assert env.watcher.check_once() == 600
    env.set_remote(False)
    assert env.watcher.check_once() == 60


def test_remote_session_during_the_wakes_own_captures_changes_nothing(tmp_path):
    env = _Env(tmp_path, photo_interval=60, iteration_count=1)
    env.set_remote(True)
    assert env.watcher.check_once() is None
    assert env.rtm.calls == []


def test_unreadable_config_is_retried(tmp_path):
    env = _Env(tmp_path, emergency_mode=True)
    env.cfg.write_text("{not json")
    env._bump(env.cfg)
    assert env.watcher.check_once() is None
    env.write(emergency_mode=True, emergency_frequency=5)
    assert env.watcher.check_once() == 300


def test_graph_keeps_one_tick_per_capture():
    """No tick is skipped: get_time is the only periodic SQ and the watcher only
    changes its period, so call_shutdown still runs once per capture."""
    import ticktalk_main
    src = open(ticktalk_main.__file__).read()
    assert "TTPeriod=60_000_000" in src
    assert "flir_planb" not in src and "TTFinishByOtherwise(" not in src


# --- watchdog heartbeat between slow iterations ------------------------------

def test_progress_marker_roundtrip(tmp_path):
    marker = str(tmp_path / "last_iteration")
    assert wd.last_progress(marker) is None
    wd.note_progress(marker)
    assert abs(wd.last_progress(marker) - __import__("time").time()) < 5


def _beating_watcher(tmp_path, monkeypatch, progress_age_s, clock):
    sent = []
    monkeypatch.setattr(wd, "heartbeat", lambda: sent.append(1) or True)
    monkeypatch.setattr(wd, "last_progress", lambda path=None: clock[0] - progress_age_s)
    env = _Env(tmp_path, emergency_mode=True)
    w = piw.PhotoIntervalWatcher(env.rtm, _graph(), str(env.cfg), clock=lambda: clock[0])
    w.applied_s = 600
    return w, sent


def test_heartbeat_sent_while_the_last_iteration_is_recent(tmp_path, monkeypatch):
    clock = [10_000.0]
    w, sent = _beating_watcher(tmp_path, monkeypatch, progress_age_s=599, clock=clock)
    assert w.beat_once() is True
    assert w.beat_once() is False                       # at most one a minute
    clock[0] += 61
    assert w.beat_once() is True
    assert len(sent) == 2


def test_heartbeat_stops_when_the_graph_stalls(tmp_path, monkeypatch):
    clock = [10_000.0]
    # last iteration older than the 10 min period plus 15 min
    w, sent = _beating_watcher(tmp_path, monkeypatch, progress_age_s=600 + 15 * 60 + 1, clock=clock)
    w._started = clock[0] - 10_000
    assert w.beat_once() is False and sent == []


def test_max_awake_waits_one_capture_interval_past_the_emergency_deadline():
    from tools import max_awake
    base = 24 * 60 + max_awake.EMERGENCY_MARGIN_MIN
    assert max_awake.emergency_limit_min({"emergency_max_hours": 24}) == base + 10
    assert max_awake.emergency_limit_min({"emergency_max_hours": 24, "emergency_frequency": 240}) == base + 240
    assert max_awake.emergency_limit_min({"emergency_max_hours": 0}) is None
