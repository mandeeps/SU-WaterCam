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

from .SQ import SQify

# Base instructions represented as single-operation SQs


@SQify
def NEG(a):
    '''
    Arithmetic negation

    :param a: numerically-valued token
    :type a: TTToken

    :return: token whose value is the aritmetic negation of the input;
        ``TTTime`` copied from input
    :rtype: TTToken
    '''
    return -a


@SQify
def NOT(a):
    '''
    Logical negation

    :param a: comparable boolean value
    :type a: TTToken

    :return: token whose value is the boolean negation of the input;
        ``TTTime`` copied from input
    :rtype: TTToken
    '''
    return not a


@SQify
def ABS(a):
    '''
    Absolute value

    :param a: numerically-valued token
    :type a: TTToken

    :return: token whose value is the absolute value of the input; ``TTTime``
        copied from input
    :rtype: TTToken
    '''
    return abs(a)


@SQify
def ADD(a, b):
    '''
    Addition (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose value is the sum of the inputs; ``TTTime`` derived
        from inputs
    :rtype: TTToken
    '''
    return a + b


@SQify
def SUB(a, b):
    '''
    Subtraction (NOT commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose value is the difference of the inputs (a-b);
        ``TTTime`` derived from inputs
    :rtype: TTToken
    '''
    return a - b


@SQify
def MULT(a, b):
    '''
    Multiplication (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose value is the product of the inputs; ``TTTime``
        derived from inputs
    :rtype: TTToken
    '''
    return a * b


@SQify
def DIV(a, b):
    '''
    Division (NOT commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose value is the quotient of the inputs (a/b); ``TTTime``
        derived from inputs
    :rtype: TTToken
    '''
    return a / b


@SQify
def EQ(a, b):
    '''
    Equality (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose boolean value is if (a == b); ``TTTime`` derived from
        inputs
    :rtype: TTToken
    '''
    return a == b


@SQify
def NEQ(a, b):
    '''
    Inequality (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose boolean value is if (a != b); ``TTTime`` derived from
        inputs
    :rtype: TTToken
    '''
    return a != b


@SQify
def LT(a, b):
    '''
    Less Than (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose boolean value is if (a < b); ``TTTime`` derived from
        inputs
    :rtype: TTToken
    '''
    return a < b


@SQify
def LTE(a, b):
    '''
    Less Than Equal (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose boolean value is if (a <= b); ``TTTime`` derived from
        inputs
    :rtype: TTToken
    '''
    return a <= b


@SQify
def GT(a, b):
    '''
    Greater Than (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose boolean value is if (a > b); ``TTTime`` derived from
        inputs
    :rtype: TTToken
    '''
    return a > b


@SQify
def GTE(a, b):
    '''
    Greater Than Equal (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose boolean value is if (a >= b); ``TTTime`` derived from
        inputs
    :rtype: TTToken
    '''
    return a >= b


@SQify
def MIN(a, b):
    '''
    Minimum (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose value is the minimum of the inputs; ``TTTime``
        derived from inputs
    :rtype: TTToken
    '''
    return min(a, b)


@SQify
def MAX(a, b):
    '''
    Maximum (commutative)

    :param a: numerically-valued token
    :type a: TTToken

    :param b: numerically-valued token
    :type b: TTToken

    :return: token whose value is the maximum of the inputs; ``TTTime``
        derived from inputs
    :rtype: TTToken
    '''
    return max(a, b)


@SQify
def SQRT(a):
    '''
    Square root

    :param a: numerically-valued token
    :type a: TTToken

    :return: token whose value is the square root of the input; ``TTTime``
        copied from input
    :rtype: TTToken
    '''
    import math
    return math.sqrt(a)


@SQify
def TUPLE_2(a, b):
    '''Return a tuple of the two arguments (debug only)'''
    return (a, b)


@SQify
def TUPLE_3(a, b, c):
    '''Return a tuple of the two arguments (debug only)'''
    return (a, b, c)


@SQify
def CONST(trigger, const=None):
    '''
    Generate a ``TTToken`` with the given constant value.  Copy the timestamp
    from the trigger.

    :param trigger: any token; value is ignored but time is copied to the
        output
    :type trigger: TTToken

    :param const: the constant value
    :type const: any

    :return: constant-valued token with the trigger's ``TTTime``
    :rtype: TTToken
    '''
    return const


