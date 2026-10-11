"""Status replies fit the mDot's payload limit, keeping the fields the server
confirms commands with (rd for remote debug, em for emergency mode)."""
import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import remote_debug as rd  # noqa: E402
from tools.lora_debug_integration import encode_for_limit, handle_debug_status_request  # noqa: E402

STATE = {"active": True, "until": time.time() + 30 * 60, "tailscale": True, "cellular": True}


def _sent(handler):
    return json.loads(bytes.fromhex(handler.transmit.call_args.args[0]))


@pytest.mark.parametrize("limit, keys", [(242, {"dbg", "rd", "tn", "cl", "ts"}),
                                         (53, {"dbg", "rd", "tn", "cl", "ts"}),   # SF9
                                         (24, {"dbg", "rd", "tn"}),
                                         (17, {"dbg", "rd"})])
def test_remote_debug_status_is_trimmed(limit, keys):
    h = MagicMock(current_size_limit=limit)
    assert rd.send_status(STATE, handler=h)
    msg = _sent(h)
    assert set(msg) == keys and len(json.dumps(msg, separators=(",", ":"))) <= limit
    assert msg["rd"] in (29, 30)


def test_no_status_when_nothing_useful_fits():
    h = MagicMock(current_size_limit=11)            # SF10
    assert not rd.send_status(STATE, handler=h)
    h.transmit.assert_not_called()


def test_limit_comes_from_the_daemon_for_an_rpc_client():
    h = MagicMock(spec=["transmit", "get_size_limit"])
    h.get_size_limit.return_value = 17
    rd.send_status(STATE, handler=h)
    assert set(_sent(h)) == {"dbg", "rd"}


def test_emergency_state_survives_a_small_limit():
    reply = handle_debug_status_request(size_limit=16, emergency_mode=True)
    assert json.loads(bytes.fromhex(reply["data"])) == {"dbg": 1, "em": 1}


def test_encode_for_limit_keeps_leading_fields():
    payload, dropped = encode_for_limit([("dbg", 1), ("rd", 5), ("ts", 1791675183)], 17)
    assert payload == b'{"dbg":1,"rd":5}' and dropped == ["ts"]
