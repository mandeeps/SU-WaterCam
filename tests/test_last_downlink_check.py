"""tools/last_downlink_check.py: the poll just before shutdown."""
import json

from tools import last_downlink_check as ldc


class FakeTx:
    def __init__(self, replies, enabled=True, reachable=True):
        self.replies, self.enabled, self.reachable, self.polls = list(replies), enabled, reachable, 0
        self.acked = []

    def is_reachable(self, timeout_s=None):
        return self.reachable

    def post_acks(self, acks):
        self.acked.append(acks)
        return True

    def poll_downlink(self):
        self.polls += 1
        return self.replies.pop(0) if self.replies else {"success": True, "commands": []}


def _cmd(frame_hex, cid=1):
    return {"success": True, "commands": [{"id": cid, "frame": frame_hex}]}


def _run(tx, params=None, tmp_path=None, monkeypatch=None, session=False):
    params = {} if params is None else params
    if monkeypatch is not None:
        state = tmp_path / "remote_debug.json"
        state.write_text(json.dumps({"active": session}))
        monkeypatch.setattr(ldc, "REMOTE_STATE", str(state))
    return ldc.check(set_param_fn=lambda k, v: params.__setitem__(k, v),
                     get_param_fn=lambda k, d=None: params.get(k, d), transmitter=tx), params


def test_disabled_and_unreachable_skip_without_polling(tmp_path, monkeypatch):
    tx = FakeTx([], enabled=False)
    out, _ = _run(tx, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert out["status"] == "disabled" and tx.polls == 0 and not out["keep_awake"]
    tx = FakeTx([], reachable=False)
    out, _ = _run(tx, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert out["status"] == "unreachable" and tx.polls == 0


def test_no_command(tmp_path, monkeypatch):
    tx = FakeTx([])
    out, _ = _run(tx, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert out["status"] == "no_command" and tx.polls == 1 and not out["keep_awake"]


def test_applies_queued_commands_up_to_the_cap(tmp_path, monkeypatch):
    tx = FakeTx([_cmd("07109014")] * 5)
    out, params = _run(tx, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert out["status"] == "applied" and tx.polls == ldc.MAX_COMMANDS
    assert params                       # something was set
    assert tx.acked == [[{"id": 1, "result": 0}]] * ldc.MAX_COMMANDS   # acked each time offered


def test_emergency_on_keeps_the_unit_awake(tmp_path, monkeypatch):
    tx = FakeTx([_cmd("08219102")])
    out, params = _run(tx, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert params.get("emergency_mode") is True
    assert out["keep_awake"] and "emergency" in out["why"]


def test_remote_session_keeps_the_unit_up(tmp_path, monkeypatch):
    out, _ = _run(FakeTx([]), tmp_path=tmp_path, monkeypatch=monkeypatch, session=True)
    assert out["keep_awake"] and "remote access" in out["why"]


def test_never_raises(tmp_path, monkeypatch):
    class Broken(FakeTx):
        def poll_downlink(self):
            raise RuntimeError("boom")
    out, _ = _run(Broken([]), tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert out["status"] == "error" and not out["keep_awake"]
