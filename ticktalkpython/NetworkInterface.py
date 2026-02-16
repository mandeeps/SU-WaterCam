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
The ``TTNetworkInterface`` creates this generic interface for exchanging
``TTNetworkMessage`` objects, which have a similar abstact interface such that
their format depends on the ``TTNetworkInterface``.
'''

from abc import abstractmethod
from abc import ABC
from enum import Enum

from . import DebugLogger

logger = DebugLogger.get_logger('Network')

class TTNetworkInterface(ABC):
    '''
    Superclass for the TTNetworkInterface, which may be of different type
    (``TTNetworkInterfaceType``). An ensemble should have one network interface
    that it uses to communicate with the other ensembles in the network. This is
    simply a specification to show what the minimum implementation of a Network
    Interface entails in our system.

    :param receiver_function: The a callback function to call whenever a message
        is received through the network :type receiver_function: function
    '''
    def __init__(self, receiver_function=None):
        # children should call super()__init__(receiver_function) before anything
        # else
        self.receiver_function = receiver_function

    @abstractmethod
    def send(self, message, ensure):
        '''
        Send a TTNetworkMessage through the network interface

        :param message: A message to send through the network interface

        :type message: TTNetworkMessage
        '''

    @abstractmethod
    def listen(self):
        '''
        Equivalent to receiving; sits in an infinite loop, waiting to receive
        messages over the network interface, and calls the receiver_function on
        the payload of the message, once arrived (and defragmented/stripped of
        headers, where applicable)
        '''
        # In most cases, this should be running as a separate thread. It is IO
        # locked, as it runs in accordance with external mechanisms like a
        # native UDP/IP stack or a WiFi module over SPI

    @abstractmethod
    def cleanup(self):
        '''
        Use to release OS resources allocated for the network
        '''

class TTNetworkMessage(ABC):
    '''
    A message to be send across a network interface in the TTPython system.
    Consists of a payload, recipient, and a flag for guaranteed delivery.

    Child classes of ``TTNetwork`` should implement a child class of this as
    well

    :param payload: The payload to be sent to the recipient.

    :type payload: Any

    :param recipient: The name of the recipient ensemble

    :type recipient: string

    :param ensure: Wheter the network should attempt to ensure/guarantee
        delivery of this message. Default to False

    :type ensure: bool
    '''
    def __init__(self, payload, recipient, ensure=False):
        self.payload = payload
        self.recipient = recipient
        self.ensure = ensure

class TTNetworkMessageType(Enum):
    '''
    A set of message types to make it easier for the ensemble to distinguish the
    purpose of a message. When serialized, this is limited to two bytes (2^16
    distinct TTMessageTypes). This should more than exceed the quantity of
    TTPython objects to identity, but might need to be augmented with a more
    flexible approach to identifying serialized data.
    '''
    SQInstance = 0
    InputToken = 1
    ClockSync = 2
    Config = 3

class TTNetworkInterfaceType(Enum):
    '''
    Network interface types
    '''
    Simulated = 0
    UDP_IP = 1
