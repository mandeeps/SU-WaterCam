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
