"""Transport order LoRa -> WiFi -> cellular for the IP uplink.

ip_upload.only_if_lora_unavailable (default true) skips the IP uplink while the
mDot is joined; ip_upload.cellular_connection (default "Quectel") is the
NetworkManager connection brought up when the server can't be reached over
WiFi. It stays up for the session but gives way as soon as LoRa or WiFi is
back. Readings delivered over IP are removed from the LoRa S&F queue (matched by capture time).
"""

import ast
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import lora_store_forward as lsf
from tools import transmit_ip
from tools.transmit_ip import IPTransmitter

HERE = os.path.dirname(__file__)
ON_DEMAND_IDLE = {"exists": True, "active": False, "autoconnect": False}
ON_DEMAND_UP = {"exists": True, "active": True, "autoconnect": False}


def _hardware(stack):
    stack.enter_context(patch("tools.aht20_temperature.get_aht20",
                              return_value={"temperature_celsius": 20.0, "relative_humidity": 50.0}))
    stack.enter_context(patch("tools.get_gps.get_location_with_retry", return_value=(None, None)))
    stack.enter_context(patch("tools.battery_manager.get_battery_status",
                              return_value={"battery_pct": 80, "battery_source": "test"}))
    stack.enter_context(patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d: d))
    stack.enter_context(patch("tools.pi_power.get_throttled", return_value=0))


def _tx(reachable, only_if_lora=True, cellular="Quectel"):
    tx = MagicMock()
    tx.enabled = True
    tx.only_if_lora_unavailable = only_if_lora
    tx.cellular_connection = cellular
    tx.cellular_connect_timeout_s = 90
    tx.is_reachable.return_value = reachable
    tx.wait_reachable.return_value = True
    tx._drain_queue.return_value = {"drained": 0, "failed": False, "failed_file": None, "drained_ts": []}
    tx.send_uplink.return_value = {"success": True, "attempts": 1}
    tx.poll_downlink.return_value = {"success": True, "command": None}
    return tx


def _run(tx, joined=False, status=ON_DEMAND_IDLE, wifi=False, up=True):
    """Run ip_uplink_transmit with hardware and NetworkManager patched; return the mocks."""
    import ticktalk_main
    handler = MagicMock()
    handler.is_joined.return_value = joined
    m = {}
    with ExitStack() as stack:
        _hardware(stack)
        stack.enter_context(patch("tools.transmit_ip.IPTransmitter", return_value=tx))
        stack.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=handler))
        stack.enter_context(patch("tools.transmit_ip.cellular_status", return_value=dict(status)))
        stack.enter_context(patch("tools.transmit_ip.wifi_connected", return_value=wifi))
        m["up"] = stack.enter_context(patch("tools.transmit_ip.cellular_up", return_value=up))
        m["down"] = stack.enter_context(patch("tools.transmit_ip.cellular_down"))
        m["dedup"] = stack.enter_context(patch("tools.lora_store_forward.remove_delivered", return_value=0))
        m["result"] = ticktalk_main.ip_uplink_transmit.__wrapped__(bitmap=[], _sensor_tracker=None, dirname=None)
    return m


class TestLoRaFirst:
    def test_lora_joined_skips_ip(self):
        tx = _tx(reachable=True)
        m = _run(tx, joined=True)
        assert m["result"]["status"] == "skipped_lora_joined"
        tx.send_uplink.assert_not_called()
        m["up"].assert_not_called()

    def test_lora_back_takes_cellular_session_down(self):
        m = _run(_tx(reachable=True), joined=True, status=ON_DEMAND_UP)
        m["down"].assert_called_once_with("Quectel")

    def test_lora_joined_ignored_when_flag_off(self):
        tx = _tx(reachable=True, only_if_lora=False)
        m = _run(tx, joined=True)
        assert m["result"]["success"] is True
        tx.send_uplink.assert_called_once()


