"""Small reliability fixes: config lock deadline, fsynced state files, log
rotation, serial write timeout, legacy unit removed, video bind address."""
import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import config_io

REPO = Path(__file__).resolve().parent.parent


# --- config_io.locked() has a deadline -----------------------------------------

def test_locked_times_out_with_a_clear_error(tmp_path):
    path = str(tmp_path / "runtime_config.json")
    held, release = threading.Event(), threading.Event()

    def holder():
        with config_io.locked(path):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    start = time.monotonic()
    try:
        with pytest.raises(config_io.ConfigLockTimeout, match="runtime_config.json.lock"):
            with config_io.locked(path, timeout_s=0.3):
                pass
        assert time.monotonic() - start < 2
    finally:
        release.set()
        t.join()
    with config_io.locked(path, timeout_s=0.3):      # free again
        pass


def test_locked_does_not_swallow_errors_from_the_body(tmp_path):
    with pytest.raises(KeyError):
        with config_io.locked(str(tmp_path / "c.json")):
            raise KeyError("x")


# --- state files are fsynced before the rename -----------------------------------

def _fsyncs(fn):
    real = os.fsync
    calls = []

    def counting(fd):
        calls.append(fd)
        return real(fd)

    with patch("os.fsync", counting):
        fn()
    return len(calls)


def test_gps_cache_and_home_are_fsynced_and_private(tmp_path, monkeypatch):
    from tools import get_gps, gps_home
    monkeypatch.setattr(get_gps, "CACHE_PATH", str(tmp_path / "gps_last_fix.json"))
    assert _fsyncs(lambda: get_gps._save_fix({"gps_lat": 1.0, "gps_lon": 2.0})) >= 1
    assert os.stat(get_gps.CACHE_PATH).st_mode & 0o777 == 0o600
    assert json.load(open(get_gps.CACHE_PATH))["gps_lat"] == 1.0
    monkeypatch.setattr(gps_home, "HOME_PATH", str(tmp_path / "gps_home.json"))
    assert _fsyncs(lambda: gps_home._save({"home": [1.0, 2.0]})) >= 1
    assert os.stat(gps_home.HOME_PATH).st_mode & 0o777 == 0o600


def test_sensor_state_and_lora_health_are_fsynced(tmp_path):
    from tools import lora_health, sensor_changes
    p = str(tmp_path / "last_sent.json")
    assert _fsyncs(lambda: sensor_changes.mark_sent({"temperature_celsius": 20.0}, state_path=p)) >= 1
    assert json.load(open(p))["temperature_celsius"] == 20.0
    h = str(tmp_path / "lora_health.json")
    assert _fsyncs(lambda: lora_health._write(h, [{"ok": True}])) >= 1
    assert json.load(open(h)) == [{"ok": True}]


def test_store_forward_records_are_fsynced(tmp_path):
    from tools import lora_store_forward as lsf
    q = str(tmp_path / "lora_pending")
    assert _fsyncs(lambda: lsf.enqueue("0001", None, 1791400000.0, queue_dir=q)) >= 1
    assert [f for f in os.listdir(q) if not f.endswith(".json") and f != ".lock"] == []


# --- log rotation ------------------------------------------------------------------

def test_logrotate_config_covers_the_growing_logs_and_is_documented():
    text = (REPO / "config" / "logrotate-watercam.conf").read_text()
    for log in ("/home/pi/powerlog/*.csv", "/home/pi/throttle_log.csv", "/home/pi/SU-WaterCam/output.log"):
        assert log in text
    body = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("wittyPi.log" in line for line in body)     # its boot offset must stay valid
    assert "copytruncate" in text and "su pi pi" in text
    guide = (REPO / "docs" / "FIELD_DEPLOYMENT_GUIDE.md").read_text()
    assert "config/logrotate-watercam.conf /etc/logrotate.d/watercam" in guide


# --- LoRa serial write timeout, legacy unit removed ------------------------------------

def test_lora_serial_port_has_a_write_timeout(monkeypatch, _mock_serial_port):
    import tools.lora_handler_concurrent as lhc
    seen = {}
    monkeypatch.setattr(lhc.serial, "Serial", lambda *a, **kw: seen.update(kw) or _mock_serial_port)
    lhc.LoRaHandler()
    assert seen.get("timeout") == 1 and seen.get("write_timeout") == 1


def test_legacy_temperature_service_is_gone():
    assert not (REPO / "config" / "temperature.service").exists()


# --- video stream bind address ---------------------------------------------------------

def test_video_binds_tailscale_else_loopback():
    from tools import video
    with patch.object(video, "tailscale_ipv4", return_value="100.64.0.7"):
        assert video.bind_address() == "100.64.0.7"
        assert video.bind_address("0.0.0.0") == "0.0.0.0"
    with patch.object(video, "tailscale_ipv4", return_value=None):
        assert video.bind_address() == "127.0.0.1"


def test_tailscale_ipv4_handles_missing_cli():
    from tools import video
    with patch.object(video.subprocess, "run", side_effect=FileNotFoundError):
        assert video.tailscale_ipv4() is None
