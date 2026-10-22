"""Apply the capture interval to the running capture loop.

ticktalk_main's capture loop is the get_time STREAM SQ, compiled with a fixed
TTPeriod of 60 s. This watcher changes that period while the graph runs:

  normal wake:                     photo_interval (seconds, runtime_config.json;
                                   there is no remote command for it: 14 94 is
                                   the flood-code frequency since #134)
  emergency mode, or a keep-awake  emergency_frequency (minutes, default 10,
  hold (a remote access session    1-240, set remotely by 13 93)
  once the wake's captures are
  done)

runrtm.py starts this watcher next to the runtime manager, the one place that
can reach the SQs' processes: it polls the config (and the remote session
state) and calls TTRuntimeManager.update_periodicity() when the wanted period
changes. A new period takes effect after the already-scheduled next capture,
so entering emergency mode captures within a minute and a changed
emergency_frequency applies from the next tick.

Every capture iteration sends systemd's watchdog heartbeat (call_shutdown), but
with a 10-240 min interval that is too rare for WatchdogSec=15min. So the
watcher also sends the heartbeat, for as long as the last iteration is no
older than the current period plus 15 min (tools/systemd_watchdog.py): a stalled
graph is still restarted.

It reads the JSON files directly rather than going through
lora_runtime_integration, so it stays independent of the LoRa daemon.
"""

import json
import os
import threading
import time

from ticktalkpython.FiringRule import TTFiringRuleType

# The compiled TTPeriod of get_time in ticktalk_main.py.
COMPILED_PERIOD_S = 60
# Below this a capture cycle (photo, co-registration, segmentation, transmit)
# can't finish before the next one starts.
MIN_PERIOD_S = 30
MAX_PERIOD_S = 1440
ROOT_TICKS_PER_S = 1_000_000
# emergency_frequency, minutes (same bounds and default as
# tools/lora_runtime_integration.py, which the watcher doesn't import)
DEFAULT_EMERGENCY_MIN = 10
MIN_EMERGENCY_MIN = 1
MAX_EMERGENCY_MIN = 240
DEFAULT_SHUTDOWN_LIMIT = 3
# The capture loop must have run within its period plus this for the
# watchdog heartbeat to be sent on its behalf
WATCHDOG_GRACE_S = 15 * 60
HEARTBEAT_EVERY_S = 60
CAPTURE_SQ_PREFIX = "get_time"


def find_capture_sq(graph):
    """Name of the periodic capture SQ (get_time-N), or None."""
    for sq in graph.sqs:
        if (sq.sq_name.startswith(CAPTURE_SQ_PREFIX)
                and getattr(sq, "firing_rule_type", None) == TTFiringRuleType.TimedRetrigger):
            return sq.sq_name
    return None


def _read_config(cfg_path):
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) else None


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clamp_photo_interval(value):
    """photo_interval in seconds, clamped; None if absent or not a number."""
    value = _as_int(value)
    if value is None:
        return None
    if value < MIN_PERIOD_S or value > MAX_PERIOD_S:
        clamped = min(max(value, MIN_PERIOD_S), MAX_PERIOD_S)
        print(f"⚠️ photo_interval {value}s outside {MIN_PERIOD_S}-{MAX_PERIOD_S}s; using {clamped}s", flush=True)
        value = clamped
    return value


def read_photo_interval(cfg_path):
    """photo_interval in seconds from the config file, clamped; None if absent or unreadable."""
    cfg = _read_config(cfg_path)
    return None if cfg is None else clamp_photo_interval(cfg.get("photo_interval"))


def emergency_period_s(cfg):
    """emergency_frequency (minutes) from a config dict as seconds, clamped; the default if absent."""
    minutes = _as_int(cfg.get("emergency_frequency"))
    if minutes is None:
        minutes = DEFAULT_EMERGENCY_MIN
    return min(max(minutes, MIN_EMERGENCY_MIN), MAX_EMERGENCY_MIN) * 60


