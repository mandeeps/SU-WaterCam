"""Apply photo_interval (seconds) to the running capture loop.

ticktalk_main's capture loop is the get_time STREAM SQ, compiled with a fixed
TTPeriod of 60 s. photo_interval in runtime_config.json (set over LoRa
command 14 / TLV 14 94, or by editing the file) is that period in seconds.
runrtm.py starts this watcher next to the runtime manager: it polls the file
and calls TTRuntimeManager.update_periodicity() when the value changes. A new
period takes effect after the already-scheduled next capture.

It reads the JSON file directly rather than going through
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
# can't finish before the next one starts; also rejects the 0-5 index values
# the API's "flood code frequency" command sends on 14 94.
MIN_PERIOD_S = 30
MAX_PERIOD_S = 1440
ROOT_TICKS_PER_S = 1_000_000
CAPTURE_SQ_PREFIX = "get_time"


def find_capture_sq(graph):
    """Name of the periodic capture SQ (get_time-N), or None."""
    for sq in graph.sqs:
        if (sq.sq_name.startswith(CAPTURE_SQ_PREFIX)
                and getattr(sq, "firing_rule_type", None) == TTFiringRuleType.TimedRetrigger):
            return sq.sq_name
    return None


def read_photo_interval(cfg_path):
    """photo_interval in seconds from the config file, clamped; None if absent or unreadable."""
    try:
        with open(cfg_path) as f:
            value = json.load(f).get("photo_interval")
    except (OSError, ValueError):
        return None
    if value is None:
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value < MIN_PERIOD_S or value > MAX_PERIOD_S:
        clamped = min(max(value, MIN_PERIOD_S), MAX_PERIOD_S)
        print(f"⚠️ photo_interval {value}s outside {MIN_PERIOD_S}-{MAX_PERIOD_S}s; using {clamped}s", flush=True)
        value = clamped
    return value


class PhotoIntervalWatcher:
    def __init__(self, rtm, graph, cfg_path, poll_s=5.0):
        self.rtm = rtm
        self.graph_name = graph.graph_name
        self.sq_name = find_capture_sq(graph)
        self.cfg_path = cfg_path
        self.poll_s = poll_s
        self.applied_s = COMPILED_PERIOD_S
        self._mtime = None

    def check_once(self):
        """Apply photo_interval if the file changed and the value differs. Returns the applied period or None."""
        try:
            mtime = os.stat(self.cfg_path).st_mtime_ns
        except OSError:
            return None
        if mtime == self._mtime:
            return None
        self._mtime = mtime
        value = read_photo_interval(self.cfg_path)
        if value is None or value == self.applied_s:
            return None
        # Phase None keeps the current one: the capture loop starts on arrival
        # (TTStartOnArrival), not on a period boundary.
        self.rtm.update_periodicity(self.graph_name, self.sq_name,
                                    value * ROOT_TICKS_PER_S, None)
        print(f"📸 Capture period {self.applied_s}s → {value}s (photo_interval)", flush=True)
        self.applied_s = value
        return value

    def run(self):
        while True:
            # Sleep first: the graph has to be instantiated on its ensemble
            # before an update can reach the SQ.
            time.sleep(self.poll_s)
            try:
                self.check_once()
            except Exception as e:  # never take the runtime down
                print(f"⚠️ photo_interval watcher: {e}", flush=True)


def start_photo_interval_watcher(rtm, graph, cfg_path, poll_s=5.0):
    """Start the watcher thread; returns it, or None if the graph has no capture SQ."""
    watcher = PhotoIntervalWatcher(rtm, graph, cfg_path, poll_s)
    if watcher.sq_name is None:
        print("⚠️ photo_interval watcher: no periodic get_time SQ in this graph", flush=True)
        return None
    threading.Thread(target=watcher.run, name="photo-interval-watcher", daemon=True).start()
    return watcher
