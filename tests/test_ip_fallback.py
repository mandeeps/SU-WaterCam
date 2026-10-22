"""IP fallback (tools/ip_fallback.py): an IP upload when LoRa can't carry bitmaps.

The node's own rule (no bitmap delivered for ip_fallback_after_hours while LoRa
holds bitmaps back, at most once per ip_fallback_min_interval_hours) and the
server's 17 97 NN request (an upload in each of the next NN wakes, 00 cancels).
"""
import json
import os
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import ip_fallback as ipf  # noqa: E402
from tools import lora_store_forward as lsf  # noqa: E402

NOW = 1_760_000_000
H = 3600


def _decide(state, pending=1, have_bitmap=True, boot="b1", after=12, interval=6, now=NOW):
    return ipf.decide(state, now, after_hours=after, min_interval_hours=interval,
                      lora_pending_bitmaps=pending, have_bitmap=have_bitmap, boot_id=boot)


# ── the node's own rule ──────────────────────────────────────────────────────

class TestDecide:
    def test_recent_lora_bitmap_needs_nothing(self):
        up, src, why = _decide({"lora_at": NOW - 2 * H})
        assert not up and src == "" and "not needed" in why

    def test_stale_and_held_back_uploads(self):
        up, src, why = _decide({"lora_at": NOW - 13 * H, "ip_at": NOW - 20 * H})
        assert up and src == "stale" and "13.0 h" in why

    def test_newest_of_lora_and_ip_counts(self):
        assert not _decide({"lora_at": NOW - 30 * H, "ip_at": NOW - 1 * H})[0]

    def test_stale_but_lora_not_holding_bitmaps_back(self):
        # e.g. the LoRa send policy skipped unchanged bitmaps: nothing is queued
        up, _, why = _decide({"lora_at": NOW - 30 * H}, pending=0)
        assert not up and "isn't holding" in why

    def test_no_bitmap_this_capture(self):
        assert not _decide({"lora_at": NOW - 30 * H}, have_bitmap=False)[0]

    def test_rate_limited(self):
        state = {"lora_at": NOW - 30 * H, "fallback_at": NOW - 5 * H}
        up, _, why = _decide(state)
        assert not up and "min 6 h" in why
        state["fallback_at"] = NOW - 6 * H
        assert _decide(state)[0]

    def test_zero_turns_it_off(self):
        up, _, why = _decide({"lora_at": NOW - 300 * H}, after=0)
        assert not up and "off" in why

    def test_never_delivered_counts_from_first_seen(self):
        assert not _decide({"first_seen": NOW - 1 * H})[0]
        assert _decide({"first_seen": NOW - 12 * H})[0]

    def test_boundary(self):
        assert not _decide({"lora_at": NOW - 12 * H + 1})[0]
        assert _decide({"lora_at": NOW - 12 * H})[0]


class TestServerRequest:
    def test_request_overrides_everything(self):
        state = {"lora_at": NOW, "request": {"wakes": 2, "at": NOW - 60}}
        up, src, _ = _decide(state, pending=0, have_bitmap=False, after=0)
        assert up and src == "request"

    def test_one_upload_per_wake(self):
        state = {"request": {"wakes": 1, "at": NOW - 60, "boot": "b1"}}
        up, _, why = _decide(state, boot="b1")
        assert not up and "already uploaded this wake" in why
        assert _decide(state, boot="b2")[0]

    def test_old_request_expires(self):
        state = {"lora_at": NOW, "request": {"wakes": 3, "at": NOW - ipf.REQUEST_MAX_AGE_S - 1}}
        assert not _decide(state)[0]
        assert ipf.pending_wakes(state, NOW) == 0

    def test_request_count_and_cancel(self):
        assert "next 2 wake" in ipf.request(2, "lora", now=NOW)
        assert ipf.pending_wakes(now=NOW) == 2
        assert ipf.uploaded("request", "b1") == 1
        assert ipf.uploaded("request", "b1") == 1      # same wake: counted once
        assert ipf.uploaded("request", "b2") == 0
        assert ipf.pending_wakes(now=NOW) == 0
        ipf.request(3, "lora", now=NOW + 3600)
        assert ipf.request(0, "lora", now=NOW + 3601) == "cancelled"
        assert ipf.pending_wakes(now=NOW + 3602) == 0

    def test_resend_does_not_restart_the_count(self):
        ipf.request(1, "lora", now=NOW)
        ipf.uploaded("request", "b1")
        # the API resends until it sees an answer: the same request minutes later
        assert "repeat" in ipf.request(1, "lora", now=NOW + 300)
        assert ipf.pending_wakes(now=NOW + 301) == 0
        # a new request later is a new request
        ipf.request(1, "lora", now=NOW + ipf.REPEAT_WINDOW_S + 1)
        assert ipf.pending_wakes(now=NOW + ipf.REPEAT_WINDOW_S + 2) == 1

    def test_stale_upload_does_not_spend_a_request(self):
        ipf.request(2, "lora", now=NOW)
        ipf.uploaded("stale", "b1")
        assert ipf.pending_wakes(now=NOW) == 2


