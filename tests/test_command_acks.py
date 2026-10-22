"""Server command frames: applied once, acked every time (docs/SYSTEM_REFERENCE.md, "Command frames").

A frame is [seq][code][value]...; tools/command_log.py remembers what was applied,
tools/transmit_ip.py parses and applies frames and posts the acks, and the LoRa
handler answers each frame with a status reply that leads with {"ak":seq}.
"""
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import command_log                                   # noqa: E402
from tools import lora_debug_integration as dbg                 # noqa: E402
from tools import transmit_ip as tip                            # noqa: E402
from tools.lora_handler_concurrent import LoRaHandler           # noqa: E402


# ── tools/command_log.py ─────────────────────────────────────────────────────

class TestCommandLog:
    FRAME = bytes.fromhex("07109032")

    def test_unseen_until_recorded(self, tmp_path):
        p = str(tmp_path / "applied.json")
        assert command_log.seen(self.FRAME, path=p) is None
        command_log.record(self.FRAME, command_log.PARTLY, path=p)
        assert command_log.seen(self.FRAME, path=p) == command_log.PARTLY

    def test_keyed_by_seq_and_hash(self, tmp_path):
        p = str(tmp_path / "applied.json")
        command_log.record(self.FRAME, 0, path=p)
        assert command_log.seen(bytes.fromhex("08109032"), path=p) is None    # same blocks, other seq
        assert command_log.seen(bytes.fromhex("07109033"), path=p) is None    # same seq, other blocks

    def test_forgotten_after_keep_s(self, tmp_path):
        p = str(tmp_path / "applied.json")
        command_log.record(self.FRAME, 0, now=1000.0, path=p)
        assert command_log.seen(self.FRAME, now=1000.0 + command_log.KEEP_S, path=p) == 0
        assert command_log.seen(self.FRAME, now=1001.0 + command_log.KEEP_S, path=p) is None

    def test_seq_zero_is_never_remembered(self, tmp_path):
        p = str(tmp_path / "applied.json")
        command_log.record(bytes.fromhex("00109032"), 0, path=p)
        assert not os.path.exists(p)
        assert command_log.seen(bytes.fromhex("00109032"), path=p) is None
        assert command_log.seen(b"", path=p) is None

    def test_recording_again_replaces_the_entry(self, tmp_path):
        p = str(tmp_path / "applied.json")
        command_log.record(self.FRAME, 0, path=p)
        command_log.record(self.FRAME, 2, path=p)
        assert command_log.seen(self.FRAME, path=p) == 2
        assert len(json.load(open(p))["applied"]) == 1

    def test_only_the_last_max_entries_are_kept(self, tmp_path):
        p = str(tmp_path / "applied.json")
        frames = [bytes([i + 1, 0x10, 0x90, i]) for i in range(command_log.MAX_ENTRIES + 6)]
        for i, f in enumerate(frames):
            command_log.record(f, 0, now=1000.0 + i, path=p)
        assert len(json.load(open(p))["applied"]) == command_log.MAX_ENTRIES
        assert command_log.seen(frames[0], now=1100.0, path=p) is None
        assert command_log.seen(frames[-1], now=1100.0, path=p) == 0

    def test_pending_acks_add_list_clear(self, tmp_path):
        p = str(tmp_path / "acks.json")
        assert command_log.pending_acks(path=p) == []
        command_log.add_pending_acks([{"id": 1, "result": 0}, {"id": 2, "result": 2}], path=p)
        command_log.add_pending_acks([{"id": 2, "result": 1}, {"id": 3}, {"nope": 1}], path=p)
        assert command_log.pending_acks(path=p) == [
            {"id": 1, "result": 0}, {"id": 2, "result": 1}, {"id": 3, "result": 0}]   # id 2 replaced
        command_log.clear_pending_acks([1, 3], path=p)
        assert command_log.pending_acks(path=p) == [{"id": 2, "result": 1}]
        command_log.clear_pending_acks([], path=p)
        assert command_log.pending_acks(path=p) == [{"id": 2, "result": 1}]

    def test_pending_acks_are_capped(self, tmp_path):
        p = str(tmp_path / "acks.json")
        command_log.add_pending_acks([{"id": i, "result": 0} for i in range(command_log.MAX_PENDING_ACKS + 5)], path=p)
        acks = command_log.pending_acks(path=p)
        assert len(acks) == command_log.MAX_PENDING_ACKS
        assert acks[-1]["id"] == command_log.MAX_PENDING_ACKS + 4

    def test_corrupt_file_reads_as_empty(self, tmp_path):
        p = tmp_path / "acks.json"
        p.write_text("{not json")
        assert command_log.pending_acks(path=str(p)) == []
        assert command_log.seen(self.FRAME, path=str(p)) is None


