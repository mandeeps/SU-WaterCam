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
A network interface for a simulated network.

This is the simplest form of network, in which the messages contain direct
references to the other ``TTEnsemble`` Object; this is permissible when using
simpy simulation environment, as the entire system runs in a single-threaded
event loop such that memory references are safe enough to use between ensembles
(Note that this strategy is only used in the network layer).

An extension of this interface can/should include a model for the network, such
that network latency and packet loss can be injected and modulated to show how
different system deployments or graph mappings impact system performance, such
as end-to-end latency.
'''

from .NetworkInterface import TTNetworkMessage
from .NetworkInterface import TTNetworkInterface
from . import DebugLogger

logger = DebugLogger.get_logger('SimNetwork')

class TTNetworkMessageSim(TTNetworkMessage):
    '''
    A network message for the simulated environment
    '''
    def __init__(self, recipient, payload):
        super().__init__(payload, recipient)

class TTNetworkInterfaceSim(TTNetworkInterface):
    '''
    Create the simulated version of the network interface, which includes a
    minimum implementation of a send and listen function.

    In the simulated case, we maintain direct references to other ensembles such
    that having ensembles explicitly listen to the network interface is
    unnecessary and computationally wasteful (as it would mean creating another
    queue and process just to maintain an abstraction). In this way, there is no
    'receiver_function' callback as an input argument
    '''
    def __init__(self, sim):
        # No need to a receiver callback because the 'listen' abstraction is unnecessary.
        super().__init__(receiver_function=None)
        self.sim = sim

    def send(self, message, ensure=False):
        '''
        Send a ``TTNetworkMessage`` through the interface. We skip the usual
        sender-receiver interface, and just inject it directly into the
        ensemble.

        :param message: The message to send to another ensemble

        :type message: ``TTNetworkMessage``

        :param ensure: Whether the message should have guaranteed delivery.
            Currently unused, but inlcuding to satisfy the super class's
            interface. Defaults to False

        :type ensure: bool
        '''
        # super().send(message)
        # TODO: Create a model that adds simulated delay
        # and/or packet loss. May need to expand the send-listen abstraction a
        # bit to make that work cleanly.
        ensemble = message.recipient
        if ensemble is not None:
            # if a delay is necessary, that can be inserted here, but it needs
            # to be called by a simpy process such that 'yield' has meaning
            # (process is a generator, so calling inline will usually cause the
            # program to hang)
            ensemble.receive_network_message(message)
            return

        raise Exception(
            f"Failed to find ensemble named in message as {message.recipient}")

    def listen(self):
        # super().listen()
        logger.warning("'listen' is not currently supported in "
                       "the TTNetworkInterfaceSim; returning immediately.")

    def cleanup():
        pass
