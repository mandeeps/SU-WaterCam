"""tools/photo_interval_watcher.py: photo_interval (seconds) drives get_time's period."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.photo_interval_watcher as piw
from ticktalkpython.FiringRule import TTFiringRuleType


def _graph():
    sqs = [SimpleNamespace(sq_name="initialize_lora_integration-1", firing_rule_type=TTFiringRuleType.Immediate),
           SimpleNamespace(sq_name="get_time-4", firing_rule_type=TTFiringRuleType.TimedRetrigger)]
    return SimpleNamespace(graph_name="ttmain", sqs=sqs)


class _Rtm:
    def __init__(self):
        self.calls = []

    def update_periodicity(self, *args):
        self.calls.append(args)


def _write(path, **cfg):
    path.write_text(json.dumps(cfg))
    # make sure the mtime moves even on coarse-grained filesystems
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_finds_the_capture_sq_by_type_not_number():
    assert piw.find_capture_sq(_graph()) == "get_time-4"


def test_compiled_graph_has_the_capture_sq():
    import pickle
    with open(os.path.join(os.path.dirname(__file__), "..", "output", "ticktalk_main.pickle"), "rb") as f:
        graph = pickle.load(f)
    assert graph.graph_name == "ttmain"
    assert piw.find_capture_sq(graph) is not None


@pytest.mark.parametrize("value, expected", [(120, 120), (5, piw.MIN_PERIOD_S), (2, piw.MIN_PERIOD_S),
                                             (99999, piw.MAX_PERIOD_S), ("90", 90)])
def test_reads_and_clamps_seconds(tmp_path, value, expected):
    cfg = tmp_path / "runtime_config.json"
    _write(cfg, photo_interval=value)
    assert piw.read_photo_interval(str(cfg)) == expected


@pytest.mark.parametrize("content", ["{not json", json.dumps({}), json.dumps({"photo_interval": "x"})])
def test_unreadable_or_missing_value_is_none(tmp_path, content):
    cfg = tmp_path / "runtime_config.json"
    cfg.write_text(content)
    assert piw.read_photo_interval(str(cfg)) is None


def test_applies_a_change_once_in_root_ticks(tmp_path):
    cfg = tmp_path / "runtime_config.json"
    _write(cfg, photo_interval=60)
    rtm = _Rtm()
    w = piw.PhotoIntervalWatcher(rtm, _graph(), str(cfg))
    assert w.check_once() is None            # 60 s is what the graph was compiled with
    _write(cfg, photo_interval=120)
    assert w.check_once() == 120
    assert rtm.calls == [("ttmain", "get_time-4", 120_000_000, 0)]
    assert w.check_once() is None            # unchanged file: no second update
    _write(cfg, photo_interval=120, area_threshold=40)
    assert w.check_once() is None            # file changed, period didn't
    assert len(rtm.calls) == 1


def test_missing_file_is_ignored(tmp_path):
    w = piw.PhotoIntervalWatcher(_Rtm(), _graph(), str(tmp_path / "absent.json"))
    assert w.check_once() is None


def test_no_thread_without_a_capture_sq(tmp_path):
    graph = SimpleNamespace(graph_name="g", sqs=[])
    assert piw.start_photo_interval_watcher(_Rtm(), graph, str(tmp_path / "c.json")) is None
