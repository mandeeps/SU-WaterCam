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

import time
import datetime

from . import DebugLogger
from .Constants import get_readable_time, TIME_OFFSET

logger = DebugLogger.get_logger('Clock')

TTClockRoot = None


def default_read_time():
    return int(time.time()*TIME_OFFSET)


def default_convert_to_readable_time(time):
    max_time = (2**32)*TIME_OFFSET
    if 0 < time:
        if time < max_time:
            return get_readable_time(time / (TIME_OFFSET))
        else:
            return "+infinity"
    return "-infinity"


class TTClock:
    '''
    Create a TTClock by specifying its name (for documentation's sake), parent,
    period and epoch

    :param name: a descriptor for this clock; used solely for documentation

    :type name: string

    :param parent_clock: This clock's parent in the clock tree

    :type parent_clock: TTClock

    :param period: the number of parent clock ticks corresponding to ONE tick of
        this clock

    :type period: int

    :param epoch: the lowest-numbered tick of the parent corresponding to *t=0*
        of this clock

    :type epoch: int
    '''

    def __init__(self, name, parent_clock, period, epoch):
        assert isinstance(period, int)
        assert isinstance(epoch, int)
        self.name = name
        self.period = period
        self.epoch = epoch
        global TTClockRoot
        if TTClockRoot is not None and parent_clock is None:
            raise Exception(
                "ClockError", "Do not attempt to redefine TTClock.root()")
        self.parent_clock = parent_clock
        # This is only meaningful for the root clock
        self.root_now = lambda: None
        self.time_to_str = lambda: None
        self.root_ticks_per_second = None

    @staticmethod
    def root():
        '''
        Return the self-defined root clock.

        Note that a root clock has two special instance variables: a function
        returning the current time (in ticks of the root clock), and a mapping
        between ticks and real-time (in seconds); these must be functions and
        integers, respectively. These can be accessed via root_clock.root_now()
        and root_clock.root_ticks_per_second

        :return: root clock

        :rtype: TTClock
        '''
        global TTClockRoot
        if TTClockRoot is None:
            root_clock = TTClock("ROOT", None, period=1, epoch=0)
            # def read_time():
            #     import time
            #     return int(time.time()*TIME_OFFSET)
            # read_time = lambda : int(time.time()*TIME_OFFSET) #default function to read time

            TTClock.__set_root__(root_clock)
            TTClock.__set_root_now__(
                now_func=default_read_time,
                time_to_str=default_convert_to_readable_time,
                ticks_per_second=1000000)
        return TTClockRoot

    @staticmethod
    def __set_root__(clock):
        '''
        Explicilty set the root clock; this is necessary when unpickling a graph
        because the initial global TTClockRoot will not be preserved within the
        global namespace.

        :param clock: The clock to set as the root clock

        :type clock: ``TTClock``
        '''
        assert clock.parent_clock is None, 'Invalid Root Clock provided; it has a parent %s' % clock.parent()
        global TTClockRoot
        TTClockRoot = clock

    @staticmethod
    def __set_root_now__(now_func=default_read_time,
                         time_to_str=default_convert_to_readable_time,
                         ticks_per_second=1000000,
                         root=None):
        # What in the world is this 'generally determined' thing?
        '''
        Set a callback function that, when read, will return the current time
        with respect to the root clock as an integer. This is generally
        determined to be in microseconds but may be changed otherwise by
        specifying how the ticks relate to seconds, which the hardware timers
        must know to set the proper timeouts.

        It is assumed that the phase of the root clock matches up so that a
        multiple of the ``ticks_per_second`` would also align with a second

        :param now_func: The function to call that returns the current time as
            an integer

        :type now_func: function

        :param ticks_per_second: The number of ticks in the root clock that
            corresponds to one second. By default, 1,000,000 ticks per second (1
            tick = 1 microsecond)

        :type ticks_per_second: int

        :param root: The root clock that the other parameters should be assigned
            to

        :type root: ``TTClock``
        '''
        if root is None:
            root = TTClock.root()
        assert root.parent() == root, 'Cannot set root-only parameters to a clock that is not a root'
        root.root_now = now_func
        root.root_ticks_per_second = ticks_per_second
        root.time_to_str = time_to_str

    def trace_to_root(self):
        '''
        Trace through the clock tree to the root clock

        :return: The root clock

        :rtype: TTClock
        '''
        if self.is_root():
            return self
        else:
            return self.parent().trace_to_root()

    def is_root(self):
        '''
        Is this clock the root?

        :return: ``True`` iff this is the root clock

        :rtype: bool
        '''
        return self.parent_clock is None
        # return self == TTClockRoot

    def __repr__(self):
        if self.is_root():
            return "<TTClock ROOT %s>" % (self.name,)
        else:
            return "<TTClock %s P:%s>" % (self.name, self.parent_clock)

    def __eq__(self, other):
        if not isinstance(other, TTClock):
            return NotImplemented
        else:
            return ((self.name == other.name)
            and (self.period == other.period)
            and (self.epoch == other.epoch)
            and (self.parent_clock == other.parent_clock))

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        return

    def parent(self):
        '''
        Return the parent of this clock (or itself if this clock is root)

        :return: the parent

        :rtype: TTClock
        '''
        if self.is_root():
            return self
        else:
            return self.parent_clock

    @staticmethod
    def common_ancestor(clock_a, clock_b):
        '''
        Walk the path toward the root clock from two different clocks until we
        find the common ancestor clock

        :param clock_a: one of the two clocks (this method is commutative)

        :type clock_a: TTClock

        :param clock_b: the other clock

        :type clock_b: TTClock

        :return: the common ancestor

        :rtype: TTClock
        '''
        # Easy case: they are the same clock
        if clock_a == clock_b:
            return clock_a
        elif clock_a == clock_b.parent():
            return clock_a
        elif clock_b == clock_a.parent():
            return clock_b
        else:
            return TTClock.common_ancestor(clock_a.parent(), clock_b.parent())

    def now(self):
        '''
        Report the current time as read from the root clock, translated into
        this clock's equivalent tick count

        :return: the current tick of this clock

        :rtype: int
        '''
        if self.is_root():
            return int(self.root_now())
        else:
            parent_time = self.parent_clock.now()
            return (parent_time - self.epoch) // self.period

    def json(self):
        '''
        Convert the Clock into a ``TTClockSpec`` and into a json
        '''
        parent_name = None
        if self.parent_clock:
            # parent is None if root, and will not have a name
            parent_name = self.parent_clock.name
        clock_spec = TTClockSpec(
            self.name, parent_name, self.period, self.epoch)

        return clock_spec.json()

    def root_ticks_per_tick(self):
        '''
        In terms of the root clock, return many ticks are there are for a tick
        of the child

        :return: The number of ticks in the root domain per tick of this clock

        :rtype: int
        '''
        if self == TTClock.root():
            return 1
        else:
            return self.parent().root_ticks_per_tick() * self.period

    def ticks_per_second(self):
        '''
        In terms of the real timeline, return how many ticks of this clock there
        are in a second

        :return: The number of ticks per second in this clock domain (truncated
            if there is a remainder)

        :rtype: int
        '''
        if self == TTClock.root():
            return self.root_ticks_per_second
        else:
            # perhaps this should actually return this as a float. No guarantee this is an integer
            return self.parent_clock.root_ticks_per_tick() // self.period