def is_hold(cfg, remote_active):
    """True when the node stays awake past the normal wake: emergency mode, or a
    remote access session once the wake's captures are done (the shutdown check
    then keeps it up). Normal captures are never slowed before that."""
    if cfg.get("emergency_mode"):
        return True
    if remote_active:
        limit = _as_int(cfg.get("shutdown_iteration_limit"))
        limit = DEFAULT_SHUTDOWN_LIMIT if limit is None else limit
        return (_as_int(cfg.get("iteration_count")) or 0) >= limit
    return False


def wanted_period_s(cfg, remote_active=False):
    """The capture period in seconds for this config; None when the config has no usable photo_interval."""
    if is_hold(cfg, remote_active):
        return emergency_period_s(cfg)
    return clamp_photo_interval(cfg.get("photo_interval"))


def remote_session_active(path):
    try:
        with open(path) as f:
            return bool(json.load(f).get("active"))
    except (OSError, ValueError, AttributeError):
        return False


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


class PhotoIntervalWatcher:
    def __init__(self, rtm, graph, cfg_path, poll_s=5.0, remote_state_path=None, clock=time.time):
        self.rtm = rtm
        self.graph_name = graph.graph_name
        self.sq_name = find_capture_sq(graph)
        self.cfg_path = cfg_path
        self.remote_state_path = remote_state_path or os.path.join(
            os.path.dirname(os.path.abspath(cfg_path)), "data", "remote_debug.json")
        self.poll_s = poll_s
        self.applied_s = COMPILED_PERIOD_S
        self._stamp = None
        self._clock = clock
        self._started = clock()
        self._last_beat = None

    def check_once(self):
        """Apply the wanted period if the files changed and it differs. Returns the applied period or None."""
        cfg_mtime = _mtime(self.cfg_path)
        if cfg_mtime is None:
            return None
        stamp = (cfg_mtime, _mtime(self.remote_state_path))
        if stamp == self._stamp:
            return None
        self._stamp = stamp
        cfg = _read_config(self.cfg_path)
        if cfg is None:
            self._stamp = None      # unreadable now (mid-write?): look again
            return None
        remote = remote_session_active(self.remote_state_path)
        value = wanted_period_s(cfg, remote)
        if value is None or value == self.applied_s:
            return None
        # Phase None keeps the current one: the capture loop starts on arrival
        # (TTStartOnArrival), not on a period boundary.
        self.rtm.update_periodicity(self.graph_name, self.sq_name,
                                    value * ROOT_TICKS_PER_S, None)
        why = "emergency_frequency" if is_hold(cfg, remote) else "photo_interval"
        print(f"📸 Capture period {self.applied_s}s → {value}s ({why})", flush=True)
        self.applied_s = value
        return value

    def beat_once(self):
        """Send systemd's watchdog heartbeat if the capture loop is alive for its period. Returns True if sent."""
        now = self._clock()
        if self._last_beat is not None and now - self._last_beat < HEARTBEAT_EVERY_S:
            return False
        try:
            from tools.systemd_watchdog import heartbeat, last_progress
            last = max(last_progress() or 0.0, self._started)
            if now - last > self.applied_s + WATCHDOG_GRACE_S:
                return False
            self._last_beat = now
            return heartbeat()
        except Exception:
            return False

    def run(self):
        while True:
            # Sleep first: the graph has to be instantiated on its ensemble
            # before an update can reach the SQ.
            time.sleep(self.poll_s)
            try:
                self.check_once()
                self.beat_once()
            except Exception as e:  # never take the runtime down
                print(f"⚠️ capture interval watcher: {e}", flush=True)


def start_photo_interval_watcher(rtm, graph, cfg_path, poll_s=5.0):
    """Start the watcher thread; returns it, or None if the graph has no capture SQ."""
    watcher = PhotoIntervalWatcher(rtm, graph, cfg_path, poll_s)
    if watcher.sq_name is None:
        print("⚠️ capture interval watcher: no periodic get_time SQ in this graph", flush=True)
        return None
    threading.Thread(target=watcher.run, name="photo-interval-watcher", daemon=True).start()
    return watcher
