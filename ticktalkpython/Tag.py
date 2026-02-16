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
Tokens carry tags that identify where they are meant to go. In the dynamic
tagged token dataflow, this included form of 'static' and 'dynamic' context tag,
where the static tag was akin to the program counter (telling where to execute
in the code) and dynamic was related to the memory/runtime usage of that code,
like a stack frame.

Tags in TTPython carry a similar dynamic context (which is currently unused, but
is useful for function application or multi-tenant programs) 'u', whereas the
rest of the tag pertains to where the token must go, including the recipient SQ
(name), port (number), and ensemble (name).

Most of the static tag is filled in during the forwarding phase of the SQ, as
tokens are duplicated for all of their recipients
'''

from . import DebugLogger

logger = DebugLogger.get_logger('Tag')

# do we ever have reason to use anything else? lots more machinery if we do.
DEFAULT_CONTEXT_ID = 'u1'


class TTTag:
    '''
    Form a token's tag from a context id, an SQ name, a timestamp, and a port.
    In u-interpreter parlance, u is the context, c is the 'code block' and s is
    the instruction (so they are combined as we cannot use any further
    graularity than one sq), t overtakes the 'i' iterator part of dynamic
    context, and p is the port.

    :param context: 'u' of the tag. Related to function application

    :type context: string

    :param sq: the name of the SQ the token is intended for

    :type sq: string

    :param port: the port number the token should arrive to

    :type port: int

    :param ensemble_name: the name of the ensemble the SQ of interest is
        expected to reside on

    :type ensemble_name: string
    '''

    def __init__(self, context=None, sq=None, port=None, ensemble_name=None):

        self.u = context
        # this represents c.s in u-interpreter terminology; this is the SQ NAME
        self.sq = sq
        self.p = port
        # only relevant in a physically mapped system
        self.e = ensemble_name

    def __repr__(self):
        return '<TTTag: u={},sq={},p={},e={}>'.format(self.u, self.sq, self.p,
                                                      self.e)

    def __eq__(self, other):
        if isinstance(other, TTTag):
            return (self.u == other.u and self.sq == other.sq
                    and self.p == other.p and self.e == other.e)
        return NotImplemented

    def __hash__(self):
        return hash((self.u, self.sq, self.p, self.e))
