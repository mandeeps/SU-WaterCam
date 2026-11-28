"""Transport order LoRa -> WiFi -> cellular for the IP uplink.

ip_upload.only_if_lora_unavailable (default true) skips the IP uplink while the
mDot is joined; ip_upload.cellular_connection (default "Quectel") is the
NetworkManager connection brought up when the server can't be reached over
WiFi. It stays up for the session but gives way as soon as LoRa or WiFi is
back. Readings delivered over IP are removed from the LoRa S&F queue (matched by capture time).
"""

import ast
import itertools
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

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
    tx.wifi_grace_s = 45
    tx.is_reachable.return_value = reachable
    tx.wait_reachable.return_value = True
    tx.wait_tailnet.return_value = True
    tx.server_only_down.return_value = False
    tx._drain_queue.return_value = {"drained": 0, "failed": False, "failed_file": None, "drained_ts": []}
    tx.send_uplink.return_value = {"success": True, "attempts": 1}
    tx.poll_downlink.return_value = {"success": True, "commands": []}
    return tx


def _run(tx, joined=False, status=ON_DEMAND_IDLE, wifi=False, up=True, in_range=False, later_status=None):
    """Run ip_uplink_transmit with hardware and NetworkManager patched; return the mocks."""
    import ticktalk_main
    handler = MagicMock()
    handler.is_joined.return_value = joined
    m = {}
    with ExitStack() as stack:
        _hardware(stack)
        stack.enter_context(patch("tools.transmit_ip.IPTransmitter", return_value=tx))
        stack.enter_context(patch("tools.lora_handler_concurrent.get_lora_handler", return_value=handler))
        if later_status is None:
            stack.enter_context(patch("tools.transmit_ip.cellular_status", return_value=dict(status)))
        else:   # the first call sees `status`, every later one `later_status`
            seq = iter([dict(status)])
            m["status"] = stack.enter_context(patch("tools.transmit_ip.cellular_status",
                                                    side_effect=lambda c: next(seq, dict(later_status))))
        stack.enter_context(patch("tools.transmit_ip.wifi_connected", return_value=wifi))
        m["in_range"] = stack.enter_context(patch("tools.transmit_ip.wifi_in_range", return_value=in_range))
        m["health"] = stack.enter_context(patch("tools.lora_health.assess",
                                                return_value={"healthy": joined, "reason": "test"}))
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

    def test_unreachable_brings_cellular_up_and_takes_it_down_after(self):
        tx = _tx(reachable=False)
        m = _run(tx)
        assert m["result"]["success"] is True
        m["up"].assert_called_once_with("Quectel", 90)
        tx.send_uplink.assert_called_once()
        tx.poll_downlink.assert_called_once()
        m["down"].assert_called_once_with("Quectel")      # after the upload and the poll
        tx._drain_queue.assert_called_once_with(metered=True, skip=tx._enqueue_live.return_value)

    def test_wifi_up_and_server_peer_online_does_not_bring_cellular_up(self):
        tx = _tx(reachable=False)
        tx.server_only_down.return_value = True
        tx.wait_reachable.return_value = False
        m = _run(tx, wifi=True)
        m["up"].assert_not_called()
        tx.keep_queued.assert_called_once()
        tx.send_uplink.assert_not_called()
        assert m["result"]["status"] == "queued"

    def test_cellular_comes_down_when_the_post_fails_too(self):
        tx = _tx(reachable=False)
        tx.send_uplink.return_value = {"success": False, "attempts": 3, "error": "x"}
        m = _run(tx)
        m["down"].assert_called_once_with("Quectel")

    def test_cellular_comes_down_even_if_the_uplink_raises(self):
        tx = _tx(reachable=False)
        tx.send_uplink.side_effect = RuntimeError("boom")
        m = _run(tx)
        assert m["result"]["status"] == "error"
        m["down"].assert_called_once_with("Quectel")

    def test_existing_session_is_used_while_wifi_is_down(self):
        tx = _tx(reachable=True)
        m = _run(tx, status=ON_DEMAND_UP, wifi=False)
        assert m["result"]["success"] is True
        m["up"].assert_not_called()
        m["down"].assert_not_called()
        tx.poll_downlink.assert_called_once()

    def test_wifi_back_takes_cellular_session_down(self):
        tx = _tx(reachable=True)
        m = _run(tx, status=ON_DEMAND_UP, wifi=True)
        m["down"].assert_called_once_with("Quectel")
        m["up"].assert_not_called()
        assert m["result"]["success"] is True

    def test_wifi_connected_but_no_route_falls_back_to_cellular_again(self):
        # WiFi gets its grace period first; only when the server still isn't
        # reachable over it does cellular come back.
        tx = _tx(reachable=False)
        tx.wait_reachable.return_value = False
        m = _run(tx, status=ON_DEMAND_UP, wifi=True, later_status=ON_DEMAND_IDLE)   # down once WiFi is back
        assert m["down"].call_args_list == [(("Quectel",),)] * 2   # WiFi came back, then after the upload
        tx.wait_reachable.assert_called_once_with(45)
        tx.wait_tailnet.assert_called_once_with(30)
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
        tx.keep_queued.assert_called_once()
        m["down"].assert_not_called()

    def test_cellular_up_but_server_unreachable_takes_it_down(self):
        tx = _tx(reachable=False)
        tx.wait_tailnet.return_value = False
        m = _run(tx)
        assert m["result"]["status"] == "queued"
        m["down"].assert_called_once_with("Quectel")
        tx.poll_downlink.assert_not_called()

    def test_post_failure_over_cellular_takes_it_down(self):
        tx = _tx(reachable=False)
        tx.send_uplink.return_value = {"success": False, "attempts": 3, "error": "timeout"}
        m = _run(tx)
        assert m["result"]["status"] == "queued"
        m["down"].assert_called_once_with("Quectel")

    def test_cellular_path_never_probes_health(self):
        tx = _tx(reachable=False)
        _run(tx)
        tx.is_reachable.assert_called_once()   # the WiFi check, before cellular comes up
        tx.wait_reachable.assert_not_called()

    def test_downlink_command_over_cellular_is_applied(self):
        tx = _tx(reachable=False)
        tx.poll_downlink.return_value = {"success": True, "commands": [{"id": 7, "frame": "0a109032"}]}
        with patch("tools.transmit_ip.apply_downlink_command",
                   return_value={"applied": ["photo_interval"], "skipped": []}) as apply:
            _run(tx)
        apply.assert_called_once()
        tx.post_acks.assert_called_once_with([{"id": 7, "result": 0}])

    def test_downlink_polled_after_uplink_over_wifi(self):
        # The wake-start poll may have had no route even when the uplink gets
        # through; poll again whatever the transport.
        tx = _tx(reachable=True)
        _run(tx, wifi=True)
        tx.poll_downlink.assert_called_once()

    def test_no_downlink_poll_when_nothing_was_delivered(self):
        tx = _tx(reachable=True)
        tx.send_uplink.return_value = {"success": False, "attempts": 3, "error": "HTTP 500"}
        _run(tx, wifi=True)
        tx.poll_downlink.assert_not_called()

    def test_downlink_poll_failure_does_not_fail_the_uplink(self):
        tx = _tx(reachable=True)
        tx.poll_downlink.side_effect = RuntimeError("boom")
        m = _run(tx, wifi=True)
        assert m["result"]["success"] is True


