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
    sim.wifi.return_value = False          # no known network: cellular, as before
    monkeypatch.setattr(rd, "wifi_up", lambda *a: sim.wifi())
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


def test_stop_leaves_a_bench_unit_up(node, monkeypatch):
    monkeypatch.setattr(rd, "ticktalk_enabled", lambda: False)    # ticktalk.service disabled
    rd.start(30)
    assert rd.stop()
    assert node.systemctl.call_args_list[-1].args == ("stop",)    # not started again
    node.rearm.assert_called_once()
    assert _state() is None


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


@pytest.mark.parametrize("payload, value", [("09189806", "06"), ("0918980c", "0c"), ("09189800", "0")])
def test_lora_decode_routes_remote_debug(payload, value):
    from tools.lora_handler_concurrent import LoRaHandler
    h = LoRaHandler.__new__(LoRaHandler)
    h.update_config = MagicMock()
    h._start_remote_debug = MagicMock()
    h._reply_with_status = MagicMock()
    LoRaHandler.decode(h, payload)
    h._start_remote_debug.assert_called_once()
    assert rd.minutes_from_code(h._start_remote_debug.call_args.args[0]) == rd.minutes_from_code(value)


def test_start_answers_before_tailscale_and_resends_are_cheap(node):
    rd.start(30)
    first, final = [c.args[0] for c in node.status.call_args_list]
    assert first["active"] and not first["tailscale"]      # immediate: stop resending
    assert final["tailscale"]
    node.status.reset_mock()
    rd.start(30)                                            # an API resend
    assert node.status.call_count == 1
    assert node.systemctl.call_count == 1 and node.up.call_count == 1   # not re-established



def test_wifi_first_skips_cellular(node):
    node.wifi.return_value = True
    rd.start(60, connection="auto")
    st = rd.load_state()
    assert st["wifi"] is True and st["tailscale"] is True
    node.up.assert_not_called()


def test_wifi_only_never_uses_cellular(node):
    rd.start(60, connection="wifi")
    st = rd.load_state()
    assert st["mode"] == "wifi" and st["wifi"] is False and st["tailscale"] is False
    node.up.assert_not_called()


def test_wifi_only_request_reaches_start(node):
    rd.request(30, "ip", connection="wifi")
    rd.tick()
    assert rd.load_state()["mode"] == "wifi"


def test_status_reports_wifi(node):
    node.wifi.return_value = True
    rd.start(60, connection="auto")
    assert node.status.call_args[0][0].get("wifi") is True



def test_cellular_session_never_tries_wifi(node):
    node.wifi.return_value = True
    rd.start(60)                                   # 18 98: cellular directly
    st = rd.load_state()
    assert st["mode"] == "cellular" and st["wifi"] is False
    node.wifi.assert_not_called()
    node.up.assert_called_once()


def test_codes_map_to_connections():
    assert rd.CODE_CONNECTION == {"18 98": "cellular", "18 99": "wifi", "18 9A": "auto"}


def test_a_new_code_changes_an_offline_session(node):
    rd.start(60, connection="wifi")                # 18 99, no network in range
    node.tailscale.return_value = False
    rd.start(60, connection="auto")                # then 18 9A
    st = rd.load_state()
    assert st["mode"] == "auto"
    node.up.assert_called_once()                   # fell back to cellular


def test_a_new_code_leaves_a_connected_session(node):
    node.wifi.return_value = True
    rd.start(60, connection="wifi")
    rd.start(60, connection="cellular")            # already up over WiFi: just extends
    assert rd.load_state()["mode"] == "wifi"
    node.up.assert_not_called()


@pytest.fixture
def wifi(monkeypatch):
    """wifi_up's NetworkManager calls, with wlan0 joining on the `joins_on`-th connect."""
    sim = MagicMock(connects=0, joins_on=1, radio=[], unavailable=0)
    monkeypatch.setattr("tools.wifi_policy.radio", lambda on: sim.radio.append(on) or True)
    monkeypatch.setattr(rd.time, "sleep", lambda s: None)

    def run(cmd, timeout=30):
        if cmd[-2:] == ["connect", "wlan0"]:
            sim.connects += 1
            return (0, "") if sim.connects >= sim.joins_on else (4, "")
        if "DEVICE,STATE" in cmd:
            if sim.unavailable:
                sim.unavailable -= 1
                return 0, "wlan0:unavailable"
            return 0, "wlan0:connected" if sim.connects >= sim.joins_on else "wlan0:disconnected"
        return 0, ""
    monkeypatch.setattr(rd, "_run", run)
    return sim


def test_wifi_up_asks_networkmanager_to_connect(wifi):
    assert rd.wifi_up(deadline_s=30)
    assert wifi.connects == 1 and wifi.radio == [True]


def test_wifi_up_waits_for_wlan0_before_connecting(wifi):
    wifi.unavailable = 2                           # just after radio-on
    assert rd.wifi_up(deadline_s=30)
    assert wifi.connects == 1 and wifi.radio == [True]   # no wasted radio cycle


def test_wifi_up_retries_a_first_failure_without_cycling(wifi):
    wifi.joins_on = 2                              # scan not done on the first try
    assert rd.wifi_up(deadline_s=30)
    assert wifi.radio == [True]


def test_wifi_up_cycles_the_radio_once_after_repeated_failures(wifi):
    wifi.joins_on = 5
    assert rd.wifi_up(deadline_s=60)
    assert wifi.radio == [True, False, True]       # one cycle, not one per failure


def test_wifi_up_gives_up_at_the_deadline(wifi, monkeypatch):
    wifi.joins_on = 99
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(rd.time, "monotonic", lambda: next(clock))
    assert not rd.wifi_up(deadline_s=60)
