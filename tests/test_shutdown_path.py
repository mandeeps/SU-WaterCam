"""Nothing between capture and call_shutdown may wait forever, and a failed
shutdown must not turn into a restart loop that starts the cycle count over."""
import json
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import config_io  # noqa: E402
from tools.lock_utils import flock_deadline  # noqa: E402


# --- lock with a deadline -----------------------------------------------------------------

def test_flock_deadline_times_out_on_a_held_lock(tmp_path):
    lock = str(tmp_path / "x.lock")
    held, release = threading.Event(), threading.Event()

    def holder():
        with flock_deadline(lock, 5):
            held.set()
            release.wait(5)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    try:
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            with flock_deadline(lock, 0.3, poll_s=0.05):
                pass
        assert time.monotonic() - start < 2
    finally:
        release.set()
        t.join()
    with flock_deadline(lock, 1):               # free again once released
        pass


# --- segformer ----------------------------------------------------------------------------

@pytest.fixture
def tm():
    import ticktalk_main
    return ticktalk_main


def test_segformer_skips_when_coreg_failed(tm, tmp_path):
    with patch("tools.segformer_client.segformer_via_daemon") as daemon, \
         patch("subprocess.run") as run:
        assert tm.segformer.__wrapped__(str(tmp_path), False) is None
    daemon.assert_not_called()
    run.assert_not_called()


def test_segformer_fallback_has_a_timeout(tm, tmp_path, monkeypatch):
    from tools.coreg_multiple import config as coreg_config
    (tmp_path / coreg_config.MODEL_INPUT_TIFF).write_bytes(b"tiff")
    monkeypatch.setattr("os.path.exists",
                        lambda p, _real=os.path.exists: False if p.endswith(".sock") else _real(p))
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("seg", 240)) as run:
        assert tm.segformer.__wrapped__(str(tmp_path), True) is None
    assert run.call_args.kwargs["timeout"] > 0


# --- call_shutdown ------------------------------------------------------------------------

@pytest.fixture
def shutdown(tmp_path, tm):
    import tools.lora_runtime_integration as lri
    from tools.lora_runtime_integration import LoRaRuntimeManager
    p = tmp_path / "runtime_config.json"
    config_io.write_json(str(p), {"iteration_count": 5, "shutdown_iteration_limit": 2,
                                  "auto_shutdown_enabled": True, "emergency_mode": False})
    with patch.object(LoRaRuntimeManager, "_init_lora_handler", lambda self: None):
        lri._runtime_manager = LoRaRuntimeManager(config_file=str(p))

    def run(returncodes):
        calls = MagicMock(side_effect=returncodes)
        with patch("subprocess.call", calls), patch("sys.exit") as exit_:
            tm.call_shutdown.__wrapped__(None)
        return [c.args[0][0] for c in calls.call_args_list], exit_
    yield run
    lri._runtime_manager = None


def test_shutdown_uses_doas_when_it_works(shutdown):
    cmds, exit_ = shutdown([0])
    assert cmds == ["doas"]
    exit_.assert_called_once_with("shutdown")


def test_shutdown_falls_back_to_sudo(shutdown):
    cmds, _ = shutdown([1, 0])
    assert cmds == ["doas", "sudo"]


def test_shutdown_survives_missing_doas(shutdown):
    cmds, exit_ = shutdown([FileNotFoundError("doas"), 1])
    assert cmds == ["doas", "sudo"]
    exit_.assert_called_once_with("shutdown")


# --- iteration count resets once per boot ---------------------------------------------------

def test_iteration_count_reset_once_per_boot(tmp_path):
    p = str(tmp_path / "runtime_config.json")
    config_io.write_json(p, {"iteration_count": 4, "ip_upload": {"device_id": "x"}})
    assert config_io.reset_iteration_count_once_per_boot(p, "boot-A")
    assert json.load(open(p))["iteration_count"] == 0
    cfg = json.load(open(p))
    cfg["iteration_count"] = 2                   # cycles ran, then the runtime restarted
    config_io.write_json(p, cfg)
    assert not config_io.reset_iteration_count_once_per_boot(p, "boot-A")
    assert json.load(open(p))["iteration_count"] == 2
    assert config_io.reset_iteration_count_once_per_boot(p, "boot-B")
    assert json.load(open(p)) == {"iteration_count": 0, "ip_upload": {"device_id": "x"},
                                  "iteration_boot_id": "boot-B"}


def test_iteration_count_without_boot_id_resets_every_run(tmp_path):
    p = str(tmp_path / "runtime_config.json")
    config_io.write_json(p, {"iteration_count": 3})
    with patch.object(config_io, "current_boot_id", return_value=None):
        assert config_io.reset_iteration_count_once_per_boot(p)
        config_io.write_json(p, {"iteration_count": 3, "iteration_boot_id": None})
        assert config_io.reset_iteration_count_once_per_boot(p)
    assert json.load(open(p))["iteration_count"] == 0
