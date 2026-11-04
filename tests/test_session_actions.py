"""Session actions (command 1A 9A): dispatch, IP poll, de-dup, results (tools/remote_debug.py)."""
import json
import os
import sys
import time
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import remote_debug as rd  # noqa: E402
from tools import transmit_ip  # noqa: E402


@pytest.fixture
def node(tmp_path, monkeypatch):
    monkeypatch.setattr(rd, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rd, "REPO", str(tmp_path))
    monkeypatch.setattr(rd, "STATE_FILE", str(tmp_path / "remote_debug.json"))
    monkeypatch.setattr(rd, "REQUEST_FILE", str(tmp_path / "remote_debug_request.json"))
    monkeypatch.setattr(rd, "LOCK_FILE", str(tmp_path / "remote_debug.lock"))
    monkeypatch.setattr(rd, "_boot_id", lambda: "boot-A")
    monkeypatch.setattr(rd, "_run", lambda cmd, timeout=30: (0 if "ticktalk.service" not in cmd else 3, ""))
    monkeypatch.setattr(rd, "_version_tag", lambda: "2026.10.7")
    monkeypatch.setattr(rd, "_last_capture_age_min", lambda: 37)
    monkeypatch.setattr(rd, "_keep_bench_wifi_on", lambda: None)
    handler = MagicMock(current_size_limit=242)
    monkeypatch.setattr("tools.lora_handler_concurrent.get_lora_handler", lambda: handler)
    return handler


def session(**kw):
    state = {"active": True, "started": time.time(), "until": time.time() + 1800, "boot_id": "boot-A",
             "tailscale": True, "wifi": True, "cellular": False, "mode": "wifi", "connection": "Quectel"}
    state.update(kw)
    rd._write_json(rd.STATE_FILE, state)
    return state


def sent(handler, n=-1):
    return json.loads(bytes.fromhex(handler.transmit.call_args_list[n].args[0]))


def frame(seq, action_id, action):
    return bytes([seq, 0x1A, 0x9A, action_id, action])


# --- dispatch ---------------------------------------------------------------------------

def test_code_is_in_the_frame_table_and_parses():
    assert transmit_ip.parse_command_frame(frame(5, 12, 1)) == (5, [{"code": "1A 9A", "payload_hex": "0c01"}])


def test_lora_frame_is_spooled_not_run(node):
    """lora_daemon only queues the action (NoNewPrivileges): nothing is sent from it."""
    from tools.lora_handler_concurrent import LoRaHandler
    h = LoRaHandler.__new__(LoRaHandler)
    h._reply_with_status = MagicMock()
    h._apply_server_frame(frame(0, 12, 1))
    spooled = os.listdir(rd._actions_dir())
    assert len(spooled) == 1
    assert json.load(open(os.path.join(rd._actions_dir(), spooled[0])))["action"] == 1
    node.transmit.assert_not_called()


def test_spooled_status_check_runs_and_replies(node):
    session()
    rd.request_action(12, 1, "frame")
    assert rd.run_spooled_actions() == 1
    msg = sent(node)
    assert msg["ai"] == 12 and msg["ac"] == 1 and msg["ok"] == 1
    assert msg["sv"] == 14        # lora_daemon, segformer_daemon, remote-debug up; ticktalk stopped
    assert (msg["vr"], msg["ca"], msg["tn"], msg["wf"], msg["cl"]) == ("2026.10.7", 37, 1, 1, 0)
    assert os.listdir(rd._actions_dir()) == []


def test_zero_id_is_rejected_by_the_decoder(node):
    r = transmit_ip.apply_downlink_command({"parts": [{"code": "1A 9A", "payload_hex": "0001"}]}, MagicMock())
    assert r["skipped"] == ["1A 9A"] and not os.path.isdir(rd._actions_dir())


# --- IP poll ------------------------------------------------------------------------------

def test_tick_polls_ip_and_runs_the_action(node, monkeypatch):
    session()
    polled = {"success": True, "commands": [{"id": 77, "frame": frame(9, 21, 1).hex()}]}
    monkeypatch.setattr(transmit_ip, "poll_downlink", lambda *a, **k: polled)
    acks = []
    real = transmit_ip.handle_downlink_reply
    monkeypatch.setattr(transmit_ip, "handle_downlink_reply",
                        lambda poll, **kw: real(poll, set_param_fn=MagicMock(), ack_fn=acks.append, **kw))
    monkeypatch.setattr("tools.command_log.APPLIED_FILE", rd.DATA_DIR + "/applied.json")
    rd.tick()
    assert acks == [[{"id": 77, "result": 0}]]
    assert sent(node)["ai"] == 21 and sent(node)["ok"] == 1


def test_tick_does_not_poll_while_tailscale_is_down(node, monkeypatch):
    session(tailscale=False, last_try=time.time())
    poll = MagicMock()
    monkeypatch.setattr(transmit_ip, "poll_downlink", poll)
    rd.tick()
    poll.assert_not_called()


def test_a_failed_poll_does_not_break_the_tick(node, monkeypatch):
    session()
    monkeypatch.setattr(transmit_ip, "poll_downlink", MagicMock(side_effect=OSError("down")))
    rd.tick()