class TestVersionChannels:
    def test_ip_uplink_carries_version_commit_and_model(self):
        tx = _tx(reachable=True)
        fields = {"software_version": 0x1A0A0100, "software_commit": 0xABC12345, "model_id": 0x4CA62324}
        with patch("tools.node_version.uplink_fields", return_value=fields):
            _run(tx, wifi=True)
        channels = {c["code"]: c["payload_hex"] for c in tx.send_uplink.call_args.args[0]}
        assert channels["01 09"] == "1a0a0100"
        assert channels["01 0A"] == "abc12345"
        assert channels["01 0B"] == "4ca62324"

    def test_missing_fields_are_left_out(self):
        tx = _tx(reachable=True)
        with patch("tools.node_version.uplink_fields", return_value={}):
            _run(tx, wifi=True)
        codes = {c["code"] for c in tx.send_uplink.call_args.args[0]}
        assert not codes & {"01 09", "01 0A", "01 0B"}


class TestCommandInUplinkReply:
    def test_live_uplink_asks_for_the_command(self):
        tx = _tx(reachable=True)
        _run(tx, wifi=True)
        assert tx.send_uplink.call_args.kwargs.get("want_command") is True

    def test_command_in_the_reply_is_applied_without_a_poll(self):
        tx = _tx(reachable=True)
        tx.send_uplink.return_value = {"success": True, "attempts": 1,
                                       "response": {"decoded": {},
                                                    "commands": [{"id": 9, "frame": "0b109032"}]}}
        with patch("tools.transmit_ip.apply_downlink_command",
                   return_value={"applied": ["photo_interval=60"], "skipped": []}) as apply:
            _run(tx, wifi=True)
        apply.assert_called_once()
        tx.post_acks.assert_called_once_with([{"id": 9, "result": 0}])
        tx.poll_downlink.assert_not_called()

    def test_empty_commands_in_the_reply_mean_nothing_pending(self):
        tx = _tx(reachable=True)
        tx.send_uplink.return_value = {"success": True, "attempts": 1,
                                       "response": {"decoded": {}, "commands": []}}
        _run(tx, wifi=True)
        tx.poll_downlink.assert_not_called()

    def test_reply_without_commands_falls_back_to_a_poll(self):
        tx = _tx(reachable=True)
        tx.send_uplink.return_value = {"success": True, "attempts": 1, "response": {"decoded": {}}}
        _run(tx, wifi=True)
        tx.poll_downlink.assert_called_once()