class TestWiFiThenCellular:
    def test_wifi_reachable_never_touches_cellular(self):
        m = _run(_tx(reachable=True), wifi=True)
        assert m["result"]["success"] is True
        m["up"].assert_not_called()
        m["down"].assert_not_called()

    def test_unreachable_brings_cellular_up_and_leaves_it_up(self):
        tx = _tx(reachable=False)
        m = _run(tx)
        assert m["result"]["success"] is True
        m["up"].assert_called_once_with("Quectel", 90)
        tx.send_uplink.assert_called_once()
        tx.poll_downlink.assert_called_once()
        m["down"].assert_not_called()

    def test_existing_session_is_used_while_wifi_is_down(self):
        tx = _tx(reachable=True)
        m = _run(tx, status=ON_DEMAND_UP, wifi=False)
        assert m["result"]["success"] is True
        m["up"].assert_not_called()
        m["down"].assert_not_called()
        tx.poll_downlink.assert_not_called()

    def test_wifi_back_takes_cellular_session_down(self):
        tx = _tx(reachable=True)
        m = _run(tx, status=ON_DEMAND_UP, wifi=True)
        m["down"].assert_called_once_with("Quectel")
        m["up"].assert_not_called()
        assert m["result"]["success"] is True

    def test_wifi_connected_but_no_route_falls_back_to_cellular_again(self):
        tx = _tx(reachable=False)
        m = _run(tx, status=ON_DEMAND_UP, wifi=True)
        m["down"].assert_called_once_with("Quectel")
        m["up"].assert_called_once()

    def test_autoconnect_connection_is_left_alone(self):
        tx = _tx(reachable=False)
        m = _run(tx, joined=True, status={"exists": True, "active": True, "autoconnect": True})
        m["down"].assert_not_called()
        m = _run(_tx(reachable=False), status={"exists": True, "active": False, "autoconnect": True})
        m["up"].assert_not_called()
        assert m["result"]["status"] == "queued"

    def test_no_such_connection_just_queues(self):
        m = _run(_tx(reachable=False), status={"exists": False, "active": False, "autoconnect": False})
        assert m["result"]["status"] == "queued"
        m["up"].assert_not_called()

    def test_cellular_fails_to_connect_queues(self):
        tx = _tx(reachable=False)
        m = _run(tx, up=False)
        assert m["result"]["status"] == "queued"
        tx._enqueue.assert_called_once()
        m["down"].assert_not_called()

    def test_cellular_up_but_server_unreachable_takes_it_down(self):
        tx = _tx(reachable=False)
        tx.wait_reachable.return_value = False
        m = _run(tx)
        assert m["result"]["status"] == "queued"
        m["down"].assert_called_once_with("Quectel")
        tx.poll_downlink.assert_not_called()

    def test_downlink_command_over_cellular_is_applied(self):
        tx = _tx(reachable=False)
        tx.poll_downlink.return_value = {"success": True, "command": {"queue_id": 7}}
        with patch("tools.transmit_ip.apply_downlink_command",
                   return_value={"applied": ["photo_interval"], "skipped": [], "queue_id": 7}) as apply:
            _run(tx)
        apply.assert_called_once()


class TestLoRaDuplicates:
    def test_delivered_readings_removed_from_lora_queue(self):
        tx = _tx(reachable=True)
        tx._drain_queue.return_value = {"drained": 2, "failed": False, "failed_file": None,
                                        "drained_ts": [100, 200]}
        m = _run(tx, wifi=True)
        delivered = m["dedup"].call_args[0][0]
        assert delivered[:2] == [100, 200] and len(delivered) == 3

    def test_failed_live_send_removes_only_drained(self):
        tx = _tx(reachable=True)
        tx._drain_queue.return_value = {"drained": 1, "failed": False, "failed_file": None,
                                        "drained_ts": [100]}
        tx.send_uplink.return_value = {"success": False, "attempts": 3, "error": "boom"}
        m = _run(tx, wifi=True)
        m["dedup"].assert_called_once_with([100])

    def test_nothing_delivered_nothing_removed(self):
        tx = _tx(reachable=True)
        tx.send_uplink.return_value = {"success": False, "attempts": 3, "error": "boom"}
        m = _run(tx, wifi=True)
        m["dedup"].assert_not_called()


class TestRemoveDelivered:
    def test_removes_exact_capture_times(self, tmp_path):
        q = str(tmp_path)
        for t in (1000, 1060, 1120):
            lsf.enqueue("aa", None, t, queue_dir=q)
        assert lsf.remove_delivered([1060, 1121], queue_dir=q) == 1
        left = sorted(int(f.split("_")[0]) for f in os.listdir(q))
        assert left == [1000, 1120]

    def test_fractional_capture_time_matches_its_second(self, tmp_path):
        q = str(tmp_path)
        lsf.enqueue("aa", None, 1000.7, queue_dir=q)
        assert lsf.remove_delivered([1000], queue_dir=q) == 1

    def test_missing_queue_dir(self, tmp_path):
        assert lsf.remove_delivered([1], queue_dir=str(tmp_path / "none")) == 0


class TestCaptureTime:
    def test_written_and_read_back(self, tmp_path):
        from tools.capture_time import capture_time, write_capture_time
        d = tmp_path / "20261007-101500"
        d.mkdir()
        assert write_capture_time(str(d), 1791400000.9) == 1791400000
        assert capture_time(str(d)) == 1791400000

    def test_falls_back_to_directory_name(self, tmp_path):
        from datetime import datetime
        from tools.capture_time import capture_time
        d = tmp_path / "20261007-101500"
        d.mkdir()
        assert capture_time(str(d)) == int(datetime(2026, 10, 7, 10, 15, 0).timestamp())

    def test_falls_back_to_now(self):
        import time
        from tools.capture_time import capture_time
        assert abs(capture_time(None) - time.time()) < 2