class TestPlanAndState:
    def test_plan_records_first_seen_and_fallback_time(self):
        assert not ipf.plan(True, "b1", 1, now=NOW)[0]
        assert ipf.load()["first_seen"] == NOW
        up, src, _ = ipf.plan(True, "b1", 1, now=NOW + 13 * H)
        assert up and src == "stale"
        assert ipf.load()["fallback_at"] == NOW + 13 * H
        assert not ipf.plan(True, "b1", 1, now=NOW + 14 * H)[0]     # rate limit

    def test_state_file_is_json(self):
        ipf.record_delivery("lora", now=NOW)
        with open(ipf.STATE_FILE) as f:
            assert json.load(f)["lora_at"] == NOW

    def test_wifi_wanted(self):
        ipf.record_delivery("lora", now=NOW - 20 * H)
        assert ipf.wifi_wanted(now=NOW, lora_pending_bitmaps=2)
        assert not ipf.wifi_wanted(now=NOW, lora_pending_bitmaps=0)


# ── where deliveries and requests come from ──────────────────────────────────

def test_is_bitmap_packet():
    assert ipf.is_bitmap_packet("081800050102")
    assert ipf.is_bitmap_packet(b"\x08\x18\x00\x01")
    assert not ipf.is_bitmap_packet("0001000000")
    assert not ipf.is_bitmap_packet(None)


@pytest.mark.parametrize("content, sent, recorded", [
    ("0818000401020304", True, True),
    ("0818000401020304", False, False),     # not sent: still queued, not delivered
    ("000100000000", True, False),          # a sensor packet
])
def test_lora_client_records_bitmap_delivery(content, sent, recorded):
    from tools.lora_handler_concurrent import LoRaHandlerClient
    client = LoRaHandlerClient(socket_path="/nonexistent")
    with patch.object(LoRaHandlerClient, "_call", return_value=sent):
        assert client.transmit(content) is sent
    assert ("lora_at" in ipf.load()) is recorded


def test_pending_bitmaps(tmp_path):
    q = str(tmp_path)
    lsf.enqueue("0001", "081800", 1000, queue_dir=q)
    lsf.enqueue("0001", None, 1001, queue_dir=q)
    assert lsf.pending_bitmaps(q) == 1
    assert lsf.pending_bitmaps(str(tmp_path / "missing")) == 0


def test_downlink_table_and_ip_command():
    from tools.transmit_ip import DOWNLINK_CODE_LENGTHS, apply_downlink_command, parse_downlink_frame
    assert DOWNLINK_CODE_LENGTHS["17 97"] == 1
    assert parse_downlink_frame(bytes.fromhex("179703")) == [{"code": "17 97", "payload_hex": "03"}]
    r = apply_downlink_command({"parts": [{"code": "17 97", "payload_hex": "03"}]}, set_param_fn=lambda k, v: None)
    assert r["applied"] == ["ip_upload_wakes=3"]
    assert ipf.pending_wakes() == 3


def test_lora_frame_is_applied_and_answered():
    import tempfile
    from tools.lora_handler_concurrent import LoRaHandler
    h = LoRaHandler.__new__(LoRaHandler)
    h.config, h.config_file, h.runtime_callback = {}, os.path.join(tempfile.mkdtemp(), "c.json"), None
    with patch.object(LoRaHandler, "_reply_with_status") as reply:
        h.decode("05179701")
    assert ipf.pending_wakes() == 1
    reply.assert_called_once_with(ack=(5, 0))      # the ack lets the API confirm it


def test_status_reply_carries_pending_requests():
    from tools.lora_debug_integration import collect_compact_status
    assert "ipr" not in dict(collect_compact_status(False))
    ipf.request(2, "lora")
    fields = collect_compact_status(False)
    assert dict(fields)["ipr"] == 2
    assert [k for k, _ in fields][:3] == ["dbg", "em", "ipr"]


def test_runtime_params_are_settable():
    from tools.lora_runtime_integration import LoRaRuntimeManager as M
    assert M._DEFAULT_PARAMS["ip_fallback_after_hours"] == 12
    assert M._DEFAULT_PARAMS["ip_fallback_min_interval_hours"] == 6
    assert M._PARAM_RANGES["ip_fallback_after_hours"] == (0, 168)


# ── WiFi policy ──────────────────────────────────────────────────────────────

def test_wifi_stays_on_when_fallback_due():
    from tools import wifi_policy
    with patch.object(wifi_policy, "_session_active", return_value=False), \
         patch.object(wifi_policy, "_keep_on_param", return_value=False), \
         patch.object(wifi_policy, "_moved", return_value=False), \
         patch.object(wifi_policy, "radio_is_on", return_value=True), \
         patch.object(wifi_policy, "radio") as radio:
        with patch.object(wifi_policy, "_ip_fallback_due", return_value=True):
            assert wifi_policy.apply({"healthy": True}) == "WiFi on: IP fallback upload due"
        radio.assert_not_called()
        with patch.object(wifi_policy, "_ip_fallback_due", return_value=False):
            assert wifi_policy.apply({"healthy": True}) == "WiFi off: LoRa is healthy"
        radio.assert_called_once_with(False)


