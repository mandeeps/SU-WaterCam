"""Remote debug session (tools/remote_debug.py, docs/REMOTE_DEBUG_SESSION.md)."""
import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import remote_debug as rd  # noqa: E402


@pytest.fixture
def node(tmp_path, monkeypatch):
    """remote_debug with its files in tmp_path and every system call recorded."""
    monkeypatch.setattr(rd, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rd, "STATE_FILE", str(tmp_path / "remote_debug.json"))
    monkeypatch.setattr(rd, "REQUEST_FILE", str(tmp_path / "remote_debug_request.json"))
    monkeypatch.setattr(rd, "LOCK_FILE", str(tmp_path / "remote_debug.lock"))
    sim = MagicMock()
    sim.cell_active, sim.cell_managed, sim.boot = False, True, "boot-A"

    def cellular_state(conn):
        return sim.cell_active, sim.cell_managed

    def cellular_up(conn):
        sim.up(conn)
        sim.cell_active = True
        return True

    def cellular_down(conn):
        sim.down(conn)
        sim.cell_active = False

    monkeypatch.setattr(rd, "cellular_state", cellular_state)
    monkeypatch.setattr(rd, "cellular_up", cellular_up)
    monkeypatch.setattr(rd, "cellular_down", cellular_down)
    monkeypatch.setattr(rd, "wait_for_tailscale", lambda *a: sim.tailscale())
    monkeypatch.setattr(rd, "suspend_shutdown_alarm", lambda: (sim.suspend(), True)[1])
    monkeypatch.setattr(rd, "rearm_schedule", lambda: sim.rearm())
    monkeypatch.setattr(rd, "_systemctl", lambda action, unit="ticktalk.service": sim.systemctl(action))
    monkeypatch.setattr(rd, "send_status", lambda state, handler=None: sim.status(dict(state or {})))
    monkeypatch.setattr(rd, "_boot_id", lambda: sim.boot)
    monkeypatch.setattr(rd, "_cellular_connection", lambda: "Quectel")
    monkeypatch.setattr(rd, "_run", lambda cmd, timeout=30: (sim.run(cmd), (0, ""))[1])
    sim.tailscale.return_value = True
    return sim


def _state(node_tmp=None):
    return rd.load_state()


def test_minutes_from_code():
    assert rd.minutes_from_code("00") == 0
    assert rd.minutes_from_code("06") == 60
    assert rd.minutes_from_code("0c") == 120          # hex, not decimal
    assert rd.minutes_from_code("18") == 240
    assert rd.minutes_from_code("ff") == 240          # capped at 4 h
    assert rd.minutes_from_code("1") == 10


def test_start_holds_the_node_up(node):
    st = rd.start(30, source="lora")
    node.suspend.assert_called_once()
    node.systemctl.assert_called_once_with("stop")
    node.up.assert_called_once_with("Quectel")
    assert st["cellular"] and st["tailscale"] and st["active"]
    assert 29 * 60 < st["until"] - time.time() <= 30 * 60
    assert _state()["cellular_was_up"] is False
    assert node.status.call_args.args[0]["active"]


def test_start_leaves_an_up_connection_alone(node):
    node.cell_active = True
    rd.start(30)
    node.up.assert_not_called()


def test_extend_keeps_original_cellular_record(node):
    rd.start(30)
    first = _state()
    rd.start(120)
    st = _state()
    assert st["started"] == first["started"] and st["cellular_was_up"] is False
    assert st["until"] - time.time() > 110 * 60


def test_stop_restores_normal_operation(node):
    rd.start(30)
    assert rd.stop()
    node.down.assert_called_once_with("Quectel")
    node.rearm.assert_called_once()
    assert node.systemctl.call_args_list[-1].args == ("start",)
    assert _state() is None
    assert node.status.call_args.args[0] == {"active": False}


def test_stop_keeps_cellular_that_was_already_up(node):
    node.cell_active = True
    rd.start(30)
    rd.stop()
    node.down.assert_not_called()


def test_stop_keeps_cellular_when_it_autoconnects(node):
    node.cell_managed = False
    rd.start(30)
    rd.stop()
    node.down.assert_not_called()


def test_stop_with_shutdown_powers_off_instead(node):
    rd.start(30)
    rd.stop(shutdown=True)
    assert node.systemctl.call_args_list[-1].args == ("stop",)    # ticktalk not restarted
    assert node.run.call_args.args[0][-3:] == ["/usr/sbin/shutdown", "-h", "now"]


def test_stop_without_session_is_harmless(node):
    assert rd.stop() is False
    node.rearm.assert_not_called()


def test_tick_expires_a_session(node):
    rd.start(30)
    st = _state()
    st["until"] = time.time() - 1
    rd._write_json(rd.STATE_FILE, st)
    rd.tick()
    node.rearm.assert_called_once()
    assert _state() is None


def test_tick_leaves_a_running_session(node):
    rd.start(30)
    rd.tick()
    node.rearm.assert_not_called()
    assert node.systemctl.call_count == 1


def test_tick_resumes_after_a_reboot_without_moving_the_end(node):
    rd.start(30)
    until = _state()["until"]
    node.boot = "boot-B"
    rd.tick()
    assert node.systemctl.call_args_list[-1].args == ("stop",)
    assert node.suspend.call_count == 2
    st = _state()
    assert st["until"] == until and st["boot_id"] == "boot-B"


def test_tick_applies_requests(node):
    rd.request(60, "ip")
    rd.tick()
    assert _state()["source"] == "ip"
    assert not os.path.exists(rd.REQUEST_FILE)
    rd.request(0, "ip")
    rd.tick()
    assert _state() is None


def test_restore_never_rewrites_the_schedule():
    src = open(rd.__file__).read()
    assert "set_schedule" not in src and "generate_schedule" not in src
    assert "apply_emergency_schedule" not in src.replace("`apply_emergency_schedule(False)`", "")


# --- decoders route 18 98 -------------------------------------------------------------------

def test_ip_downlink_queues_a_request(node):
    from tools.transmit_ip import apply_downlink_command
    r = apply_downlink_command({"parts": [{"code": "18 98", "payload_hex": "0c"}]}, MagicMock())
    assert r["applied"] == ["remote_debug=120"]
    assert json.load(open(rd.REQUEST_FILE))["minutes"] == 120


@pytest.mark.parametrize("payload, value", [("189806", "06"), ("18980c", "0c"), ("189800", "0")])
def test_lora_decode_routes_remote_debug(payload, value):
    from tools.lora_handler_concurrent import LoRaHandler
    h = LoRaHandler.__new__(LoRaHandler)
    h.update_config = MagicMock()
    h._start_remote_debug = MagicMock()
    LoRaHandler.decode(h, payload)
    h._start_remote_debug.assert_called_once()
    assert rd.minutes_from_code(h._start_remote_debug.call_args.args[0]) == rd.minutes_from_code(value)
