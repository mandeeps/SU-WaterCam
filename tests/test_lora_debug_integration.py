"""The remote debug-status reply: what goes in it, how it fits the payload
limit, and how LoRaHandler.decode() gets it on air.
"""
import json
import threading
from unittest import mock

import pytest

from tools import lora_debug_integration as dbg

FULL = [("dbg", 1), ("up", 57.1), ("ct", 48.2), ("th", 327680), ("em", 0),
        ("vi", 4.49), ("vo", 4.95), ("mp", 23), ("dp", 41), ("ld", 0.23), ("ts", 1791077046)]


def _decode(payload: bytes) -> dict:
    return json.loads(payload.decode("utf-8"))


# ── fitting the payload limit ───────────────────────────────────────────────

@pytest.mark.parametrize("limit", [242, 125, 53, 11])
def test_reply_fits_each_data_rate_and_starts_with_the_marker(limit):
    payload, _ = dbg.encode_for_limit(FULL, limit)
    assert 0 < len(payload) <= limit
    assert payload.startswith(b'{"dbg":1')
    assert _decode(payload)["dbg"] == 1


def test_full_reply_fits_dr2():
    payload, dropped = dbg.encode_for_limit(FULL, 125)
    assert dropped == [] and len(payload) <= 125


def test_fields_are_dropped_from_the_end_in_priority_order():
    payload, dropped = dbg.encode_for_limit(FULL, 53)
    kept = list(_decode(payload))
    assert kept == [k for k, _ in FULL][:len(kept)]          # a prefix, in order
    assert dropped == [k for k, _ in FULL][len(kept):]
    assert {"up", "ct", "th"} <= set(kept)                   # the essentials survive DR1


def test_limit_too_small_for_any_reply_is_an_error():
    with mock.patch.object(dbg, "collect_compact_status", return_value=FULL):
        r = dbg.handle_debug_status_request(size_limit=8)
    assert r["status"] == "error"


# ── collecting ──────────────────────────────────────────────────────────────

def test_unreadable_fields_are_left_out_not_faked():
    with mock.patch.object(dbg, "_throttled", return_value=None), \
         mock.patch.object(dbg, "_wittypi_volts", return_value=None):
        keys = [k for k, _ in dbg.collect_compact_status(emergency_mode=True)]
    assert "th" not in keys and "vi" not in keys and "vo" not in keys
    assert keys[0] == "dbg" and "em" in keys


def test_emergency_mode_is_0_or_1_and_omitted_when_unknown():
    assert dict(dbg.collect_compact_status(emergency_mode=True))["em"] == 1
    assert dict(dbg.collect_compact_status(emergency_mode=False))["em"] == 0
    assert "em" not in dict(dbg.collect_compact_status())


def test_needs_no_psutil():
    with mock.patch.dict("sys.modules", {"psutil": None}):
        r = dbg.handle_debug_status_request()
    assert r["status"] == "success"


# ── the transmit-ready response ─────────────────────────────────────────────

def test_success_payload_is_hex_of_the_json_reply():
    with mock.patch.object(dbg, "collect_compact_status", return_value=FULL):
        r = dbg.handle_debug_status_request(size_limit=242)
    assert r["status"] == "success" and r["dropped"] == []
    int(r["data"], 16)                                       # all hex digits
    assert _decode(bytes.fromhex(r["data"])) == dict(FULL)
    assert r["size_bytes"] == len(bytes.fromhex(r["data"]))


def test_process_debug_command_only_answers_type_50_command_01():
    with mock.patch.object(dbg, "handle_debug_status_request", return_value={"status": "success"}) as h:
        assert dbg.process_debug_command("50011") == {"status": "success"}
        assert dbg.process_debug_command("21001") is None
    h.assert_called_once()


# ── decode() sends it promptly, off the listener thread ─────────────────────

def test_decode_sends_the_reply_from_a_helper_thread():
    import tools.lora_handler_concurrent as lhc

    handler = lhc.LoRaHandler()          # serial is mocked by conftest
    handler.current_size_limit = 53      # e.g. US915 DR1
    sent = []
    done = threading.Event()

    def fake_transmit(content, max_retries=2):
        sent.append((content, threading.current_thread().name))
        done.set()
        return True

    with mock.patch.object(handler, "transmit", side_effect=fake_transmit), \
         mock.patch.object(handler, "queue_binary_transmit") as queued:
        handler.decode("5001")
        assert done.wait(5), "reply was never transmitted"

    content, thread_name = sent[0]
    payload = bytes.fromhex(content)
    assert len(payload) <= 53 and _decode(payload)["dbg"] == 1
    assert thread_name == "debug-reply"   # not the caller's (listener's) thread
    queued.assert_not_called()            # the undrained queue is no longer used