# ── parse_command_frame / apply_command_frame ────────────────────────────────

def _apply_parts_recorder(applied=("area_threshold=50%",), skipped=()):
    calls = []

    def apply_parts(parts):
        calls.append(parts)
        return {"applied": list(applied), "skipped": list(skipped)}
    apply_parts.calls = calls
    return apply_parts


class TestApplyCommandFrame:
    def test_parse(self):
        seq, parts = tip.parse_command_frame(bytes.fromhex("07109032"))
        assert seq == 7 and parts == [{"code": "10 90", "payload_hex": "32"}]
        assert tip.parse_command_frame(b"") is None
        assert tip.parse_command_frame(bytes.fromhex("07ffff")) == (7, None)     # rest is not a valid frame

    def test_valid_frame_is_applied_once(self):
        f = bytes.fromhex("07109032")
        first = tip.apply_command_frame(f, ap1 := _apply_parts_recorder())
        assert first["seq"] == 7 and first["result"] == 0 and first["duplicate"] is False
        assert first["applied"] == ["area_threshold=50%"] and len(ap1.calls) == 1
        again = tip.apply_command_frame(f, ap2 := _apply_parts_recorder())
        assert again["duplicate"] is True and again["result"] == 0 and again["seq"] == 7
        assert ap2.calls == []                          # apply_parts not called

    def test_duplicate_returns_the_stored_result(self):
        f = bytes.fromhex("07109032")
        tip.apply_command_frame(f, _apply_parts_recorder(skipped=["13 93"]))     # partly: 1
        again = tip.apply_command_frame(f, ap := _apply_parts_recorder())
        assert again["duplicate"] and again["result"] == 1 and ap.calls == []

    def test_invalid_rest_is_rejected(self):
        ap = _apply_parts_recorder()
        r = tip.apply_command_frame(bytes.fromhex("09ffff"), ap)
        assert r["result"] == 2 and r["seq"] == 9 and ap.calls == []
        # and it is remembered: a resend is acked the same way without being re-parsed
        assert command_log.seen(bytes.fromhex("09ffff")) == 2

    def test_partly_applied_is_1_and_nothing_applied_is_2(self):
        assert tip.apply_command_frame(bytes.fromhex("0a109032"),
                                       _apply_parts_recorder(skipped=["13 93"]))["result"] == 1
        assert tip.apply_command_frame(bytes.fromhex("0b109032"),
                                       _apply_parts_recorder(applied=[], skipped=["10 90"]))["result"] == 2

    def test_seq_zero_is_applied_every_time(self):
        f = bytes.fromhex("00109032")
        ap = _apply_parts_recorder()
        r1 = tip.apply_command_frame(f, ap)
        r2 = tip.apply_command_frame(f, ap)
        assert r1["seq"] == r2["seq"] == 0 and not r1["duplicate"] and not r2["duplicate"]
        assert len(ap.calls) == 2

    def test_empty_frame_is_rejected(self):
        assert tip.apply_command_frame(b"", _apply_parts_recorder())["result"] == 2


# ── apply_downlink_command: widths and ranges ────────────────────────────────

def _blocks(code, value_hex):
    return {"parts": [{"code": code, "payload_hex": value_hex}]}


class TestDownlinkCodes:
    def test_code_widths_table(self):
        assert tip.DOWNLINK_CODE_LENGTHS["12 92"] == 2
        assert tip.DOWNLINK_CODE_LENGTHS["13 93"] == tip.DOWNLINK_CODE_LENGTHS["14 94"] == 1
        assert tip.DOWNLINK_CODE_LENGTHS["50 01"] == 0 and "21" not in tip.DOWNLINK_CODE_LENGTHS

    @pytest.mark.parametrize("code, name, value_hex, minutes", [
        ("12 92", "monitoring_frequency", "0001", 1),
        ("12 92", "monitoring_frequency", "2760", 10080),
        ("13 93", "emergency_frequency", "01", 1),
        ("13 93", "emergency_frequency", "f0", 240),
        ("14 94", "neighborhood_emergency_frequency", "01", 1),
        ("14 94", "neighborhood_emergency_frequency", "f0", 240),
    ])
    def test_interval_in_range_is_applied(self, code, name, value_hex, minutes):
        store = {}
        r = tip.apply_downlink_command(_blocks(code, value_hex), lambda k, v: store.__setitem__(k, v))
        assert store == {name: minutes} and r["skipped"] == []

    @pytest.mark.parametrize("code, value_hex", [
        ("12 92", "0000"), ("12 92", "2761"), ("12 92", "ffff"),
        ("13 93", "00"), ("13 93", "f1"), ("14 94", "00"), ("14 94", "f1"),
    ])
    def test_interval_out_of_range_is_skipped(self, code, value_hex):
        store = {}
        r = tip.apply_downlink_command(_blocks(code, value_hex), lambda k, v: store.__setitem__(k, v))
        assert store == {} and r["skipped"] == [code] and r["applied"] == []

    def test_set_param_returning_false_is_skipped(self):
        r = tip.apply_downlink_command(_blocks("12 92", "003c"), lambda k, v: False)
        assert r["skipped"] == ["12 92"] and r["applied"] == []

    def test_status_request_is_applied(self):
        set_param = MagicMock()
        r = tip.apply_downlink_command(_blocks("50 01", ""), set_param)
        assert r["applied"] == ["status_request"] and r["skipped"] == []
        set_param.assert_not_called()