class TestReplyCommands:
    def test_reply_commands(self):
        from tools.transmit_ip import reply_commands
        c = [{"id": 1, "frame": "0110902a"}]
        assert reply_commands({"success": True, "response": {"commands": c}}) == {"success": True, "commands": c}
        assert reply_commands({"success": True, "response": {"commands": []}}) == \
            {"success": True, "commands": []}
        assert reply_commands({"success": True, "response": {"decoded": {}}}) is None
        assert reply_commands({"success": True, "response": None}) is None
        assert reply_commands({"success": False, "response": {"commands": c}}) is None

    def test_send_uplink_sends_the_flag_only_when_asked(self):
        from tools.transmit_ip import IPTransmitter
        tx = IPTransmitter.__new__(IPTransmitter)
        tx.device_id, tx.server_url = "watercam-006", "http://server.example.ts.net:8000"
        with patch.object(IPTransmitter, "_post_with_retry", return_value={"success": True}) as post, \
                patch("tools.transmit_ip._resolves_into_tailnet", return_value=True):
            tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1, want_command=True)
            tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1)
        assert post.call_args_list[0].args[1]["want_command"] is True
        assert "want_command" not in post.call_args_list[1].args[1]


class TestQueueFirst:
    """#311: the live reading is queued before it's sent."""

    def test_queued_before_the_send_and_unqueued_after_it(self):
        tx = _tx(reachable=True)
        order = []
        tx._enqueue_live.side_effect = lambda *a: order.append("queue") or "/q/live.json"
        tx.send_uplink.side_effect = lambda *a, **k: order.append("send") or {"success": True, "attempts": 1}
        m = _run(tx)
        assert order == ["queue", "send"]
        tx.unqueue.assert_called_once_with("/q/live.json")
        tx.keep_queued.assert_not_called()
        assert m["result"]["success"] is True

    def test_failed_send_keeps_it_queued(self):
        tx = _tx(reachable=True)
        tx._enqueue_live.return_value = "/q/live.json"
        tx.send_uplink.return_value = {"success": False, "attempts": 3, "error": "timeout"}
        _run(tx)
        tx.keep_queued.assert_called_once()
        assert tx.keep_queued.call_args.args[0] == "/q/live.json"
        tx.unqueue.assert_not_called()

    def test_an_error_mid_send_leaves_it_queued(self):
        tx = _tx(reachable=True)
        tx._enqueue_live.return_value = "/q/live.json"
        tx.send_uplink.side_effect = RuntimeError("boom")
        m = _run(tx)
        assert m["result"]["status"] == "error"
        tx._enqueue_live.assert_called_once()
        tx.unqueue.assert_not_called()

    def test_skipped_when_lora_is_healthy(self):
        tx = _tx(reachable=True)
        _run(tx, joined=True)
        tx._enqueue_live.assert_not_called()


