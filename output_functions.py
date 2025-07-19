# Copyright 2024 Carnegie Mellon University
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
This file is used to specify how to handle where Messages from output arcs
should go. The function should have a argument list provided by the
`runrtm.py` or `runens.py` script through a list of arguments provided by the
`--output_func` flag. The list should start with the name of the desired
output function followed by the arguments in order excluding the last
argument, which should be the message to be sent. The script will partially
apply the given arg list. For example, the default call would be
`--output_func log_msg_to_file ./output.log` and partially applies the first
arg. We recommend using this flag as the last option in the call to the
wrapper scripts to avoid clashing with other options in the script.
'''
import requests
import output_functions
from functools import partial
from inspect import getmembers, isfunction

from ticktalkpython.IPC import Message


def get_output_func(name):
    funcs = getmembers(output_functions, isfunction)
    return next(
        (func_tuple[1] for func_tuple in funcs if func_tuple[0] == name), None)


def get_applied_output_func(name, args):
    output_func = get_output_func(name)
    return partial(output_func, *args)


'''
Add user-defined output functions below
'''


def log_msg_to_file(log_file_name, msg: Message):
    token, source_sq_name, source_ensemble_name = msg.payload

    with open(log_file_name, 'a') as log_file:
        log_line = (str(token) + 'from SQ "' + source_sq_name + '" on ENS: "' +
                    source_ensemble_name + '".\r\n')
        chars_written = log_file.write(log_line)
    return str(log_line) == chars_written


def send_to_endpoint(endpoint, msg: Message):
    r = requests.post(url=endpoint, data=msg)
    return r