# ── handle_downlink_reply ────────────────────────────────────────────────────

def _reply(*cmds):
    return {"success": True, "commands": list(cmds)}


class TestHandleDownlinkReply:
    def test_applies_in_order_and_acks_once_with_all_results(self):
        order, acks = [], []
        set_param = lambda k, v: order.append((k, v))               # noqa: E731
        out = tip.handle_downlink_reply(
            _reply({"id": 41, "frame": "07109032"},
                   {"id": 42, "frame": "08139305"},
                   {"id": 43, "frame": "09ffff"}),                  # invalid: rejected
            set_param_fn=set_param, ack_fn=acks.append)
        assert order == [("area_threshold", 50), ("emergency_frequency", 5)]
        assert acks == [[{"id": 41, "result": 0}, {"id": 42, "result": 0}, {"id": 43, "result": 2}]]
        assert out["status"] == "applied" and out["acks"] == acks[0]

    def test_duplicates_are_acked_again_but_not_applied_again(self):
        calls, acks = [], []
        reply = _reply({"id": 41, "frame": "07109032"})
        tip.handle_downlink_reply(reply, set_param_fn=lambda k, v: calls.append(k), ack_fn=acks.append)
        tip.handle_downlink_reply(reply, set_param_fn=lambda k, v: calls.append(k), ack_fn=acks.append)
        assert calls == ["area_threshold"]
        assert acks == [[{"id": 41, "result": 0}]] * 2

    def test_malformed_entries_are_ignored(self):
        acks = []
        out = tip.handle_downlink_reply(
            _reply({"id": 1}, {"frame": "07109032"}, {"id": "x", "frame": "07109032"},
                   {"id": 2, "frame": "zz"}, "junk", {"id": 3, "frame": "0a109032"}),
            set_param_fn=lambda k, v: None, ack_fn=acks.append)
        assert acks == [[{"id": 3, "result": 0}]] and out["status"] == "applied"

    def test_only_malformed_entries_means_no_ack(self):
        ack_fn = MagicMock()
        tip.handle_downlink_reply(_reply({"id": 1}), set_param_fn=lambda k, v: None, ack_fn=ack_fn)
        ack_fn.assert_not_called()

    def test_no_commands(self):
        ack_fn = MagicMock()
        out = tip.handle_downlink_reply(_reply(), set_param_fn=lambda k, v: None, ack_fn=ack_fn)
        assert out == {"status": "no_command"}
        ack_fn.assert_not_called()

    def test_default_ack_goes_through_post_acks(self):
        with patch.object(tip, "post_acks") as post:
            tip.handle_downlink_reply(_reply({"id": 5, "frame": "0b109032"}), set_param_fn=lambda k, v: None)
        post.assert_called_once_with([{"id": 5, "result": 0}])


# ── IPTransmitter: post_acks, send_uplink, poll_downlink ─────────────────────

TRUSTED = "http://100.101.102.103:8000"   # a tailnet address: trusted without DNS


def _tx(url=TRUSTED):
    tx = tip.IPTransmitter.__new__(tip.IPTransmitter)
    tx.device_id, tx.server_url = "watercam-005", url
    tx.timeout_s, tx.retry_attempts, tx.retry_backoff_s = 5, 1, 0
    tx.api_key = ""
    tx._session = MagicMock()
    return tx


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.text = json.dumps(body or {})
    r.headers = {}
    return r


ACKS = [{"id": 41, "result": 0}, {"id": 42, "result": 2}]


