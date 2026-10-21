"""ticktalk.service's watchdog (#208): one heartbeat per capture iteration."""
import os
import socket
from pathlib import Path

from tools import systemd_watchdog

ROOT = Path(__file__).resolve().parent.parent


def test_no_notify_socket_means_no_heartbeat(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert systemd_watchdog.heartbeat() is False


def test_heartbeat_reaches_the_notify_socket(tmp_path, monkeypatch):
    path = str(tmp_path / "notify")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
        srv.bind(path)
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        assert systemd_watchdog.heartbeat() is True
        assert srv.recv(64) == b"WATCHDOG=1"


def test_abstract_notify_socket(monkeypatch):
    name = f"watercam-test-{os.getpid()}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
        srv.bind("\0" + name)
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)
        assert systemd_watchdog.heartbeat() is True
        assert srv.recv(64) == b"WATCHDOG=1"


def test_a_missing_socket_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "gone"))
    assert systemd_watchdog.heartbeat() is False


def test_the_unit_has_the_watchdog_and_restarts():
    unit = (ROOT / "config" / "ticktalk.service").read_text()
    assert "\nWatchdogSec=15min\n" in unit and "\nNotifyAccess=all\n" in unit
    assert "\nRestart=on-failure\n" in unit


def test_call_shutdown_beats_once_per_iteration():
    src = (ROOT / "ticktalk_main.py").read_text()
    body = src[src.index("def call_shutdown("):]
    beat = body.index("heartbeat()")
    assert beat < body.index("atomic_increment_iteration_count()")
    assert body[:beat].count("\ndef ") == 0