def test_same_frame_over_lora_then_ip_runs_once(node, monkeypatch):
    """command_log remembers the frame: the IP poll only acks it again."""
    session()
    monkeypatch.setattr("tools.command_log.APPLIED_FILE", rd.DATA_DIR + "/applied.json")
    f = frame(9, 21, 1)
    transmit_ip.apply_command_frame(f, lambda parts: transmit_ip.apply_downlink_command({"parts": parts}, MagicMock()))
    monkeypatch.setattr(transmit_ip, "poll_downlink",
                        lambda *a, **k: {"success": True, "commands": [{"id": 1, "frame": f.hex()}]})
    monkeypatch.setattr(transmit_ip, "post_acks", lambda acks, **k: True)
    rd.tick()
    assert node.transmit.call_count == 1


# --- de-dup -----------------------------------------------------------------------------------

def test_same_action_id_from_two_frames_runs_once(node):
    session()
    assert rd.handle_action(30, 1, "lora")["ok"] == 1
    assert rd.handle_action(30, 1, "ip") is None          # different seq, same action id
    assert node.transmit.call_count == 1
    assert "30" in rd.load_state()["actions"]


def test_action_id_is_forgotten_after_the_window(node):
    session(actions={"30": time.time() - rd.ACTION_DEDUP_S - 1})
    assert rd.handle_action(30, 1, "lora")["ok"] == 1


# --- refusals and failures --------------------------------------------------------------------

def test_no_session_refuses_with_a_result(node):
    rd.request_action(8, 1, "frame")
    rd.tick()
    assert sent(node) == {"ai": 8, "ac": 1, "ok": 0, "er": rd.ER_NO_SESSION}


def test_unknown_action_is_ok_zero(node):
    session()
    rd.handle_action(40, 99)
    assert sent(node) == {"ai": 40, "ac": 99, "ok": 0, "er": rd.ER_UNKNOWN}


def test_a_failing_action_reports_failure(node, monkeypatch):
    session()
    monkeypatch.setitem(rd.ACTIONS, 1, MagicMock(side_effect=RuntimeError("x")))
    rd.handle_action(41, 1)
    assert sent(node) == {"ai": 41, "ac": 1, "ok": 0, "er": rd.ER_FAILED}


# --- size ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("limit, keys", [(242, {"ai", "ac", "ok", "sv", "vr", "ca", "tn", "wf", "cl"}),
                                         (53, {"ai", "ac", "ok", "sv", "vr"}),          # SF9
                                         (40, {"ai", "ac", "ok", "sv"}),
                                         (24, {"ai", "ac", "ok"})])
def test_result_fits_the_limit(node, limit, keys):
    session()
    node.current_size_limit = limit
    rd.handle_action(255, 1)
    payload = bytes.fromhex(node.transmit.call_args.args[0])
    assert len(payload) <= limit and set(json.loads(payload)) == keys


def test_full_status_result_is_small(node):
    session()
    rd.handle_action(255, 1)
    assert len(bytes.fromhex(node.transmit.call_args.args[0])) <= 78


def test_nothing_is_sent_when_even_the_ack_does_not_fit(node):
    session()
    node.current_size_limit = 11
    rd.handle_action(12, 1)
    node.transmit.assert_not_called()


def test_status_result_has_no_sim_data(node):
    session()
    rd.handle_action(12, 1)
    assert not {"sim", "ic", "cd", "mb"} & set(sent(node))


def test_path_unit_watches_the_action_spool():
    cfg = os.path.join(os.path.dirname(__file__), "..", "config", "remote-debug.path")
    assert "PathChanged=/home/pi/SU-WaterCam/data/remote_debug_actions" in open(cfg).read()


def test_spool_write_is_atomic_and_temp_files_are_skipped(node):
    session()
    rd.request_action(5, 1, "frame")
    names = os.listdir(rd._actions_dir())
    assert len(names) == 1 and not names[0].startswith(".") and not names[0].endswith(".tmp")
    # a half-written request (temp file) is never read or removed
    tmp = os.path.join(rd._actions_dir(), ".123_6.json.tmp")
    open(tmp, "w").write("{")
    assert rd.run_spooled_actions() == 1
    assert os.path.exists(tmp) and node.transmit.call_count == 1


def test_request_action_creates_the_spool_directory(node):
    assert not os.path.isdir(rd._actions_dir())
    rd.request_action(5, 1, "frame")
    assert os.path.isdir(rd._actions_dir())


def test_session_poll_clears_the_shutdown_alarm_after_a_schedule(node, monkeypatch):
    session()
    cleared = MagicMock()
    monkeypatch.setattr(rd, "suspend_shutdown_alarm", cleared)
    monkeypatch.setattr(transmit_ip, "poll_downlink", lambda *a, **k: {})
    monkeypatch.setattr(transmit_ip, "handle_downlink_reply", lambda *a, **k: {"applied_params": ["schedule=00"]})
    rd.poll_ip_commands()
    cleared.assert_called_once()
    monkeypatch.setattr(transmit_ip, "handle_downlink_reply", lambda *a, **k: {"applied_params": ["area=5"]})
    rd.poll_ip_commands()
    cleared.assert_called_once()