class TestIPTransmitterAcks:
    def test_post_acks_2xx(self):
        tx = _tx()
        tx._session.post.return_value = _resp(204)
        assert tx.post_acks(ACKS) is True
        url = tx._session.post.call_args.args[0]
        assert url == f"{TRUSTED}/ip/ack"
        assert tx._session.post.call_args.kwargs["json"] == {"device_id": "watercam-005", "acks": ACKS}
        assert command_log.pending_acks() == []

    def test_post_acks_nothing_to_send(self):
        tx = _tx()
        assert tx.post_acks([]) is True
        tx._session.post.assert_not_called()

    @pytest.mark.parametrize("failure", ["status", "exception"])
    def test_failed_acks_are_kept_for_the_next_uplink(self, failure):
        import requests
        tx = _tx()
        if failure == "status":
            tx._session.post.return_value = _resp(500)
        else:
            tx._session.post.side_effect = requests.exceptions.ConnectionError("down")
        assert tx.post_acks(ACKS) is False
        assert command_log.pending_acks() == ACKS

    def test_untrusted_server_gets_no_acks_but_they_are_kept(self):
        tx = _tx("http://192.168.1.20:8000")
        assert tx.post_acks(ACKS) is False
        tx._session.post.assert_not_called()
        assert command_log.pending_acks() == ACKS

    def test_send_uplink_carries_pending_acks_and_clears_them_after_success(self):
        command_log.add_pending_acks(ACKS)
        tx = _tx()
        tx._session.post.return_value = _resp(200, {"decoded": {}})
        out = tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1)
        assert out["success"]
        assert tx._session.post.call_args.kwargs["json"]["acks"] == ACKS
        assert command_log.pending_acks() == []

    def test_send_uplink_keeps_pending_acks_when_it_fails(self):
        command_log.add_pending_acks(ACKS)
        tx = _tx()
        tx._session.post.return_value = _resp(500)
        with patch("tools.transmit_ip.time.sleep"):
            out = tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1)
        assert not out["success"]
        assert command_log.pending_acks() == ACKS

    def test_send_uplink_without_pending_acks_has_no_acks_key(self):
        tx = _tx()
        tx._session.post.return_value = _resp(200, {})
        tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1)
        assert "acks" not in tx._session.post.call_args.kwargs["json"]

    def test_untrusted_uplink_sends_no_acks(self):
        command_log.add_pending_acks(ACKS)
        tx = _tx("http://192.168.1.20:8000")
        tx._session.post.return_value = _resp(200, {})
        tx.send_uplink([{"code": "02 01", "payload_hex": "0000002c"}], device_ts=1)
        assert "acks" not in tx._session.post.call_args.kwargs["json"]
        assert command_log.pending_acks() == ACKS


class TestPollDownlink:
    def test_returns_the_command_list(self):
        tx = _tx()
        cmds = [{"id": 41, "frame": "07109032"}, {"id": 42, "frame": "08139305"}]
        tx._session.get.return_value = _resp(200, {"commands": cmds})
        out = tx.poll_downlink()
        assert out["success"] and out["commands"] == cmds
        assert tx._session.get.call_args.args[0] == f"{TRUSTED}/ip/downlink/watercam-005"

    def test_404_is_nothing_pending(self):
        tx = _tx()
        tx._session.get.return_value = _resp(404)
        out = tx.poll_downlink()
        assert out["success"] and out["commands"] == []

    def test_untrusted_is_not_polled(self):
        tx = _tx("http://192.168.1.20:8000")
        out = tx.poll_downlink()
        assert out["success"] and out["commands"] == [] and out["untrusted"]
        tx._session.get.assert_not_called()

    def test_error_status_and_bad_body(self):
        tx = _tx()
        tx._session.get.return_value = _resp(500)
        assert tx.poll_downlink()["success"] is False
        tx._session.get.return_value = _resp(200, {"commands": "nope"})
        assert tx.poll_downlink()["commands"] == []


# ── the LoRa handler ─────────────────────────────────────────────────────────

@pytest.fixture
def handler():
    h = LoRaHandler.__new__(LoRaHandler)
    h.config, h.config_file, h.runtime_callback = {}, os.path.join(tempfile.mkdtemp(), "c.json"), None
    with patch.object(LoRaHandler, "_reply_with_status") as reply:
        h.reply = reply
        yield h


