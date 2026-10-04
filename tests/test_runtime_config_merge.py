"""runtime_config.json writes must not revert other processes' changes.

lora_daemon and each ticktalk SQ process keep their own LoRaRuntimeManager.
set_parameter() used to save the whole in-memory copy, and every wake
integrate_with_ticktalk() copied lora_config.json over the shared keys, so
IP-downlink and hand-made changes were silently reverted (seen on 005/006
2026-10-04: shutdown_iteration_limit 4 -> 2).
"""
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.lora_runtime_integration as lri
from tools.lora_runtime_integration import LoRaRuntimeManager


def _manager(path):
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        m = LoRaRuntimeManager(config_file=str(path))
    m.lora_handler = None
    return m


def _read(path):
    return json.loads(path.read_text())


def _edit(path, **changes):
    cfg = _read(path)
    cfg.update(changes)
    path.write_text(json.dumps(cfg))
    st = os.stat(path)  # move the mtime on even on coarse filesystems
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "runtime_config.json"
    p.write_text(json.dumps({"area_threshold": 10, "photo_interval": 60,
                             "shutdown_iteration_limit": 2}))
    return p


def test_a_write_keeps_another_processes_change(cfg):
    daemon, ticktalk = _manager(cfg), _manager(cfg)
    assert ticktalk.set_parameter("area_threshold", 40)
    assert daemon.set_parameter("photo_interval", 90)       # daemon's copy still says area 10
    assert _read(cfg)["area_threshold"] == 40
    assert _read(cfg)["photo_interval"] == 90


def test_a_write_keeps_a_hand_edit(cfg):
    m = _manager(cfg)
    _edit(cfg, shutdown_iteration_limit=4)
    assert m.set_parameter("area_threshold", 30)
    assert _read(cfg)["shutdown_iteration_limit"] == 4
    assert m.get_parameter("shutdown_iteration_limit") == 4


def test_callback_sees_the_value_another_process_wrote(cfg):
    a, b = _manager(cfg), _manager(cfg)
    seen = []
    a.register_update_callback("area_threshold", lambda new, old: seen.append((new, old)))
    assert b.set_parameter("area_threshold", 40)
    assert a.set_parameter("area_threshold", 50)
    assert seen[-1] == (50, 40)


def test_an_unreadable_file_is_rebuilt_from_memory(cfg):
    m = _manager(cfg)
    cfg.write_text("{not json")
    assert m.set_parameter("area_threshold", 20)
    assert _read(cfg)["area_threshold"] == 20
    assert _read(cfg)["photo_interval"] == 60


def test_failed_save_leaves_memory_unchanged(cfg):
    m = _manager(cfg)
    with patch.object(m, "save_parameters", return_value=False):
        assert not m.set_parameter("area_threshold", 50)
    assert m.get_parameter("area_threshold") == 10


def test_wake_setup_no_longer_copies_lora_config_over_runtime_config(cfg):
    m = _manager(cfg)
    _edit(cfg, shutdown_iteration_limit=4)
    m.lora_handler = SimpleNamespace(config={"shutdown_iteration_limit": 2, "photo_interval": 60})
    with patch.object(lri, "get_runtime_manager", return_value=m):
        lri.integrate_with_ticktalk()
    assert _read(cfg)["shutdown_iteration_limit"] == 4


def test_explicit_sync_merges_only_the_synced_keys(cfg):
    m = _manager(cfg)
    other = _manager(cfg)
    assert other.set_parameter("area_threshold", 70)        # m's copy is stale
    m.lora_handler = SimpleNamespace(config={"photo_interval": 120})
    assert m.sync_with_lora_config()
    assert _read(cfg)["photo_interval"] == 120
    assert _read(cfg)["area_threshold"] == 70
