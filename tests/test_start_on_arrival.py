"""TTStartOnArrival: the capture loop fires when its trigger arrives, not at the next minute."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ticktalkpython.FiringRule import TTFiringRule, TTFiringRuleType


def test_firing_rule_reads_the_keyword():
    on = TTFiringRule(TTFiringRuleType.TimedRetrigger,
                      {"streaming_period": 60, "TTStartOnArrival": True})
    off = TTFiringRule(TTFiringRuleType.TimedRetrigger, {"streaming_period": 60})
    assert on.start_on_arrival is True
    assert off.start_on_arrival is False


def test_compiled_capture_loop_starts_on_arrival():
    from runrtm import unpack_graph
    here = os.path.dirname(__file__)
    graph = unpack_graph(os.path.join(here, "..", "output", "ticktalk_main.pickle"))
    get_time = [sq for sq in graph.sqs if sq.sq_name.startswith("get_time")]
    assert len(get_time) == 1
    assert get_time[0].firing_rule.start_on_arrival is True


def test_phase_set_on_arrival_fires_almost_immediately():
    from ticktalkpython.SQSync import START_ON_ARRIVAL_LEAD_TICKS, TTSQSync
    rule = TTFiringRule(TTFiringRuleType.TimedRetrigger,
                        {"streaming_period": 60_000_000, "TTStartOnArrival": True})
    sync = object.__new__(TTSQSync)   # only calculate_next_trigger_time is used
    sync.firing_rule = rule
    now = 1_791_400_017_250_000   # 17.25 s past a minute
    rule.phase = (now + START_ON_ARRIVAL_LEAD_TICKS) % rule.period   # what the first match does
    assert sync.calculate_next_trigger_time(start_from=now) - now == START_ON_ARRIVAL_LEAD_TICKS
    nxt = sync.calculate_next_trigger_time(start_from=now + START_ON_ARRIVAL_LEAD_TICKS + 1)
    assert nxt - now == START_ON_ARRIVAL_LEAD_TICKS + 60_000_000
