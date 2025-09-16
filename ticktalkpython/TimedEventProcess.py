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

import threading
import time

# Optional import
try:
    import simpy
except ImportError:
    pass

from . import DebugLogger

logger = DebugLogger.get_logger('TimedEventProcess')


def delayed_apply_phy(duration, args, callback_func):
    '''
    A function to delay a physical process by a set amount, calling the
    callback on the provided value when the delay expires. The physical
    variant uses time.sleep (which takes a value in seconds) to wait.

    :param duration: The number of seconds to wait

    :type duration: float

    :param args: The arguments to apply to callback_func

    :type args: List[Any]

    :param callback_func: The function to call with a list of arguments
        (``args``)

    :type callback_func: function
    '''
    logger.debug('Wait %f seconds to apply %s to function', duration, args)
    time.sleep(duration)
    callback_func(*args)


def delayed_apply_sim(duration, args, callback_func, sim):
    '''
    A function to delay a simulated process by a set amount, calling the
    callback on the provided value when the delay expires. The simulated will
    yield this function (meaning it is a generator and must be called using
    sim.process, else the program hangs); the duration is in the number of
    simulation ticks to delay the process

    :param duration: The number of ticks to wait

    :type duration: float

    :param args: The arguments to apply to callback_func

    :type args: List[Any]

    :param callback_func: The function to call with a single argument
        (``args``)

    :type callback_func: function

    :param sim: The simpy.environment object

    :type sim: simpy.Environment
    '''
    logger.debug('Wait %f simulation ticks to apply %s to function', duration,
                 args)
    yield sim.timeout(duration)
    callback_func(*args)


def wait(duration, args, callback_func, sim=None, allow_late=True):
    '''
    Wait for a set duration for calling callback on a singular input

    :param duration: the amount of time to wait, specifically in terms of the
        root clock

    :type duration: ``float``

    :param args: The args to be applied to the callback function when the
        delay expires

    :type args: ``List[Any]``, but list components should be serializable
        (or pickleizable), depending on usage

    :param callback_func: The function to call with the list of args

    :type callback_fund: ``func``

    :param sim: Optional simulation environment in case of simulated runtime

    :type sim: ``simpy.Environment``

    :rtype: None
    '''
    if duration < 0:
        if allow_late:
            callback_func(*args)
        else:
            logger.warning("Disallowed late timed inputs, discarding token")
    else:
        if sim is not None and isinstance(sim, simpy.Environment):
            sim.process(delayed_apply_sim(duration, args, callback_func, sim))
        else:
            threading.Thread(target=delayed_apply_phy,
                             args=[duration, args, callback_func]).start()


def wait_until(clock,
               apply_time,
               args,
               callback_func,
               sim=None,
               allow_late=True):
    '''
    Wait for until some time to call a callback on a singular input

    :param clock: The reference clock for the apply time; this should be the
        ROOT clock at this time.

    :type clock: ``Clock.TTClock``

    :param apply_time: the time to apply the args, specifically in terms of
        the root clock

    :type apply_time: ``float``

    :param args: The list of arguments to be applied to the callback when the
        delay expires

    :type args: ``List[Any]``, but list components should be serializable
        (or pickleizable), depending on usage

    :param callback_func: The function to call with the list of args

    :type callback_fund: ``func``

    :param sim: Optional simulation environment in case of simulated runtime

    :type sim: ``simpy.Environment``

    :rtype: None
    '''
    assert clock.is_root(), 'Non-root waiting is not currently supported'
    current_time = clock.now()
    # assume the apply time is in the same timeline as the root clock
    duration = (apply_time - current_time) / clock.ticks_per_second()

    if duration > 0:
        wait(duration, args, callback_func, sim=sim, allow_late=allow_late)
    elif allow_late:
        logger.debug("Immediately execute as wait time already passed")
        callback_func(*args)
    else:
        logger.warning("Disallowed late timed inputs, discarding token")
