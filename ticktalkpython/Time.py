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

import sys

from . import Clock
from . import DebugLogger

logger = DebugLogger.get_logger('Time')


class TTTime:
    '''
    Define an interval based on a clock.  Ticks must be integers, and the
    stop_tick must be greater than start_tick

    :param clock: the clock with which this time interval is associated

    :type clock: TTClock

    :param start_tick: the first tick of clock corresponding to the beginning of
        this interval

    :type start_tick: int

    :param stop_tick: the first tick of the clock following the interval; must
        be strictly greater than *start_tick*

    :type stop_tick: int
    '''
    # sys.maxsize causes issues in some OS's/machines like Raspberry Pi's.
    # Python will work with arbitrarily large integers (albeit more slowly)
    MAX_TIMESTAMP = max(sys.maxsize, (2**32) * 1000000)

    def __init__(self, clock, start_tick, stop_tick):
        assert isinstance(start_tick, int)
        assert isinstance(stop_tick, int)
        assert (start_tick <
                stop_tick), "stop_tick must be greater than start_tick"
        self.clock = clock
        self.start_tick = start_tick
        self.stop_tick = stop_tick

    def __repr__(self):
        return (f"<TTTime [{self.clock.time_to_str(self.start_tick)},"
                f"{self.clock.time_to_str(self.stop_tick)}] C:{self.clock}>")

    def __eq__(self, other):
        if isinstance(other, TTTime):
            return (self.clock == other.clock) and (
                self.start_tick == other.start_tick) and (self.stop_tick
                                                          == other.stop_tick)
        else:
            return NotImplemented

    def __hash__(self):
        # not hashing clocks
        return hash((self.start_tick, self.stop_tick))

    @staticmethod
    def infinite(clock):
        '''
        Return an 'infinite' time interval with reference to a clock.

        :param clock: the clock to use in the returned time

        :type clock: TTClock

        :return: infinite time interval

        :rtype: TTTime
        '''
        # sys.maxsize may vary per platform (size of address space), and
        # integers can be larger than sys.maxsize in Python3. For instance, a
        # Raspberry Pi 3 uses a 32-bit OS, so sys.maxsize is 2^31 -1. In 64 bit
        # ubuntu, it would be 2^63 - 1.
        return TTTime(clock, -TTTime.MAX_TIMESTAMP, TTTime.MAX_TIMESTAMP)

    def n_ticks(self):
        '''
        Return the length of the interval in ticks

        :return: interval length

        :rtype: int
        '''
        return self.stop_tick - self.start_tick

    def ancestor_time(self, ancestor):
        '''
        Translate this time into an ancestor's time: climb from this time's
        clock toward root, looking for a match, translating time as we go

        :param ancestor: a clock that is an ancestor to this clock, up to or
            including the root clock

        :type ancestor: TTClock
        '''
        if ancestor == self.clock:
            return self
        elif self.clock.is_root():
            # signal an error -- we reached root and root was not the desired
            # ancestor
            raise Exception('Ancestor', 'Given ancestor is invalid')
        else:
            # Compute a new TTTime: this time but with reference to the parent
            parent_start_tick = (self.start_tick *
                                 self.clock.period) + self.clock.epoch
            parent_stop_tick = (self.stop_tick *
                                self.clock.period) + self.clock.epoch
            parent_time = TTTime(self.clock.parent_clock, parent_start_tick,
                                 parent_stop_tick)
            return parent_time.ancestor_time(ancestor)

    @staticmethod
    def common_ancestor_overlap_time(time_a, time_b):
        '''
        Find the common ancestor clock for the clocks of each specified time and
        compute the intersection of the given times in terms of the ancestor, or
        *None* if the intersection is empty

        :param time_a: one of the times (this method is commutative)

        :type time_a: TTTime

        :param time_b: the other time

        :type time_b: TTTime

        :return: time (interval) representing the intersection

        :rtype: TTTime
        '''
        common_ancestor_clock = Clock.TTClock.common_ancestor(
            time_a.clock, time_b.clock)
        time_a_common = time_a.ancestor_time(common_ancestor_clock)
        time_b_common = time_b.ancestor_time(common_ancestor_clock)
        max_start = max(time_a_common.start_tick, time_b_common.start_tick)
        min_stop = min(time_a_common.stop_tick, time_b_common.stop_tick)
        if max_start < min_stop:
            return TTTime(common_ancestor_clock, max_start, min_stop)
        else:
            return None

    @staticmethod
    def common_ancestor_overlap_time_multi(*times):
        '''
        Find the common ancestor clock for the clocks of each specified time and
        compute the intersection of the given times in terms of the ancestor, or
        *None* if the intersection is empty.

        :param times: list of times

        :type times: TTTime

        :return: time (interval) representing the intersection

        :rtype: TTTime
        '''
        clocks = map(lambda x: x.clock, times)
        ancestor_clock = times[0].clock
        for clock in clocks:
            ancestor_clock = Clock.TTClock.common_ancestor(
                ancestor_clock, clock)
        # Find the maximum overlap among all the times, starting with infinity
        # on the ancestor clock and narrowing down
        time_overlap = TTTime.infinite(clock)
        for time in times:
            time_overlap = TTTime.common_ancestor_overlap_time(
                time_overlap, time)
            if time_overlap is None:
                return None
        return time_overlap

    def child_time(self, child):
        '''
        Lossy conversion into a child clock's domain. If the period relating
        the child and parent is not 1, then information will be lost as
        time-labels are constructed of integers, meaning that the integer
        division must truncate. The child doesn't not have to be the direct
        child of the clock used in the time label, but it must be a direct
        descendant.

        :param child: A direct descendant of the clock denoted in the 'clock'
            field of the token.

        :type child: ``TTClock``

        :return: The time in the child domain (potentially with loss of
            precision)

        :rtype: ``TTTime``
        '''
        if self.clock == child:
            return self

        ancestor = child.parent()
        time = self

        # head recursion
        if ancestor != self.clock:
            time = self.child_time(ancestor)

        child_start_tick = (time.start_tick - child.epoch) // child.period
        child_stop_tick = (time.stop_tick - child.epoch) // child.period
        # ensure no 0-width intervals, since that is considered illegal
        child_stop_tick = (child_stop_tick + 1 if child_start_tick
                           == child_stop_tick else child_stop_tick)

        return TTTime(child, child_start_tick, child_stop_tick)

    def calculate_center_timestamp(self):
        '''
        Calculate the center of the interval AS A FLOATING POINT. This is
        meant for contniuous signal processing, where we assume that the
        value on a token is 'collected' at the center of the interval, even
        if that is a floating point. This should not be used without knowing
        what you are doing.

        :return: the center timestamp of the interval

        :rtype: float
            (this is why the programmer must be careful, as we use int everywhere
            else)
        '''
        return (self.start_tick + self.stop_tick) / 2