class TestSharedCellularSession:
    """#337: the last upload to finish takes cellular down."""

    def test_uses_a_session_another_upload_brought_up(self):
        tx = _tx(reachable=False)
        m = _run(tx, later_status=ON_DEMAND_UP)    # idle at the start, up by the reachability step
        m["up"].assert_not_called()
        tx.wait_tailnet.assert_called_once_with(30)
        tx._drain_queue.assert_called_once_with(metered=True, skip=tx._enqueue_live.return_value)
        m["down"].assert_not_called()               # alone here (leave() -> no teardown owed)
        assert m["result"]["success"] is True

    def test_teardown_follows_leave(self):
        from unittest.mock import patch as _p
        tx = _tx(reachable=False)
        with _p("tools.lock_utils.CellularUsers.leave", return_value=False):
            m = _run(tx)                            # brought it up, another upload still running
        m["up"].assert_called_once()
        m["down"].assert_not_called()
        tx = _tx(reachable=False)
        with _p("tools.lock_utils.CellularUsers.leave", return_value=True):
            m = _run(tx, status=ON_DEMAND_UP)       # used another's session and was the last out
        m["down"].assert_called_once_with("Quectel")


class TestWiFiGraceBeforeCellular:
    """A saved WiFi network in range but not usable yet gets a bounded wait first."""

    def test_wifi_in_range_gets_a_wait_and_cellular_stays_down(self):
        tx = _tx(reachable=False)
        m = _run(tx, in_range=True)
        tx.wait_reachable.assert_called_once_with(45)
        m["up"].assert_not_called()
        assert m["result"]["success"] is True

    def test_wifi_that_never_comes_falls_back_to_cellular(self):
        tx = _tx(reachable=False)
        tx.wait_reachable.return_value = False   # the WiFi wait; cellular then waits on wait_tailnet
        m = _run(tx, in_range=True)
        tx.wait_reachable.assert_called_once_with(45)
        tx.wait_tailnet.assert_called_once_with(30)
        m["up"].assert_called_once()
        assert m["result"]["success"] is True

    def test_out_of_range_goes_straight_to_cellular(self):
        # A cellular-only field unit: no saved network in the scan, so no wait.
        tx = _tx(reachable=False)
        m = _run(tx, in_range=False)
        m["up"].assert_called_once()
        tx.wait_reachable.assert_not_called()
        tx.wait_tailnet.assert_called_once_with(30)

    def test_connected_wifi_without_the_server_yet_gets_the_wait(self):
        tx = _tx(reachable=False)
        m = _run(tx, wifi=True)
        tx.wait_reachable.assert_called_once_with(45)
        m["up"].assert_not_called()

    def test_zero_disables_the_wait(self):
        tx = _tx(reachable=False)
        tx.wifi_grace_s = 0
        m = _run(tx, in_range=True)
        m["in_range"].assert_not_called()
        m["up"].assert_called_once()

    def test_reachable_at_once_never_waits_or_scans(self):
        tx = _tx(reachable=True)
        m = _run(tx)
        tx.wait_reachable.assert_not_called()
        m["in_range"].assert_not_called()

    def test_cellular_brought_up_by_another_upload_during_the_wait_is_metered(self):
        # The LoRa fallback's upload brought cellular up while this one waited
        # for WiFi: the server answered over cellular, so drain as metered and
        # leave the session to the upload that started it.
        tx = _tx(reachable=False)
        m = _run(tx, in_range=True, later_status=ON_DEMAND_UP)
        assert m["status"].call_count == 2   # once at the start, once after the wait
        tx.wait_reachable.assert_called_once_with(45)
        tx._drain_queue.assert_called_once_with(metered=True, skip=tx._enqueue_live.return_value)
        m["up"].assert_not_called()
        m["down"].assert_not_called()
        assert m["result"]["success"] is True

    def test_wifi_connected_after_the_wait_drains_unmetered_even_with_cellular_up(self):
        # WiFi joined during the wait while another upload had cellular up:
        # the probe went over WiFi, so no metered drain.
        tx = _tx(reachable=False)
        m = _run(tx, wifi=True, later_status=ON_DEMAND_UP)
        tx._drain_queue.assert_called_once_with(metered=False, skip=tx._enqueue_live.return_value)
        m["up"].assert_not_called()

    def test_wifi_after_the_wait_drains_unmetered(self):
        tx = _tx(reachable=False)
        _run(tx, in_range=True)
        tx._drain_queue.assert_called_once_with(metered=False, skip=tx._enqueue_live.return_value)

    def test_cellular_already_up_doesnt_wait(self):
        tx = _tx(reachable=False)
        m = _run(tx, status=ON_DEMAND_UP, in_range=True)
        assert (45,) not in [c.args for c in tx.wait_reachable.call_args_list]