class TestWaitForSensors:
    def test_ready_when_paths_exist(self, tmp_path):
        from tools.wait_for_sensors import wait_for_sensors
        (tmp_path / "dev").touch()
        r = wait_for_sensors(timeout_s=1, checks=[("x", str(tmp_path / "dev"), None)], unit_dir=str(tmp_path))
        assert r["ready"] is True and r["missing"] == []

    def test_times_out_and_reports_missing(self, tmp_path):
        from tools.wait_for_sensors import wait_for_sensors
        r = wait_for_sensors(timeout_s=0.3, poll_s=0.1,
                             checks=[("camera", str(tmp_path / "nope"), None)], unit_dir=str(tmp_path))
        assert r["ready"] is False and r["missing"] == ["camera"]

    def test_daemon_skipped_when_not_installed(self, tmp_path):
        from tools.wait_for_sensors import pending
        checks = [("lora daemon", str(tmp_path / "lora.sock"), "lora_daemon.service")]
        assert pending(checks, unit_dir=str(tmp_path)) == []
        (tmp_path / "lora_daemon.service").touch()
        assert pending(checks, unit_dir=str(tmp_path)) == ["lora daemon"]


class TestGraphOrder:
    def test_ip_after_lora_and_shutdown_after_ip(self):
        src = open(os.path.join(HERE, "..", "ticktalk_main.py")).read()
        calls = {}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and isinstance(node.value.func, ast.Name):
                calls[node.value.func.id] = [a.id for a in node.value.args if isinstance(a, ast.Name)]
        assert calls["ip_uplink_transmit"] == ["lora_return", "sensor_tracker", "dirname"]
        assert calls["lora_token_with_tracker"] == ["bitmap", "sensor_tracker", "dirname"]
        assert calls["call_shutdown"] == ["ip_return"]


class TestConfig:
    def _tx(self, tmp_path, ip_cfg):
        p = tmp_path / "runtime_config.json"
        p.write_text(json.dumps({"ip_upload": ip_cfg}))
        return IPTransmitter(config_path=str(p))

    def test_defaults_on(self, tmp_path):
        tx = self._tx(tmp_path, {})
        assert tx.only_if_lora_unavailable is True
        assert tx.cellular_connection == "Quectel"
        assert tx.cellular_connect_timeout_s == 90

    def test_turned_off(self, tmp_path):
        tx = self._tx(tmp_path, {"only_if_lora_unavailable": False, "cellular_connection": ""})
        assert tx.only_if_lora_unavailable is False
        assert tx.cellular_connection == ""

    def test_bad_types_fall_back(self, tmp_path):
        tx = self._tx(tmp_path, {"cellular_connection": 5, "cellular_connect_timeout_s": "x"})
        assert tx.cellular_connection == ""
        assert tx.cellular_connect_timeout_s == 90


def _proc(rc=0, out=""):
    return MagicMock(returncode=rc, stdout=out, stderr="")


class TestNmcli:
    def test_up_runs_nmcli_with_wait(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc()) as run:
            assert transmit_ip.cellular_up("Quectel", 60) is True
        assert run.call_args[0][0] == ["nmcli", "--wait", "60", "connection", "up", "Quectel"]

    def test_up_failure_returns_false(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(4)):
            assert transmit_ip.cellular_up("Quectel") is False

    def test_missing_nmcli_never_raises(self):
        with patch("tools.transmit_ip.subprocess.run", side_effect=FileNotFoundError("nmcli")):
            assert transmit_ip.cellular_up("Quectel") is False
            transmit_ip.cellular_down("Quectel")
            assert transmit_ip.cellular_status("Quectel")["exists"] is False
            assert transmit_ip.wifi_connected() is False

    def test_status_active_on_demand(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(0, "no\nactivated\n")):
            assert transmit_ip.cellular_status("Quectel") == {"exists": True, "active": True, "autoconnect": False}

    def test_status_inactive_autoconnect(self):
        # nmcli prints no GENERAL.STATE line for an inactive connection
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(0, "yes\n")):
            assert transmit_ip.cellular_status("Quectel") == {"exists": True, "active": False, "autoconnect": True}

    def test_status_missing_connection(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(10)):
            assert transmit_ip.cellular_status("Nope")["exists"] is False

    def test_wifi_connected(self):
        with patch("tools.transmit_ip.subprocess.run",
                   return_value=_proc(0, "wifi:connected\ngsm:disconnected\nloopback:connected (externally)\n")):
            assert transmit_ip.wifi_connected() is True
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(0, "wifi:disconnected\n")):
            assert transmit_ip.wifi_connected() is False
