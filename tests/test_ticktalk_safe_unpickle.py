"""The runtime's UDP listener unpickles with an allowlist (SU-WaterCam #204)."""
import datetime
import os
import pickle

import numpy as np
import pytest

from ticktalkpython import SafeUnpickle
from ticktalkpython.IPC import Message, NetMsg, Recipient
from ticktalkpython.TTToken import TTToken


class _CallsGetcwd:
    """A payload that makes pickle call os.getcwd (stands in for anything worse)."""
    def __reduce__(self):
        return (os.getcwd, ())


def _token_message():
    token = TTToken({"path": "/home/pi/x.png", "pct": 27.3, "ok": True,
                     "when": datetime.datetime(2026, 10, 19, 12, 0),
                     "arr": np.arange(6, dtype=np.uint8).reshape(2, 3)}, None)
    return [Message(NetMsg.SendToken if hasattr(NetMsg, "SendToken") else list(NetMsg)[0],
                    {"token": token}, list(Recipient)[0])]


def test_runtime_messages_load_in_enforce_mode(monkeypatch):
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    out = SafeUnpickle.loads(pickle.dumps(_token_message()))
    value = out[0].payload["token"].value
    assert value["path"] == "/home/pi/x.png" and value["arr"].shape == (2, 3)


def test_enforce_refuses_a_payload_that_calls_a_function(monkeypatch):
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    with pytest.raises(pickle.UnpicklingError, match="blocked posix.getcwd|blocked os.getcwd"):
        SafeUnpickle.loads(pickle.dumps(_CallsGetcwd()))


def test_enforce_refuses_ticktalk_functions(monkeypatch):
    # a class from ticktalkpython is fine, a function there isn't
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    from ticktalkpython import DebugLogger

    class _CallsTT:
        def __reduce__(self):
            return (DebugLogger.get_logger, ("x",))
    with pytest.raises(pickle.UnpicklingError, match="blocked ticktalkpython.DebugLogger.get_logger"):
        SafeUnpickle.loads(pickle.dumps(_CallsTT()))


def test_warn_logs_once_and_loads(monkeypatch):
    # (TTPython's loggers don't propagate, so check the call rather than caplog)
    monkeypatch.setenv("TT_PICKLE_POLICY", "warn")
    SafeUnpickle._warned.clear()
    calls = []
    monkeypatch.setattr(SafeUnpickle.logger, "warning", lambda *a: calls.append(a))
    assert SafeUnpickle.loads(pickle.dumps(_CallsGetcwd())) == os.getcwd()
    SafeUnpickle.loads(pickle.dumps(_CallsGetcwd()))
    assert len(calls) == 1 and "allowlist" in calls[0][0]


def test_off_is_plain_pickle(monkeypatch):
    monkeypatch.setenv("TT_PICKLE_POLICY", "off")
    assert SafeUnpickle.loads(pickle.dumps(_CallsGetcwd())) == os.getcwd()


def test_default_is_warn(monkeypatch):
    monkeypatch.delenv("TT_PICKLE_POLICY", raising=False)
    assert SafeUnpickle.policy() == "warn"
    monkeypatch.setenv("TT_PICKLE_POLICY", "bogus")
    assert SafeUnpickle.policy() == "warn"


def test_listener_uses_it():
    src = open(os.path.join(os.path.dirname(__file__), "..", "ticktalkpython", "NetworkManager.py")).read()
    assert "SafeUnpickle.loads(payload)" in src and "pickle.loads(payload)" not in src.replace("SafeUnpickle.loads(payload)", "")


def test_what_a_real_wake_unpickled_is_allowed(monkeypatch):
    """The three types a warn-mode wake on 005 logged (2026-10-20) load in enforce mode."""
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    from intervaltree import IntervalTree
    from ticktalkpython import Clock
    tree = IntervalTree.from_tuples([(1, 5), (7, 9)])
    out = SafeUnpickle.loads(pickle.dumps({"tree": tree, "read": Clock.default_read_time,
                                           "fmt": Clock.default_convert_to_readable_time}))
    assert sorted(out["tree"]) == sorted(tree) and out["read"] is Clock.default_read_time


class _NM:
    """Just enough of a TTNetworkManager for handle_network_message."""
    def __init__(self):
        import logging
        self.logger = logging.getLogger("test.NM")
        self.handled = []

    def handle_IPC_message_from_network(self, msg):
        self.handled.append(msg)


def _handle(payload):
    from ticktalkpython.NetworkManager import TTNetworkManager
    from ticktalkpython.NetworkInterface import TTNetworkMessage
    nm = _NM()
    msg = TTNetworkMessage.__new__(TTNetworkMessage)
    msg.payload = payload
    TTNetworkManager.handle_network_message(nm, msg)
    return nm.handled


def test_a_refused_message_is_dropped_not_raised(monkeypatch, caplog):
    # Raised, the refusal would end the NetworkManager loop and the runtime with it
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    assert _handle(pickle.dumps(_CallsGetcwd())) == []
    assert "dropped" in caplog.text and "blocked" in caplog.text


@pytest.mark.parametrize("payload", [b"not a pickle", b"", pickle.dumps(42), pickle.dumps([1, 2])])
def test_junk_and_non_messages_are_dropped(monkeypatch, payload):
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    assert _handle(payload) == []


def test_runtime_messages_still_get_through(monkeypatch):
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    handled = _handle(pickle.dumps(_token_message()))
    assert len(handled) == 1 and handled[0].payload["token"].value["pct"] == 27.3


def _claims(op: bytes, arg: bytes) -> bytes:
    """A protocol-4 stream whose single opcode claims far more than it holds."""
    return b"\x80\x04" + op + arg + b"."


@pytest.mark.parametrize("stream", [
    # FRAME of 2**59 bytes: the C unpickler buffers it at that size
    b"\x80\x04\x95" + (1 << 59).to_bytes(8, "little") + b"K\x01.",
    # BYTEARRAY8 / BINBYTES8 of 2**40 bytes, holding 3
    _claims(b"\x96", (1 << 40).to_bytes(8, "little")) + b"abc",
    _claims(b"\x8e", (1 << 40).to_bytes(8, "little")) + b"abc",
    # LONG_BINPUT to memo slot 2**31: the memo table grows to that index
    b"\x80\x04K\x01r" + (1 << 31).to_bytes(4, "little") + b".",
])
def test_streams_claiming_huge_sizes_are_refused_without_allocating(monkeypatch, stream):
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    with pytest.raises(pickle.UnpicklingError, match="malformed stream"):
        SafeUnpickle.loads(stream)


def test_large_real_messages_still_load(monkeypatch):
    monkeypatch.setenv("TT_PICKLE_POLICY", "enforce")
    token = TTToken({"arr": np.zeros((480, 640), dtype=np.uint16)}, None)
    msg = [Message(list(NetMsg)[0], {"token": token}, list(Recipient)[0])] * 50
    out = SafeUnpickle.loads(pickle.dumps(msg, protocol=5))
    assert out[0].payload["token"].value["arr"].shape == (480, 640)