class TestWiFiInRange:
    """tools.transmit_ip.wifi_in_range against canned nmcli output."""

    @staticmethod
    def _nmcli(profiles, ssids, scan):
        def run(cmd, **kw):
            args = cmd[1:]
            if args[:5] == ["-t", "-f", "UUID,TYPE", "connection", "show"]:
                out = profiles
            elif args[:2] == ["-g", "802-11-wireless.ssid"]:
                out = ssids[args[-1]]
            elif args[:4] == ["-t", "-f", "SSID", "device"]:
                out = scan
            else:
                raise AssertionError(f"unexpected nmcli call {args}")
            return MagicMock(stdout=out)
        return run

    def _in_range(self, profiles, ssids, scan):
        with patch("tools.transmit_ip.subprocess.run", side_effect=self._nmcli(profiles, ssids, scan)):
            return transmit_ip.wifi_in_range()

    def test_saved_network_in_the_scan(self):
        assert self._in_range("u1:802-11-wireless\nu2:gsm\n", {"u1": "BenchNet\n"}, "Other\nBenchNet\n")

    def test_saved_network_not_in_the_scan(self):
        assert not self._in_range("u1:802-11-wireless\n", {"u1": "BenchNet\n"}, "Other\n")

    def test_no_saved_wifi(self):
        assert not self._in_range("u2:gsm\n", {}, "BenchNet\n")

    def test_colon_in_the_ssid(self):
        assert self._in_range("u1:802-11-wireless\n", {"u1": "Lab\\:5G\n"}, "Lab\\:5G\n")

    def test_nmcli_missing_means_not_in_range(self):
        with patch("tools.transmit_ip.subprocess.run", side_effect=OSError("no nmcli")):
            assert transmit_ip.wifi_in_range() is False


