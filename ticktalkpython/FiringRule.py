# Copyright 2021 Carnegie Mellon University
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from enum import Enum

from .Clock import TTClock, TTClockSpec

from . import DebugLogger

logger = DebugLogger.get_logger('FiringRule')


class TTFiringRuleType(Enum):
    '''
    An enumeration for the base type of a firing rule. There is a more
    specification for firing rules in ``SQSync.TTFiringRule``
    '''
    Strict = 0
    Timed = 1
    TimedRetrigger = 2
    SequentialRetrigger = 3

    # experimental
    Immediate = 4
    Deadline = 5


class TTFiringRule():
    '''
    Full description of a firing rule

    :param rule_type: The type of the firing rule

    :type rule_type: TTFiringRuleType

    :param pattern: The input/ouput pattern the SQ follows

    :type pattern: TTSQPattern

    :param firing_rule_args: A set of arguments for configuring the firing rule,
        in a dictionary. The members depend on the rule_type

    :type firing_rule_args: dict

    :param is_sequential: Boolean indicating if this SQ needs to be on
        sequential/chronological inputs. Defaults to False

    :type is_sequential: bool

    :param use_deadline: Boolean indicating if this SQ uses a deadline or not.
        Exact specifications should be included within the firing_rule_args
        dictionary. Defaults to False

    :type use_deadline: bool
    '''
    def __init__(self,
                 rule_type,
                 firing_rule_args=None,
                 pattern=None,
                 is_sequential=False,
                 use_deadline=False):
        assert isinstance(
            rule_type, TTFiringRuleType
        ), f'firing rule type must be of type {TTFiringRuleType}'
        self.rule_type = rule_type
        self.is_sequential = is_sequential
        self.use_deadline = use_deadline
        self.pattern = pattern
        self.firing_rule_args = firing_rule_args
        self.clock = None

        if rule_type == TTFiringRuleType.TimedRetrigger:
            self.set_timed_retriggering(firing_rule_args)

        if rule_type == TTFiringRuleType.Deadline:
            self.set_deadline_triggering(firing_rule_args)

    def set_timed_retriggering(self, firing_rule_args_dict):
        # self.clock = TTClockSpec('root', None, 1, 0) #by default, the root
        # clock will be used
        self.clock = None
        self.period = 1
        self.phase = 0
        # TODO: need to fix why the names don't match up with TT kwargs??
        self.first_delay = 0

        for key in firing_rule_args_dict.keys():
            if key == 'streaming_clock':
                # this should be a TTClockSpec
                self.clock = firing_rule_args_dict[key]
            elif key == 'streaming_period':
                # this is in terms of the attached clock; else we assume it's the
                # root clock
                self.period = firing_rule_args_dict[key]
            elif key == 'streaming_phase':
                # this should be within [0, period)
                self.phase = firing_rule_args_dict[key]
            elif key == 'TTFirstInstanceDelay':
                self.first_delay = firing_rule_args_dict[key]


        if self.clock is None:
            # must be a better way to access this. It should be replaced at
            # runtime with a clock hosted by the ensemble (or its processes)
            self.clock = TTClock.root()

        assert self.phase % self.period == self.phase, f'phase must be within [0, period={self.period}'

        # store these w.r.t. the root domain to make delays and such easier to calculate
        self.period = self.period * self.clock.root_ticks_per_tick()
        # how does phase get modulated?
        self.phase = self.phase * self.clock.root_ticks_per_tick()

        if not self.clock.is_root():
            logger.error(
                'Please only use the root clock for stream generation; tracing to root'
            )
            clk = self.clock
            while not clk.is_root():
                clk = clk.parent()
            self.clock = clk

        self.clockspec = TTClockSpec.from_clock(self.clock)

    def set_deadline_triggering(self, firing_rule_args_dict):
        self.clock = None
        for key in firing_rule_args_dict.keys():
            if key == 'streaming_clock':
                # this should be a TTClockSpec
                self.clock = firing_rule_args_dict[key]

        if self.clock is None:
            self.clock = TTClock.root()

        self.clockspec = TTClockSpec.from_clock(self.clock)

    def update_clock(self, clocks):
        '''
        Update the clock being used for this firing rule to match the runtime
        environment, mainly for reading the current time when determining the
        next stream sampling time

        :param clocks: A list of clocks provided by the runtime to choose from

        :type clocks: list(TTClock)
        '''
        if self.rule_type != TTFiringRuleType.TimedRetrigger:
            # are there other cases that require use of a clock?
            return

        updated_clock = False
        # search the clocks until we find one with an equivalent specification
        # (TTClockSpec)
        for clk in clocks:
            if TTClockSpec.from_clock(clk) == TTClockSpec.from_clock(
                    self.clock):
                logger.debug('Replaced clock %s with %s in sync sq',
                             self.clock, clk)
                self.clock = clk
                updated_clock = True
                logger.debug('current time on this clock is %d', clk.now())

        if updated_clock is None:
            logger.warning('Could not find a clock to update')
            logger.warning(clocks)
            logger.warning(self.clock)
