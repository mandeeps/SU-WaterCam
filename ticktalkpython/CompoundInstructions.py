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
'''
The following functions are known TTPython compound functions that compile
to multiple SQs. An intuition of their semantics is shown in each function
body but are actually compiled to a group of SQs.
'''

from Empty import TTEmpty


def TTFinishByOtherwise(data_token_to_check, TTTimeDeadline, TTPlanB,
                        TTWillContinue):
    '''
    TTFinishByOtherwise handles exceptional timing when action does not
    complete before a specified deadline. It executes like a try/catch block
    for timing. `data_token_to_check` first must match the deadline token
    specified by `TTTimeDeadline`. If it matches with the deadline token and
    is less than the deadline specified by the `TTTimeDeadline` token, it
    will return `data_token_to_check`. Otherwise, if the time has passed,
    it will run `TTPlanB`. The `TTWillContinue` flag indicates whether
    TTFinishByOtherwise will return after running `TTPlanB`.

    The following code below will run in the case `data_token_to_check`
    matches with a `TTTimeDeadline` token or the deadline has passed from
    specified by the value in the `TTTimeDeadline` token.

    This function can only be called in an annotated ``@GRAPHify`` function.

    :param data_token_to_check: the token to be used to check if the deadline
        has been met

    :type data_token_to_check: TTToken

    :param TTTimeDeadline: the token responsible for setting the deadline. The
        stop_tick time of the token is used as the deadline

    :type TTTimeDeadline: TTToken

    :param TTPlanB: When a deadline is triggered, the expression within Plan B
        will be run. Currently, the value is assumed to be a function call.
        In future releases, this will be a generic expression.

    :type TTPlanB: Python expression

    :param TTWillContinue: this specifies whether a value will be propagated
        after the Plan B expression runs. Setting this to `False` will stop
        any further token generation, which effectively stops any downstream
        nodes from firing in the same iteration

    :type TTWillContinue: bool
    '''
    # data token matched and came in on time
    if data_token_to_check is not None:
        return data_token_to_check

    # deadline has passed, but data has not come in
    planb_value = TTPlanB()

    if TTWillContinue:
        return planb_value

    return TTEmpty()


def TTSingleRunTimeout(expr, TTTimeout):
    '''
    This TT Compound function takes an expression and a TTTimeout. This
    creates a group of SQs ensuring that only 1 instance of the expression can
    be run at a particular time and prevent out-of-order execution. This is
    useful as a synchronization primitive to prevent livelock occurring in
    the dataflow graph with repeated calls to a function.

    This function can only be called in an annotated ``@GRAPHify`` function.

    :param expr: The expression guarded with a synchronization lock. Only one
        instance of this expression will run in the dataflow graph it resides
        in

    :type expr: Python expression

    :param TTTimeout: The timeout period before releasing an instance's lock on
        an executing expression. This prevents livelock in cases where one
        composes `TTSingleRunTimeout` with `TTPlanB`.

    :type TTTimeout: int
    '''

    global sq_state

    # the following test and set operation is atomic in the dataflow graph
    # with its synchronization rules
    if sq_state.get('lock', False) is False:
        sq_state['lock'] = True

    expr()

    # wait for TTTimeout microseconds before releasing lock
    import time
    time.sleep(TTTimeout / 1e6)

    sq_state['lock'] = False
