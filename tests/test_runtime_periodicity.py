"""Changing a TimedRetrigger SQ's period and phase at runtime.

Path: TTRuntimeManager.update_periodicity() -> RuntimeMsg.UpdatePeriodicity to
the runtime-manager process -> SyncMsg.UpdatePeriodicity forwarded over the
network to the SQ's ensemble -> TTInputTokenProcess -> TTSQSync ->
TTFiringRule.set_period_phase(). The next trigger time is computed from the
firing rule each time, so the change applies from the next iteration.
"""
import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ticktalkpython.FiringRule import TTFiringRule, TTFiringRuleType
from ticktalkpython.InputTokenProcess import TTInputTokenProcess
from ticktalkpython.IPC import Message, NetMsg, Recipient, RuntimeMsg, SyncMsg
from ticktalkpython.RuntimeManager import TTRuntimeManager, TTRuntimeManagerProcess
from ticktalkpython.SQSync import TTSQSync


def _timed_rule(period=10, phase=0):
    return TTFiringRule(TTFiringRuleType.TimedRetrigger,
                        {"streaming_period": period, "streaming_phase": phase})


# ── firing rule ─────────────────────────────────────────────────────────────

def test_set_period_phase_updates_and_wraps_phase_into_the_period():
    rule = _timed_rule(10, 0)
    rule.set_period_phase(5, 12)
    assert (rule.period, rule.phase) == (5, 2)


def test_set_period_phase_can_change_one_value_only():
    rule = _timed_rule(10, 3)
    rule.set_period_phase(period=20)
    assert (rule.period, rule.phase) == (20, 3)
    rule.set_period_phase(phase=7)
    assert (rule.period, rule.phase) == (20, 7)


def test_set_period_phase_rejects_a_non_positive_period():
    with pytest.raises(AssertionError):
        _timed_rule().set_period_phase(0, 0)


def test_set_period_phase_ignores_other_rule_types():
    rule = TTFiringRule(TTFiringRuleType.Immediate)
    rule.set_period_phase(5, 1)
    assert not hasattr(rule, "period")


# ── SQ synchronisation: the next trigger uses the new period ────────────────

def test_next_trigger_time_follows_the_updated_period():
    sync = object.__new__(TTSQSync)
    sync.firing_rule = _timed_rule(10, 0)
    assert sync.calculate_next_trigger_time(start_from=25) == 30
    sync.update_periodicity(7, 3)
    assert sync.calculate_next_trigger_time(start_from=25) == 31


# ── input-token process ─────────────────────────────────────────────────────

def _input_token_process(sqs):
    proc = object.__new__(TTInputTokenProcess)
    proc.sqs = sqs
    proc.logger = logging.getLogger("test-input-tokens")
    return proc


def test_input_token_process_applies_the_update_to_the_named_sq():
    sq = MagicMock()
    proc = _input_token_process({"get_time-3": sq})
    proc.handle_message(Message(SyncMsg.UpdatePeriodicity, ("get_time-3", 120, 0),
                                Recipient.ProcessInputTokens))
    sq.update_periodicity.assert_called_once_with(120, 0)


def test_input_token_process_ignores_an_unknown_sq():
    sq = MagicMock()
    proc = _input_token_process({"get_time-3": sq})
    proc.handle_message(Message(SyncMsg.UpdatePeriodicity, ("nope", 120, 0),
                                Recipient.ProcessInputTokens))
    sq.update_periodicity.assert_not_called()


# ── runtime manager ─────────────────────────────────────────────────────────

def test_public_api_sends_the_runtime_message():
    rtm = SimpleNamespace(sent=[])
    rtm.send_to_runtime = rtm.sent.append
    TTRuntimeManager.update_periodicity(rtm, "ttmain", "get_time-3", 120, 0)
    (msg,) = rtm.sent
    assert msg.msg_type == RuntimeMsg.UpdatePeriodicity
    assert msg.payload == ("ttmain", "get_time-3", 120, 0)
    assert msg.process_recipient == Recipient.ProcessRuntimeManager


def _runtime_process(instantiated):
    proc = object.__new__(TTRuntimeManagerProcess)
    proc.instantiated_graphs = instantiated
    proc.logger = logging.getLogger("test-rtm")
    proc.forwarded = []
    proc.input_network_func = proc.forwarded.append
    return proc


def test_runtime_process_forwards_the_update_to_the_sqs_ensemble():
    # The first version of this handler was spliced into the middle of the
    # ExecuteGraphOnInputs handler, so it ran graph-start code here (and hit a
    # NameError on input_dict) while graph start lost its body.
    proc = _runtime_process({"ttmain": (None, {"get_time-3": "ensemble-pi"})})
    proc.handle_message(Message(RuntimeMsg.UpdatePeriodicity,
                                ("ttmain", "get_time-3", 120, 0),
                                Recipient.ProcessRuntimeManager))
    (net,) = proc.forwarded
    assert net.msg_type == NetMsg.ForwardNetworkMessage
    ensemble, inner = net.payload
    assert ensemble == "ensemble-pi"
    assert inner.msg_type == SyncMsg.UpdatePeriodicity
    assert inner.payload == ("get_time-3", 120, 0)
    assert inner.process_recipient == Recipient.ProcessInputTokens


@pytest.mark.parametrize("graph, sq", [("other", "get_time-3"), ("ttmain", "nope")])
def test_runtime_process_drops_updates_for_unknown_graphs_or_sqs(graph, sq):
    proc = _runtime_process({"ttmain": (None, {"get_time-3": "ensemble-pi"})})
    proc.handle_message(Message(RuntimeMsg.UpdatePeriodicity, (graph, sq, 120, 0),
                                Recipient.ProcessRuntimeManager))
    assert proc.forwarded == []