@SQify
def DEADLINE(x, TTTimeDeadline=None):
    '''
    Building block in implementing TTFinishByOtherwise. Programmer direct use
    is highly discouraged.

    :param x: data token; this SQ will act as a no-op if the arrives before
        the deadline and matches the deadline's context
    :type x: TTToken

    :param TTTimeDeadline: token used during this SQs firing rule to enforce
        the deadline
    :type TTTimeDeadline: TTToken

    :return: signal to either run Plan B or an identity of the data token
    :rtype: Tuple[TTToken, TTToken]
    '''
    from ticktalkpython.Empty import TTEmpty
    if (x is None):
        return TTEmpty(), None
    return x, TTEmpty()


@SQify
def MERGE(then_exp, else_exp):
    '''
    Acts as an identity node. Coupled with the Immediate firing rule.
    Programmer direct use is highly discouraged.

    :param then_exp: takes one branch of execution
    :type then_exp: TTToken

    :param else_exp: takes other branch of execution
    :type else_exp: TTToken

    :return: token of the identity of either then_exp or else_exp
    :rtype: TTToken
    '''
    if else_exp is None:
        return then_exp
    return else_exp


@SQify
def TIME_TOKEN(trigger):
    '''
    Generate a ``TTToken`` with the given time value. Copy the value from the
    trigger.

    :param trigger: any token; value is ignored but time is copied to the
        output
    :type trigger: TTToken

    :param time: a TTTime
    :type const: TTTime

    :return: passthrough value with the trigger's ``TTTime``
    :rtype: TTToken
    '''
    return trigger


@SQify
def SYSLOG(a):
    '''
    Print the argument as a debug message to the system log (debug only)

    :param a: any token
    :type a: TTToken

    :return: ``True``-valued token; ``TTTime`` copied from input
    :rtype: TTToken
    '''
    print(f"[log] {a}")
    return True


@SQify
def TTTIME_TO_VALUES(token, TTExecuteOnFullToken=True):
    '''
    Move the upper and lower bounds of the ``TTTime`` interval into the value
    field as a tuple (start, stop) ticks. The ``TTClock`` is not provided; the
    clock is not mutable, and should remain untouched. If it is modified, a
    runtime error is thrown since this has far reaching side effects

    NB: This function operates on the tokens themselves. The optional argument
    is not used; it is metasyntax telling the runtime to supply the full
    tokens instead of just the values.

    This instruction can only be used within a graph that is compiled and run in
    the graph interpetation runtime environment

    :param token: The token whose timestamps must be moved to the value field.
        The time and tag are unchanged

    :type token: ``TTToken``

    :return: token whose value is a tuple containing two integers; the start and
        stop tick on ``TTTime`` part of the token.

    :rtype: ``TTToken``
    '''
    token.value = (token.time.start_tick, token.time.stop_tick)
    return token


@SQify
def VALUES_TO_TTTIME(start_tick_token,
                     stop_tick_token,
                     TTExecuteOnFullToken=True):
    '''
    Set the start and stop ticks of the output token's time field to be the
    values from the two inputs tokens; one is the start tick, the other is the
    stop tick. If the values are not integers or start >= stop, a ValueError
    is thrown.

    NB: This function operates on the tokens themselves. The optional argument
    is not used; it is metasyntax telling the runtime to supply the full
    tokens instead of just the values.

    :param start_tick_token: A tokcn whose value field contains the start tick
        (in terms of the attached clock)

    :type start_tick_token: ``TTToken``

    :param stop_tick_token: A tokcn whose value field contains the stop tick
        (in terms of the attached clock)

    :type stop_tick_token: ``TTToken``

    :return: A token with no value (None) and a ``TTTime`` whose start tick is
        the value of the first input and whose stop tick is the value of the
        second.

    :rtype: ``TTToken``
    '''
    # imports here within the function are necessary since each SQ gets its
    # own private namespace at runtime that starts without imports
    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Time import TTTime
    from ticktalkpython.Tag import TTTag

    time = TTTime(
        start_tick_token.time.clock, start_tick_token.value,
        stop_tick_token.value)  # will fail if values are not integers

    token = TTToken(None, time, tag=TTTag(context=start_tick_token.tag.u))
    return token