# ── ip_uplink_transmit ───────────────────────────────────────────────────────

def _tx(enabled=False, reachable=True):
    tx = MagicMock()
    tx.enabled = enabled
    tx.configured = True
    tx.only_if_lora_unavailable = True
    tx.cellular_connection = "Quectel"
    tx.cellular_connect_timeout_s = 90
    tx.wifi_grace_s = 0
    tx.is_reachable.return_value = reachable
    tx.wait_tailnet.return_value = True
    tx.server_only_down.return_value = False
    tx._drain_queue.return_value = {"drained": 0, "failed": False, "failed_file": None, "drained_ts": []}
    tx.send_uplink.return_value = {"success": True, "attempts": 1}
    tx.poll_downlink.return_value = {"success": True, "command": None}
    return tx


def _run(tx, healthy=True, pending=1, bitmap=(1, 2, 3)):
    import ticktalk_main
    m = {}
    with ExitStack() as st:
        st.enter_context(patch("tools.aht20_temperature.get_aht20", return_value={}))
        st.enter_context(patch("tools.get_gps.get_location_with_retry", return_value=(None, None)))
        st.enter_context(patch("tools.battery_manager.get_battery_status",
                               return_value={"battery_pct": 80, "battery_source": "test"}))
        st.enter_context(patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d: d))
        st.enter_context(patch("tools.pi_power.get_throttled", return_value=0))
        st.enter_context(patch("tools.transmit_ip.IPTransmitter", return_value=tx))
        st.enter_context(patch("tools.transmit_ip.cellular_status",
                               return_value={"exists": True, "active": False, "autoconnect": False}))
        st.enter_context(patch("tools.transmit_ip.wifi_connected", return_value=False))
        st.enter_context(patch("tools.transmit_ip.wifi_in_range", return_value=False))
        st.enter_context(patch("tools.lora_health.assess", return_value={"healthy": healthy, "reason": "t"}))
        st.enter_context(patch("tools.lora_store_forward.pending_bitmaps", return_value=pending))
        st.enter_context(patch("tools.lora_store_forward.remove_delivered", return_value=0))
        st.enter_context(patch("tools.config_io.current_boot_id", return_value="boot-1"))
        m["wifi_on"] = st.enter_context(patch("tools.wifi_policy.ensure_on", return_value=False))
        m["up"] = st.enter_context(patch("tools.transmit_ip.cellular_up", return_value=True))
        m["down"] = st.enter_context(patch("tools.transmit_ip.cellular_down"))
        m["result"] = ticktalk_main.ip_uplink_transmit.__wrapped__(
            bitmap=list(bitmap), _sensor_tracker=None, dirname=None)
    return m


class TestUplink:
    def test_disabled_and_nothing_due(self):
        tx = _tx()
        ipf.record_delivery("lora")
        assert _run(tx)["result"]["status"] == "disabled"
        tx.send_uplink.assert_not_called()

    def test_server_request_uploads_with_ip_off_and_lora_healthy(self):
        ipf.request(2, "lora")
        tx = _tx()
        m = _run(tx, healthy=True)
        assert m["result"]["success"] is True
        assert tx.send_uplink.call_args.kwargs["ip_fallback"] is True
        m["wifi_on"].assert_called_once()
        assert ipf.pending_wakes() == 1
        assert "ip_at" in ipf.load()
        # second capture of the same wake: no second upload
        tx2 = _tx()
        assert _run(tx2)["result"]["status"] == "disabled"
        tx2.send_uplink.assert_not_called()

    def test_stale_fallback_over_cellular_takes_it_down_after(self):
        import time
        ipf._update(lambda s: s.update(first_seen=time.time() - 20 * H, lora_at=time.time() - 20 * H))
        tx = _tx(reachable=False)
        m = _run(tx, healthy=True, pending=3)
        assert m["result"]["success"] is True
        m["up"].assert_called_once()
        m["down"].assert_called_once_with("Quectel")
        assert "fallback_at" in ipf.load()

    def test_stale_but_nothing_held_back(self):
        import time
        ipf._update(lambda s: s.update(first_seen=time.time() - 20 * H, lora_at=time.time() - 20 * H))
        tx = _tx()
        assert _run(tx, pending=0)["result"]["status"] == "disabled"

    def test_unconfigured_server_never_sends(self):
        ipf.request(1, "lora")
        tx = _tx()
        tx.configured = False
        assert _run(tx)["result"]["status"] == "disabled"
        assert ipf.pending_wakes() == 1

    def test_enabled_routine_upload_records_ip_bitmap(self):
        tx = _tx(enabled=True)
        m = _run(tx, healthy=False)
        assert m["result"]["success"] is True
        assert tx.send_uplink.call_args.kwargs["ip_fallback"] is False
        assert "ip_at" in ipf.load()
        m["down"].assert_not_called()
