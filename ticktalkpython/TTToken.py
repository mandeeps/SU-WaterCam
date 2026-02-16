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
Tokens carry values between SQs, and contain ``TTTime`` and ``TTTag``
attributes that designate their context with respect to time and their place
within the application. The ``TTTime`` is primarily used to associate like
tokens during the synchronization phase of an SQ.

Tokens may carry any serializable value (there is no shared memory between SQs
or Ensembles); we use Python native 'pickle' format for serialization. For
example, a 'Process' object is not serializable, nor are most objects that
work with hardware interfaces. The best way to check if an object is
serializable is by calling pickle.dumps(obj), which will raise an Exception if
it is *not* serializable.
'''

import copy
import reprlib

from .Time import TTTime
from . import DebugLogger

logger = DebugLogger.get_logger('token')

class TTToken():
    '''
    ``TTToken`` objects carry a data value and a time-tag.  They are designed
    and implemented in such a way that many Python infix operators can accept
    ``TTToken`` objects as parameters, extracting the data value and operating
    on that while properly handling the time value.

    Tokens originating from streaming sources should be flagged as such by
    setting the ``streaming`` parameter to ``True``

    :param value: an un-interpreted item representing the data to be
        transported by this token

    :type value: any

    :param time: the time corresponding to the value -- intended to indicate the
        interval of its validity

    :type time: TTTime

    :param streaming: a flag indicating that this token originated from a
        streaming souroce or from an SQ that had received a streaming token as
        input

    :type streaming: bool, optional

    :param tag: A tag that dictates where to send a token in the TickTalk system
        and program

    :type tag: ``TTTag``
    '''

    def __init__(self, value, time, is_streaming=False, tag=None):
        self.value = value
        self.time = time
        self.tag = tag
        self.is_streaming = is_streaming

    def __repr__(self):
        return f"<TTToken {reprlib.repr(self.value)} T:{self.time}, Tag:{self.tag}>"

    def __eq__(self, other):
        if isinstance(other, TTToken):
            return (self.value == other.value and self.time == other.time
                    and self.tag == other.tag)
        return NotImplemented

    def __hash__(self):
        return hash((self.value, self.time, self.tag))

    def __neg__(self):
        return TTToken(-self.value, self.time)

    # Support Python infix operations in the MAIN function
    def __add__(self, token):
        if isinstance(token, TTToken):
            time_overlap = TTTime.common_ancestor_overlap_time(
                self.time, token.time)
            if time_overlap is not None:
                return TTToken(self.value + token.value, time_overlap)
            else:
                return None
        else:
            # Take a chance that the "token" is really a raw value like a
            # constant
            return TTToken(self.value + token, self.time)

    def __radd__(self, token):
        return self.__add__(token)

    def __sub__(self, token):
        if isinstance(token, TTToken):
            time_overlap = TTTime.common_ancestor_overlap_time(
                self.time, token.time)
            if time_overlap is not None:
                return TTToken(self.value - token.value, time_overlap)
            else:
                return None
        else:
            # Take a chance that the "token" is really a raw value like a
            # constant
            return TTToken(self.value - token, self.time)

    def __rsub__(self, token):
        # non-commutative--can't simply call __sub__
        if isinstance(token, TTToken):
            time_overlap = TTTime.common_ancestor_overlap_time(
                self.time, token.time)
            if time_overlap is not None:
                return TTToken(token.value - self.value, time_overlap)
            else:
                return None
        else:
            # Take a chance that the "token" is really a raw value like a
            # constant
            return TTToken(token - self.value, self.time)

    def __mul__(self, token):
        if isinstance(token, TTToken):
            time_overlap = TTTime.common_ancestor_overlap_time(
                self.time, token.time)
            if time_overlap is not None:
                return TTToken(self.value * token.value, time_overlap)
            else:
                return None
        else:
            # Take a chance that the "token" is really a raw value like a
            # constant
            return TTToken(self.value * token, self.time)

    def __rmul__(self, token):
        return self.__mul__(token)

    def __truediv__(self, token):
        if isinstance(token, type(TTToken(None, None))):
            time_overlap = TTTime.common_ancestor_overlap_time(
                self.time, token.time)
            if time_overlap is not None:
                return TTToken(self.value / token.value, time_overlap)
            else:
                return None
        else:
            # Take a chance that the "token" is really a raw value like a
            # constant
            return TTToken(self.value / token, self.time)

    def __rtruediv__(self, token):
        # non-commutative--can't simply call __truediv__
        if isinstance(token, TTToken):
            time_overlap = TTTime.common_ancestor_overlap_time(
                self.time, token.time)
            if time_overlap is not None:
                return TTToken(token.value / self.value, time_overlap)
            else:
                return None
        else:
            # Take a chance that the "token" is really a raw value like a
            # constant
            return TTToken(token / self.value, self.time)

    def copy_token(self):
        '''
        Make a copy of the token. The tag and value are deep copied , but the
        time is not, as it would lead to copies of the clock tree, which may
        cause odd side effects

        :return: A copy of this token

        :rtype: ``TTToken``
        '''
        tag = copy.deepcopy(self.tag)
        # do not deep copy! it will, duplicate the clock and break things
        time = copy.copy(self.time)
        # assuming deep-copy; if the user is doing something with objects and
        # references, this may cause issues!
        value = copy.deepcopy(self.value)

        return TTToken(value, time, is_streaming=self.is_streaming, tag=tag)
