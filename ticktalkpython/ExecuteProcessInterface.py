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
from .Time import TTTime
from .Time import TTTimeSpec
from .TTToken import TTToken

class TTExecutionContext:
    '''
    A ``TTExecutionContext`` contains all necessary information to invoke and
    execute an SQ (``TTSQExecute``) at runtime. This is entirely free of direct
    memory references, instead relying on consistent naming to identify the SQ
    to run the provided set of inputs on.

    :param sq_name: The name of the SQ

    :type sq_name: string

    :param inputs: A list of ``TTTokens`` that the SQ will operate on.

    :type inputs: list(TTToken)

    :param input_time_overlap: The time overlap of the input tokens as
        determined during synchronization (within ``TTInputTokenProcess`` and
        ``TTSQSync``)

    :type input_time_overlap: TTTime

    :param estimate_runtime: An estimate of how long it will take to execute an
        SQ, default as 0

    :type estimate_runtime: int
    '''
    def __init__(self,
                 sq_name,
                 inputs,
                 input_time_overlap,
                 estimate_runtime=0):
        self.sq_name = sq_name
        self.inputs = inputs
        self.input_time_overlap = input_time_overlap
        self.estimate_runtime = estimate_runtime

    # FIXME: this modifies its original list.
    # this means that sticky tokens in TokenStorage are affected!
    def dereference_token_times(self):
        '''
        Convert the ``TTTime```objects within the tag to ``TTTimeSpecs``,
        which are free of memory references that make inter-process and
        inter-device exchange of tokens less efficient (we wish to avoid
        serializing the entire clock tree for every single passed token).
        '''
        for tok in self.inputs:
            if isinstance(tok, TTToken):
                if isinstance(tok.time, TTTime):
                    tok.time = TTTimeSpec.from_time(tok.time)
            else:
                raise ValueError('input to TTExecutionContext is not a token!')

    def rereference_token_times(self, clocks):
        '''
        Convert the ``TTTimeSpec`` objects within the tokens to ``TTTime``; the
        ``TTExecuteProcess` will expect ``TTTimes``. To do this conversion, we
        provide a set of clocks to revert the ``TTClockSpec`` within the
        ``TTTimeSpec`` to an actual ``TTClock``

        The main purpose of this is to prevent clocks from being copied. This
        is problematic w.r.t. consistency, the relation between the root-clock
        and hardware time. It may also increase the size of messages send,
        thus requiring more bytes be serialized & deserialized
        '''
        for tok in self.inputs:
            if isinstance(tok, TTToken):
                tok.time = TTTimeSpec.to_time(tok.time, clock_list=clocks)
            else:
                raise ValueError('input to TTExecutionContext is not a token!')

    def __repr__(self):
        return (f'<TTExecutionContext {hex(id(self))} - '
                f'{self.sq_name} execute on {self.inputs}')