class TestHandleDownlinkReply:
    def test_poll_failed(self, capsys):
        from tools.transmit_ip import handle_downlink_reply
        out = handle_downlink_reply({"success": False, "error": "timeout"}, set_param_fn=lambda k, v: None)
        assert out == {"status": "poll_failed", "error": "timeout"}
        assert "poll failed: timeout" in capsys.readouterr().out

    def test_no_command_is_reported(self, capsys):
        from tools.transmit_ip import handle_downlink_reply
        out = handle_downlink_reply({"success": True, "commands": []},
                                    set_param_fn=lambda k, v: None, label="IP downlink after uplink")
        assert out == {"status": "no_command"}
        assert "IP downlink after uplink: no pending commands" in capsys.readouterr().out

    def test_command_is_applied_and_acked(self):
        from tools.transmit_ip import handle_downlink_reply
        acks = []
        with patch("tools.transmit_ip.apply_downlink_command",
                   return_value={"applied": ["photo_interval=5"], "skipped": ["99 99"]}):
            out = handle_downlink_reply({"success": True, "commands": [{"id": 3, "frame": "039999"}]},
                                        set_param_fn=lambda k, v: None, ack_fn=acks.append)
        assert out["status"] == "applied"
        assert out["applied_params"] == ["photo_interval=5"]
        assert out["acks"] == [{"id": 3, "result": 1}]
        assert acks == [[{"id": 3, "result": 1}]]


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
        left = sorted(int(f.split("_")[0]) for f in os.listdir(q) if f.endswith(".json"))
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


def test_modem_state_summary():
    out = ("  General  |  state: \x1b[32mregistered\x1b[0m\n"
           "           |  failed reason: --\n  Status   |  access tech: lte\n"
           "           |  signal quality: 41% (recent)\n  power state: on\n")
    with patch("tools.transmit_ip.subprocess.run", return_value=_proc(out=out)):
        assert transmit_ip._modem_state() == (
            "state registered, failed reason --, access tech lte, signal quality 41% (recent)")
    with patch("tools.transmit_ip.subprocess.run", side_effect=FileNotFoundError("mmcli")):
        assert transmit_ip._modem_state() == ""


class TestNmcli:
    @pytest.fixture(autouse=True)
    def usb_already_on(self):
        # these tests patch the shared subprocess module, so tools.usb_power
        # would think it had just powered the modem and wait for it
        with patch("tools.usb_power.usb_power_on", return_value=False), \
             patch("tools.transmit_ip._modem_state", return_value="state searching"):
            yield

    def test_up_runs_nmcli_with_wait(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc()) as run:
            assert transmit_ip.cellular_up("Quectel", 60) is True
        assert run.call_args[0][0] == ["nmcli", "--wait", "60", "connection", "up", "Quectel"]

    def test_up_failure_returns_false(self):
        # out of time: no retry
        clock = itertools.count(0, 100)
        with patch("tools.transmit_ip.time.monotonic", side_effect=lambda: next(clock)), \
             patch("tools.transmit_ip.subprocess.run", return_value=_proc(4)) as run:
            assert transmit_ip.cellular_up("Quectel") is False
        assert run.call_count == 1

    def test_up_retries_until_the_modem_registers(self):
        # The modem is there but not registered yet: activation fails, then works
        unreg = MagicMock(returncode=4, stdout="", stderr=(
            "Error: Connection activation failed: The device could not be readied for configuration"))
        with patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.subprocess.run", side_effect=[unreg, unreg, _proc()]) as run:
            assert transmit_ip.cellular_up("Quectel", 90) is True
        assert run.call_count == 3

    def test_up_logs_the_modem_state_on_failure(self, caplog):
        unreg = MagicMock(returncode=4, stdout="", stderr="Error: Connection activation failed")
        with patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.subprocess.run", side_effect=[unreg, _proc()]):
            assert transmit_ip.cellular_up("Quectel", 90) is True
        assert "state searching" in caplog.text and "retrying" in caplog.text

    def test_up_gives_up_on_registration_at_the_deadline(self):
        unreg = MagicMock(returncode=4, stdout="", stderr="Error: Connection activation failed")
        clock = itertools.count(0, 20)
        with patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.time.monotonic", side_effect=lambda: next(clock)), \
             patch("tools.transmit_ip.subprocess.run", return_value=unreg) as run:
            assert transmit_ip.cellular_up("Quectel", 90) is False
        assert 1 < run.call_count < 10

    def test_up_does_not_retry_a_missing_connection(self):
        gone = MagicMock(returncode=10, stdout="", stderr="Error: unknown connection 'Quectel'.")
        with patch("tools.transmit_ip.time.sleep") as sleep, \
             patch("tools.transmit_ip.subprocess.run", return_value=gone) as run:
            assert transmit_ip.cellular_up("Quectel", 90) is False
        assert run.call_count == 1
        sleep.assert_not_called()


    def test_up_retries_until_the_modem_appears(self):
        missing = MagicMock(returncode=10, stdout="", stderr="Error: No suitable device found for this connection")
        with patch("tools.usb_power.usb_power_on", return_value=True) as on, \
             patch("tools.usb_power.wait_for", return_value=True), \
             patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.subprocess.run", side_effect=[missing, missing, _proc()]) as run:
            assert transmit_ip.cellular_up("Quectel", 60) is True
        on.assert_called_once()
        assert run.call_count == 3

    def test_up_gives_up_when_the_modem_never_appears(self):
        missing = MagicMock(returncode=10, stdout="", stderr="Error: No suitable device found for this connection")
        clock = itertools.count(0, 20)
        with patch("tools.usb_power.usb_power_on", return_value=True), \
             patch("tools.usb_power.wait_for", return_value=False), \
             patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.time.monotonic", side_effect=lambda: next(clock)), \
             patch("tools.transmit_ip.subprocess.run", return_value=missing):
            assert transmit_ip.cellular_up("Quectel", 60) is False

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