@SQify
def READ_TTCLOCK(trigger, delay=0, TTClock=None, TTExecuteOnFullToken=True):
    '''
    Read a clock that is supplied as an input argument. The clock is supplied
    be the runtime environment, but the start and stop tick will be unchanged.

    NB: This function operates on the tokens themselves. The optional argument
    'TTExecuteOnFullToken' is not used; it is metasyntax telling the runtime
    to supply the full tokens instead of just the values.

    :param trigger: A token whose arrival will cause this SQ to run and record
        the current time in the 'value' field

    :type trigger: ``TTToken``

    :return: A token whose value is the current time according to the provided
        ``TTClock``

    :rtype: ``TTToken``
    '''
    # imports here within the function are necessary since each SQ gets its
    # own private namespace at runtime that starts without imports
    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Tag import TTTag

    if TTClock is None:
        raise Exception('No clock supplied; that must be present when calling '
                        'this function within the GRAPHify\'d function')

    # suspect variable name, given prevalence of TTClock
    current_time = TTClock.now() + delay

    return TTToken(current_time,
                   trigger.time,
                   tag=TTTag(context=trigger.tag.u))


@SQify
def COPY_TTTIME(value_token, time_token, TTExecuteOnFullToken=True):
    '''
    SQ that creates a new token with the value of one and the time tag of
    another. This may be challenging to work with when trying to synchronize the
    two together, at least if the value_token did not originate from the root of
    the graph, as the tokens will obviously not synchronize. This will also be
    problematic if they use different clocks

    NB: This function operates on the tokens themselves. The optional argument
    'TTExecuteOnFullToken' is not used; it is metasyntax telling the runtime to
    supply the full tokens instead of just the values.

    :param value_token: The token carrying the desired value for the output
        token.

    :type value_token: ``TTToken``

    :param time_token: The token carrying the desired ``TTTime`` for the output
        token

    :return: A ``TTToken`` carrying the value of the first input and time of the
        second

    :rtype: ``TTToken``
    '''
    # imports here within the function are necessary since each SQ gets its own
    # private namespace at runtime that starts without imports

    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Tag import TTTag

    return TTToken(value_token.value,
                   time_token.time,
                   tag=TTTag(context=value_token.tag.u))


@SQify
def GET_INFINITY(trigger, TTClock=None):
    '''
    Return the maxiumum timestamp supported by the system. Note that the
    trigger value is unused.

    :param trigger: Not used
    :type trigger: Not used
    :return: A ``TTToken`` carrying the value of the maximum timestamp.
    :rtype: ``TTToken``
    '''

    from ticktalkpython.Time import TTTime
    return TTTime.infinite(TTClock).stop_tick


@SQify
def GET_NEG_INFINITY(trigger, TTClock=None):
    '''
    Return the minimum timestamp supported by the system. Note that the
    trigger value is unused.

    :param trigger: Not used
    :type trigger: Not used

    :return: A ``TTToken`` carrying the value of the minimum timestamp.
    :rtype: ``TTToken``
    '''

    from ticktalkpython.Time import TTTime
    return TTTime.infinite(TTClock).start_tick


@SQify
def ADD_TIME_DELAY(token, delay=0, TTExecuteOnFullToken=True):
    '''
    Modifies the start and end tick of the time interval of the token to be
    delayed by parameter delay.

    :param value_token: The token carrying the desired value for the output
        token.
    :type token: ``TTToken``

    :param delay: the specified delay
    :type delay: int

    :return: A ``TTToken`` with time interval [x, y] as [x + delay, y + delay]
    :rtype: ``TTToken``
    '''

    token.time.start_tick += delay
    token.time.stop_tick += delay
    return token


@SQify
def SET_TIMEOUT(token, TTClock=None, TTExecuteOnFullToken=True):
    '''
    Modifies the start and end tick of the tine interval of the token to be
    end tick and Time.Infinity respectively.

    :param value_token: The token carrying the desired value for the output
        token.
    :type token: ``TTToken``

    :return: A ``TTToken`` with time interval [x, y] as [y, +infty]
    :rtype: ``TTToken``
    '''
    from ticktalkpython.Time import TTTime

    token.time.start_tick = token.time.stop_tick
    token.time.stop_tick = TTTime.infinite(TTClock).stop_tick
    return token
