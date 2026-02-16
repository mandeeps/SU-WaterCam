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

import itertools


class TTMappedPort:
    '''
    TTMappedPorts carry all the necessary information to generate tags that
    represent the location a token must be sent to. This information should be
    filled in when mapping the program to a set of devices. A TTMappedPort
    replaces the compile time Ports for a SQ.

    :param ensemble_name: Name of an ensemble that hosts the recipient SQ

    :type ensemble_name: String

    :param sq_name: Name of the SQ to send output tokens to

    :type sq_name: String

    :param port_number: Index (from 0) of the port with the named SQ the
        token must be sent to

    :type port_number: int
    '''
    def __init__(self, ensemble_name, sq_name, port_number):

        self.ensemble_name = ensemble_name
        self.sq_name = sq_name
        self.port_number = port_number

    def __repr__(self):
        return f"<TTMappedPort Ens={self.ensemble_name}; SQ-name={self.sq_name}; Port={self.port_number}>"

    def __eq__(self, other):
        if isinstance(other, TTMappedPort):
            return ((self.ensemble_name == other.ensemble_name)
                    and (self.sq_name == other.sq_name)
                    and (self.port_number == other.port_number))
        return NotImplemented


class Port:
    '''
    A port represents either an input or output port
    Its use depends on how the SQ uses the port
    '''
    id_iter = itertools.count()

    # Each instance of a port is unique.
    # output ports link to any sq's input port list that shares the same data_name
    def __init__(self, data_name=None):
        self.data_name = (data_name if data_name is not None else
                          f'${next(Port.id_iter)}')

    def __repr__(self):
        return f'P({str(self.data_name)})'