class TestLoRaFrames:
    def test_frame_is_applied_and_acked_with_seq_and_result(self, handler):
        handler.decode("07109032")
        assert handler.config["area_threshold"] == 50
        handler.reply.assert_called_once_with(ack=(7, 0))

    def test_repeated_frame_is_acked_again_but_not_reapplied(self, handler):
        handler.decode("07219106")
        assert handler.config["emergency_mode"] is True
        handler.config["emergency_since"] = "marker"        # a re-apply would restart the timer
        handler.decode("07219106")
        assert handler.config["emergency_since"] == "marker"
        assert [c.kwargs["ack"] for c in handler.reply.call_args_list] == [(7, 0), (7, 0)]

    def test_same_seq_with_different_blocks_is_a_new_command(self, handler):
        handler.decode("07109032")
        handler.decode("07109033")
        assert handler.config["area_threshold"] == 51

    def test_seq_zero_is_reapplied_every_time(self, handler):
        handler.decode("00219106")
        handler.config["emergency_since"] = "marker"
        handler.decode("00219106")
        assert handler.config["emergency_since"] != "marker"
        assert [c.kwargs["ack"] for c in handler.reply.call_args_list] == [(0, 0), (0, 0)]

    def test_emergency_on_then_off(self, handler):
        handler.decode("01219118")                           # 0x18 = 24 h
        assert handler.config["emergency_mode"] is True and handler.config["emergency_max_hours"] == 24
        handler.decode("029999")
        assert handler.config["emergency_mode"] is False
        assert [c.kwargs["ack"] for c in handler.reply.call_args_list] == [(1, 0), (2, 0)]

    def test_remote_debug_and_status_request(self, handler):
        with patch.object(LoRaHandler, "_start_remote_debug") as rd:
            handler.decode("03189803")
        rd.assert_called_once_with("03", "18 98")
        handler.reply.assert_called_once_with(ack=(3, 0))
        handler.reply.reset_mock()
        handler.decode("045001")
        handler.reply.assert_called_once_with(ack=(4, 0))

    def test_invalid_frame_is_acked_as_rejected(self, handler):
        handler.decode("0affffff")
        handler.reply.assert_called_once_with(ack=(10, 2))

    def test_bare_remote_start_byte_is_not_a_command(self, handler):
        handler.decode("21")
        handler.reply.assert_not_called()
        assert "emergency_mode" not in handler.config

    def test_ack_leads_the_status_reply_sent_on_air(self):
        h = LoRaHandler.__new__(LoRaHandler)
        h.config, h.config_file, h.runtime_callback = {}, os.path.join(tempfile.mkdtemp(), "c.json"), None
        h.current_size_limit = 242
        sent = []
        with patch.object(LoRaHandler, "_send_debug_reply", lambda self, data: sent.append(data)), \
                patch.object(dbg, "collect_compact_status",
                             side_effect=lambda em=None, ack=None: [("ak", ack[0]), ("dbg", 1)]):
            h._reply_with_status(ack=(9, 0))
            import time
            for _ in range(50):
                if sent:
                    break
                time.sleep(0.05)
        assert sent and json.loads(bytes.fromhex(sent[0])) == {"ak": 9, "dbg": 1}


# ── the status reply ─────────────────────────────────────────────────────────

class TestStatusAck:
    def test_ack_comes_first_and_ar_only_when_not_zero(self):
        keys = [k for k, _ in dbg.collect_compact_status(False, ack=(123, 0))]
        assert keys[:2] == ["ak", "dbg"] and "ar" not in keys
        fields = dbg.collect_compact_status(False, ack=(123, 2))
        assert [k for k, _ in fields][:3] == ["ak", "ar", "dbg"]
        assert fields[:2] == [("ak", 123), ("ar", 2)]

    def test_no_ack_no_ak(self):
        keys = [k for k, _ in dbg.collect_compact_status(False)]
        assert "ak" not in keys and "ar" not in keys and keys[0] == "dbg"

    def test_ack_survives_dr0(self):
        payload, dropped = dbg.encode_for_limit(dbg.collect_compact_status(False, ack=(123, 0)), 11)
        assert payload == b'{"ak":123}' and len(payload) == 10 and "dbg" in dropped
        payload, _ = dbg.encode_for_limit([("ak", 5), ("ar", 2), ("dbg", 1)], 11)
        assert payload == b'{"ak":5}'          # {"ak":5,"ar":2} is 15 B: the result is the first to go

    def test_handle_debug_status_request_passes_the_ack(self):
        r = dbg.handle_debug_status_request(size_limit=11, emergency_mode=False, ack=(200, 0))
        assert r["status"] == "success"
        assert json.loads(bytes.fromhex(r["data"])) == {"ak": 200}