class TTTimeSpec:
    '''
    A ``TTTimeSpec`` is a specification for the ``TTTime``, such that it does
    not contain any direct memory references that would be duplicated when
    serializing the object before it gets passed through a queue or network
    interface

    :param clockspec: the specification for the clock domain this time resides
        in

    :type clockspec: ``TTClockSpec``

    :param start_tick: The start tick of the interval (strictly less than the
        stop tick)

    :type start_tick: int

    :param stop_tick: the stop tick of the itnerval (strictly greater than the
        start tick)

    :type stop_tick: int

    :param ensemble_src_name: The name of the ensemble that generated this
        time interval, defaults to None

    :type ensemble_src_name: string | None
    '''
    def __init__(self,
                 clockspec,
                 start_tick: int,
                 stop_tick: int,
                 ensemble_src_name=None):
        if isinstance(clockspec, Clock.TTClock):
            self.clockspec = Clock.TTClockSpec.from_clock
        elif isinstance(clockspec, Clock.TTClockSpec):
            self.clockspec = clockspec
        else:
            raise TypeError(
                'clockspec should either be a TTClock or a TTClockSpec')

        self.start_tick = start_tick
        self.stop_tick = stop_tick
        self.ensemble_src_name = ensemble_src_name

    @classmethod
    def from_time(cls, time):
        return TTTimeSpec(Clock.TTClockSpec.from_clock(time.clock),
                          time.start_tick, time.stop_tick, None)

    @classmethod
    def infinite(cls, clockspec):
        return TTTimeSpec(clockspec, -TTTime.MAX_TIMESTAMP,
                          TTTime.MAX_TIMESTAMP)

    def to_time(self, clock=None, clock_list=None):
        '''
        Convert this timestamp into the equivalent ``TTTime``. If no clock is
        provided, it will attempt to find it from a clock list, but failing
        that, will raise a ValueError

        :param clock: The clock the ``TTTime`` should use. Defaults to None

        :type clock: ``TTClock``

        :param clock_list: A list of clocks to search for the clockspec in.
            Defaults to an empty list

        :type clock_list: list(``TTClock``)

        :return: the equivalent ``TTTime``

        :rtype: ``TTTime``
        '''
        c_list = [] if clock_list is None else clock_list
        if clock is None and 0 < len(c_list):
            clock = self.clockspec.to_clock(c_list)
        if clock is None:
            raise ValueError(
                "Couldn't determine clock for TTime from TTimeSpec", str(self))

        return TTTime(clock, self.start_tick, self.stop_tick)


    def __eq__(self, other):
        if isinstance(other, TTTimeSpec):
            return (self.clockspec == other.clockspec
                    and self.start_tick == other.start_tick
                    and self.stop_tick == other.stop_tick
                    and self.ensemble_src_name == other.ensemble_src_name)
        else:
            return NotImplemented

    def __repr__(self):
        string = (f'<TTTimeSpec c:{self.clockspec}, '
                  f'({self.clockspec.time_to_str(self.start_tick)},'
                  f'{self.clockspec.time_to_str(self.stop_tick)})')
        if self.ensemble_src_name:
            string += f'{self.ensemble_src_name}>'
        else:
            string += '>'
        return string