class TTClockSpec:
    '''
    TTClockSpec is a specification of a clock, and describes its relation to the
    parent clock without carrying a direct memory reference to said clock.

    In practice, this is often used to prevent the entire clock tree from being
    copied or serialized when ``TTTime`` and ``TTToken`` objects are shared
    between processes or ensembles. ``TTTimeSpec`` uses ``TTClockSpec`` in place
    of ``TTClock``

    :param name: A name used to refer to the clock, matching the ``TTClock``
        exactly

    :type name: string

    :param parent_clock: A name referring to a parent clock (rather than a
        direct reference as used in ``TTClock``)

    :type parent_clock: string

    :param period: The period of this clock specfication; ``period`` ticks of
        the parent clock equate to 1 tick of this

    :type period: int

    :param epoch: The offset of this clock with respect to the parent clock.
        This clock would be ``epoch`` ticks ahead of the parent clock, where
        ``epoch`` ticks are in the domain of the parent

    :type epoch: int
    '''

    def __init__(self,
                 name,
                 parent_clock=None,
                 period=1,
                 epoch=0,
                 time_to_str=default_convert_to_readable_time):
        self.name = name
        # provided as only a string in the compiler. The full clock object is not created.
        self.parent_clock = parent_clock
        self.period = period
        self.epoch = epoch
        self.time_to_str = time_to_str

    def json(self):
        '''
        Convert the ``TTClockSpec`` into an equivalent JSON representation for
        serialization
        '''
        j = {}
        j['name'] = self.name
        if self.parent_clock:
            j['parent_name'] = self.parent_clock
        j['period'] = self.period
        j['epoch'] = self.epoch
        return j

    @classmethod
    def from_clock(cls, clock):
        '''
        Create a ``TTClockSpec`` from a TTClock object. Useful for removing
        references to make Clocks easily serializable. `TTClock`` references are
        converted to clock names.

        :param clock: The clock to create a specification for

        :type clock: TTClock

        :return: Clock Specificaton matching the formulation of the clock itself

        :rtype: TTClockSpec
        '''
        if isinstance(clock, TTClockSpec):
            return clock
        if not isinstance(clock, TTClock):
            raise TypeError(
                'Can only construct a TTClockSpec from a TTClock; given %s' % clock)

        if clock.parent is None or clock.parent() == clock:
            return TTClockSpec(clock.name, None, clock.period, clock.epoch,
                               default_convert_to_readable_time)

        return TTClockSpec(clock.name, clock.parent_clock.name, clock.period,
                           clock.epoch, clock.time_to_str)

    def to_clock(self, available_clocks):
        '''
        Retrieve the reference to the correct clock from a set of the available
        ones. This is determined the TTClockSpec's recorded name and parent's
        name
        '''
        for clk in available_clocks:
            if clk.name == self.name:
                # check parent's name
                if self.parent_clock == clk.parent().name:
                    return clk
                elif self.parent_clock is None and clk.parent() == clk:
                    # the case for the root clock
                    return clk
        raise ValueError('Clock %s not found' % self)

    def is_root(self):
        '''
        Determine if this ``TTClockSpec`` is for a root clock, based on whether
        there is a parent clock defined or not

        :return: True if this ``TTClockSpec`` is a root clock; False otherwise

        :rtype: bool
        '''
        if self.parent_clock is None:
            return True
        return False

    def __repr__(self):
        return f'<TTClockSpec name:{self.name}, parent:{self.parent_clock}>'

    def __eq__(self, other):
        if not isinstance(other, TTClockSpec):
            return NotImplemented
        else:
            return (self.name == other.name
            and self.period == other.period
            and self.epoch == other.epoch
            and self.parent_clock == other.parent_clock)