class TestWaitTailnet:
    def _status(self, self_online=True, peer_online=True):
        return json.dumps({"Self": {"Online": self_online},
                           "Peer": {"k": {"DNSName": "server.example.ts.net.", "Online": peer_online}}})

    def test_online_peer(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(out=self._status())):
            assert transmit_ip.tailscale_online("server.example.ts.net") is True
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(out=self._status(self_online=False))):
            assert transmit_ip.tailscale_online("server.example.ts.net") is False

    def test_unknown_peer_or_no_tailscale_is_none(self):
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(out=self._status())):
            assert transmit_ip.tailscale_online("other.example.ts.net") is None
        with patch("tools.transmit_ip.subprocess.run", side_effect=FileNotFoundError("tailscale")):
            assert transmit_ip.tailscale_online("server.example.ts.net") is None

    def test_not_running_yet_is_false_not_none(self):
        # Booted with no network: no peers until a link comes up (006, 2026-11-17)
        for state in ("NoState", "Starting", "NeedsLogin"):
            with patch("tools.transmit_ip.subprocess.run",
                       return_value=_proc(out=json.dumps({"BackendState": state, "Self": {"Online": False}}))):
                assert transmit_ip.tailscale_online("server.example.ts.net") is False
        running = json.loads(self._status())
        running["BackendState"] = "Running"
        with patch("tools.transmit_ip.subprocess.run", return_value=_proc(out=json.dumps(running))):
            assert transmit_ip.tailscale_online("server.example.ts.net") is True

    def test_wait_tailnet_waits_out_nostate_after_cellular(self):
        # Cellular just came up and Tailscale hasn't started: wait_tailnet waits for it
        # instead of returning at once (the POST was then refused as untrusted)
        nostate = json.dumps({"BackendState": "NoState", "Self": {"Online": False}})
        running = json.dumps({"BackendState": "Running", "Self": {"Online": True},
                              "Peer": {"k": {"DNSName": "server.example.ts.net.", "Online": True}}})
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        with patch("tools.transmit_ip.subprocess.run",
                   side_effect=[_proc(out=nostate)] * 3 + [_proc(out=running)]) as run, \
             patch("tools.transmit_ip.trusted_server", return_value=True), \
             patch("tools.transmit_ip.time.sleep"):
            assert tx.wait_tailnet(30) is True
        assert run.call_count == 4

    def test_wait_tailnet_waits_until_online(self):
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        with patch("tools.transmit_ip.tailscale_online", side_effect=[False, False, True]) as online, \
             patch("tools.transmit_ip.trusted_server", return_value=True), \
             patch("tools.transmit_ip.time.sleep"):
            assert tx.wait_tailnet(30) is True
        assert online.call_count == 3

    def test_wait_tailnet_waits_for_the_name_to_resolve(self):
        # Running, but MagicDNS doesn't answer for the server yet: trusted_server()
        # would refuse the POST, so keep waiting (011, 2026-11-18)
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        with patch("tools.transmit_ip.tailscale_online", return_value=True), \
             patch("tools.transmit_ip.trusted_server", side_effect=[False, False, True]) as trusted, \
             patch("tools.transmit_ip.time.sleep"):
            assert tx.wait_tailnet(30) is True
        assert trusted.call_count == 3

    def test_wait_tailnet_gives_up_if_the_name_never_resolves(self):
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        clock = itertools.count(0, 20)
        with patch("tools.transmit_ip.tailscale_online", return_value=True), \
             patch("tools.transmit_ip.trusted_server", return_value=False), \
             patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.time.monotonic", side_effect=lambda: next(clock)):
            assert tx.wait_tailnet(30) is False

    def test_wait_tailnet_waits_when_it_cant_tell(self):
        # Status unreadable or the server not listed yet (None): wait for trust too
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        with patch("tools.transmit_ip.tailscale_online", side_effect=[None, None, True]), \
             patch("tools.transmit_ip.trusted_server", side_effect=[False, False, True]) as trusted, \
             patch("tools.transmit_ip.time.sleep"):
            assert tx.wait_tailnet(30) is True
        assert trusted.call_count == 3

    def test_wait_tailnet_waits_at_least_the_floor(self):
        # The main loop passes 30 s; a weak cellular link gets TAILNET_WAIT_MIN_S
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        clock = itertools.count(0, 10)
        with patch("tools.transmit_ip.tailscale_online", return_value=False) as online, \
             patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.time.monotonic", side_effect=lambda: next(clock)):
            assert tx.wait_tailnet(30) is False
        assert online.call_count == transmit_ip.TAILNET_WAIT_MIN_S // 10

    def test_wait_tailnet_gives_up(self):
        tx = IPTransmitter(override_url="http://server.example.ts.net:8000")
        clock = itertools.count(0, 20)
        with patch("tools.transmit_ip.tailscale_online", return_value=False), \
             patch("tools.transmit_ip.time.sleep"), \
             patch("tools.transmit_ip.time.monotonic", side_effect=lambda: next(clock)):
            assert tx.wait_tailnet(30) is False

    def test_non_tailnet_server_needs_no_wait(self):
        tx = IPTransmitter(override_url="http://192.0.2.10:8000")
        with patch("tools.transmit_ip.tailscale_online") as online:
            assert tx.wait_tailnet(30) is True
        online.assert_not_called()


class TestMovedUnit:
    def test_moved_unit_sends_over_ip_even_with_lora_joined(self, tmp_path, monkeypatch):
        import json
        from tools import gps_home
        monkeypatch.setattr(gps_home, "HOME_PATH", str(tmp_path / "gps_home.json"))
        (tmp_path / "gps_home.json").write_text(json.dumps({"home": [10.0, 20.0], "moved": True}))
        tx = _tx(reachable=True)
        m = _run(tx, joined=True, wifi=True)
        assert m["result"]["success"] is True
        channels = tx.send_uplink.call_args[0][0]
        codes = {c["code"]: c["payload_hex"] for c in channels}
        assert codes["01 06"] == "00000001"

    def test_unmoved_unit_still_skips_ip_when_lora_joined(self, tmp_path, monkeypatch):
        from tools import gps_home
        monkeypatch.setattr(gps_home, "HOME_PATH", str(tmp_path / "gps_home.json"))
        tx = _tx(reachable=True)
        m = _run(tx, joined=True, wifi=True)
        assert m["result"]["status"] == "skipped_lora_joined"
