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

import datetime

TIME_OFFSET = 1e6
TIME_FRACTIONAL = 3
PRINTED_TIME_OFFSET = 10 ** TIME_FRACTIONAL
'''
default name for the runtime manager ensemble. Other ensembles generally assume
there is one runtime manager, and it goes by this name. This is how they build
the first entry to their routing table and send a message to 'Join' the network
of ensembles
'''
RUNTIME_MANAGER_ENSEMBLE_NAME = 'runtime-manager'


def get_readable_time(t):
    return datetime.datetime.fromtimestamp(t).strftime(
        '%Y-%m-%d %H:%M:%S.%f')[:-TIME_FRACTIONAL]
