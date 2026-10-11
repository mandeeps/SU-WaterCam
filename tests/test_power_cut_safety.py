"""A power cut must not cost a unit its config, its shutdown, or its photos.

- runtime_config.json / lora_config.json are replaced atomically and recovered from
  their .bak (tools/config_io.py). An in-place rewrite cut short used to make the
  unit run on defaults and then save those defaults over its identity and upload
  settings, and the failing shutdown counter kept it awake every window.
- Emergency mode ends after emergency_max_hours, since it disables both shutdown paths.
- take_two_photos no longer imports add_metadata (libxmp) before taking photos.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import config_io  # noqa: E402
import tools.lora_runtime_integration as lri  # noqa: E402
from tools.lora_runtime_integration import LoRaRuntimeManager  # noqa: E402

IDENTITY = {"ip_upload": {"device_id": "watercam-042", "enabled": True}, "iteration_count": 0,
            "shutdown_iteration_limit": 2, "auto_shutdown_enabled": True, "emergency_mode": False}


def _manager(path):
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        m = LoRaRuntimeManager(config_file=str(path))
    m.lora_handler = None
    return m


# --- config_io ------------------------------------------------------------------------

def test_write_keeps_previous_good_file_as_backup(tmp_path):
    p = tmp_path / "c.json"
    config_io.write_json(str(p), {"a": 1})
    assert not (tmp_path / "c.json.bak").exists()
    config_io.write_json(str(p), {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}
    assert json.loads((tmp_path / "c.json.bak").read_text()) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir() if ".tmp" in f.name] == []


@pytest.mark.parametrize("damage", [b"", b'{"a": 2, "ip_up', b"\x00" * 64])
def test_damaged_file_restored_from_backup(tmp_path, damage):
    p = tmp_path / "c.json"
    config_io.write_json(str(p), {"a": 1})
    config_io.write_json(str(p), {"a": 2})
    p.write_bytes(damage)                       # what a cut in-place write leaves
    assert config_io.read_json(str(p)) == {"a": 1}
    assert json.loads(p.read_text()) == {"a": 1}
    assert any(f.name.startswith("c.json.corrupt-") for f in tmp_path.iterdir())


def test_damaged_file_never_replaces_good_backup(tmp_path):
    p = tmp_path / "c.json"
    config_io.write_json(str(p), {"a": 1})
    config_io.write_json(str(p), {"a": 2})
    p.write_text("{broken")
    config_io.write_json(str(p), {"a": 3})
    assert json.loads((tmp_path / "c.json.bak").read_text()) == {"a": 1}


def test_unreadable_with_no_backup_raises(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{broken")
    with pytest.raises(config_io.ConfigUnreadable):
        config_io.read_json(str(p))


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        config_io.read_json(str(tmp_path / "nope.json"))


def test_failed_write_leaves_original(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    config_io.write_json(str(p), {"a": 1})
    monkeypatch.setattr(config_io.os, "fsync", MagicMock(side_effect=OSError("cut")))
    with pytest.raises(OSError):
        config_io.write_json(str(p), {"a": 2})
    assert json.loads(p.read_text()) == {"a": 1}
    assert [f.name for f in tmp_path.iterdir() if ".tmp" in f.name] == []


# --- runtime manager ------------------------------------------------------------------

def test_damaged_runtime_config_recovers_identity(tmp_path):
    p = tmp_path / "runtime_config.json"
    config_io.write_json(str(p), IDENTITY)
    m = _manager(p)
    m.atomic_increment_iteration_count()        # leaves IDENTITY as the .bak
    p.write_text('{"ip_upload": {"dev')         # cut mid-write
    m2 = _manager(p)
    assert m2.get_parameter("ip_upload")["device_id"] == "watercam-042"
    assert m2.atomic_increment_iteration_count()["iteration_count"] == 1


def test_unrecoverable_config_is_not_overwritten_at_load(tmp_path):
    p = tmp_path / "runtime_config.json"
    p.write_text("{broken")
    m = _manager(p)
    assert m.get_parameter("ip_upload") is None
    assert p.read_text() == "{broken"           # left for a person to recover


def test_unrecoverable_config_still_counts_iterations(tmp_path):
    p = tmp_path / "runtime_config.json"
    p.write_text("{broken")
    m = _manager(p)
    assert m.atomic_increment_iteration_count()["iteration_count"] == 1
    assert m.atomic_increment_iteration_count()["iteration_count"] == 2
    assert any(f.name.startswith("runtime_config.json.corrupt-") for f in tmp_path.iterdir())


def test_set_parameter_writes_atomically_and_merges(tmp_path):
    p = tmp_path / "runtime_config.json"
    config_io.write_json(str(p), IDENTITY)
    m = _manager(p)
    assert m.set_parameter("photo_interval", 120)
    cfg = json.loads(p.read_text())
    assert cfg["photo_interval"] == 120 and cfg["ip_upload"]["device_id"] == "watercam-042"


def test_emergency_since_tracks_emergency_mode(tmp_path):
    p = tmp_path / "runtime_config.json"
    config_io.write_json(str(p), IDENTITY)
    m = _manager(p)
    m.set_parameter("emergency_mode", True)
    since = json.loads(p.read_text())["emergency_since"]
    assert isinstance(since, float)
    m.set_parameter("emergency_mode", True)     # already on: clock not restarted
    assert json.loads(p.read_text())["emergency_since"] == since
    m.set_parameter("emergency_mode", False)
    assert json.loads(p.read_text())["emergency_since"] is None


def test_emergency_without_timestamp_gets_one(tmp_path):
    p = tmp_path / "runtime_config.json"
    config_io.write_json(str(p), dict(IDENTITY, emergency_mode=True))
    r = _manager(p).atomic_increment_iteration_count()
    assert r["emergency_mode"] and isinstance(r["emergency_since"], float)
    assert r["emergency_max_hours"] == 24


# --- call_shutdown: emergency expiry ---------------------------------------------------

@pytest.fixture
def shutdown_env(tmp_path, monkeypatch):
    import ticktalk_main
    p = tmp_path / "runtime_config.json"
    lri._runtime_manager = None

    def run(cfg):
        config_io.write_json(str(p), cfg)
        lri._runtime_manager = _manager(p)
        schedule = MagicMock(return_value={})
        calls = MagicMock()
        monkeypatch.setattr("tools.wittypi_control.apply_emergency_schedule", schedule)
        monkeypatch.setattr("subprocess.call", calls)
        with patch("sys.exit") as exit_:
            result = ticktalk_main.call_shutdown.__wrapped__(None)
        return result, schedule, calls, exit_, json.loads(p.read_text())
    yield run
    lri._runtime_manager = None


def test_recent_emergency_keeps_unit_awake(shutdown_env):
    import time
    result, schedule, calls, exit_, cfg = shutdown_env(
        dict(IDENTITY, iteration_count=5, emergency_mode=True, emergency_since=time.time() - 3600))
    assert result == "emergency_mode_active"
    exit_.assert_not_called()
    schedule.assert_not_called()


def test_expired_emergency_ends_and_shuts_down(shutdown_env):
    import time
    result, schedule, calls, exit_, cfg = shutdown_env(
        dict(IDENTITY, iteration_count=5, emergency_mode=True, emergency_since=time.time() - 25 * 3600))
    assert cfg["emergency_mode"] is False
    schedule.assert_called_once_with(False)
    exit_.assert_called_once_with("shutdown")


def test_emergency_max_hours_zero_never_expires(shutdown_env):
    result, schedule, calls, exit_, cfg = shutdown_env(
        dict(IDENTITY, iteration_count=5, emergency_mode=True, emergency_since=1.0, emergency_max_hours=0))
    assert result == "emergency_mode_active" and cfg["emergency_mode"] is True


# --- photos -----------------------------------------------------------------------------

def test_take_two_photos_does_not_import_metadata_before_capture():
    src = open(os.path.join(os.path.dirname(__file__), "..", "tt_take_photos.py")).read()
    body = src[src.index("def take_two_photos"):]
    assert body.index("from tools.add_metadata import") > body.index("picam2.capture_file(image_on)")
