"""The skip message must name the real reason LoRa is unavailable.

get_lora_handler() returns None only when the LoRa daemon's socket is missing or
is not a socket. The wake cycle used to report that as "serial port busy", which
sent unit 006 on 2026-09-30 looking for a port conflict when lora_daemon.service
had simply never been installed.
"""
import socket

from tools.lora_handler_concurrent import get_lora_handler, lora_unavailable_reason


def test_missing_socket_says_daemon_not_running(tmp_path):
    path = str(tmp_path / "lora.sock")
    assert get_lora_handler(path) is None
    reason = lora_unavailable_reason(path)
    assert "daemon not running" in reason and "lora_daemon.service" in reason
    assert "serial port" not in reason


def test_regular_file_is_reported_as_not_a_socket(tmp_path):
    path = tmp_path / "lora.sock"
    path.write_text("")
    assert get_lora_handler(str(path)) is None
    assert "not a socket" in lora_unavailable_reason(str(path))


def test_live_socket_yields_a_handler(tmp_path):
    path = str(tmp_path / "lora.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    try:
        assert get_lora_handler(path) is not None
    finally:
        srv.close()
