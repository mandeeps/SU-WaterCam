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
The ``TTNetwork`` in TTPython abstracts over network interface types.
'''

import pickle

from . import DebugLogger
from .NetworkInterface import TTNetworkInterfaceType
from .NetworkInterfaceSim import TTNetworkMessageSim
from .NetworkInterfaceSim import TTNetworkInterfaceSim
from .NetworkInterfaceUDP import TTNetworkInterfaceUDP
from .NetworkInterfaceUDP import TTNetworkMessageUDP

logger = DebugLogger.get_logger('Network')

class TTNetwork():
    '''
    This is a small wrapper class for the ``TTNetworkInterface`` that abstracts
    over network implementations. This also holds and manages the routing table,
    although the addresses within this will vary based on the actual network
    interface.

    :param ensemble_name: The (unique) name of this ensemble

    :type ensemble_name: string

    :param interface_type: The type of ``TTNetworkInterface`` to be created.
        Depending on the value, the set of arguments will be used in different
        ways

    :type interface_type: TTNetworkInterfaceType :param sim: If using a
        simulated form of network, this is the simulation environment. Defaults
        to None :type sim: ``simpy.Environment``

    :param ip: If using a physical network interface that invokes the IP layer,
        include that as a string here in IPv4 format. Defaults to None

    :type ip: string

    :param port: The port to use for the network interface. Must be between 1024
        and 65535 and must not be used by another other process on the machine.
        Defaults to None

    :type port: int

    :param receiver_function: A callback to execute on whenever a message is
        received over the network. This should accept one input argument, i.e.,
        the message received. This is generally a function that inserts the
        message into a shared queue.

    :type receiver_function: function
    '''
    # TTNetwork may be a bit of an overspecification, but the main goal is to
    # keep things like the NetworkManager from having to change its
    # behavior based on the network interace implementation. This is practical
    # decision based on improving abstractions. There is likely a better way to
    # create the network interface, as including all possible arguments as
    # optional here is unwieldy. This will be more useful if we decide to
    # support multiple network interfaces, though. There is probably a better
    # way to create this Network
    def __init__(
            self,
            ensemble_name,
            interface_type,
            sim=None,
            ip_addr=None,
            port=None,
            receiver_function=None):
        self.ensemble_name = ensemble_name
        self.interface_type = interface_type
        if interface_type == TTNetworkInterfaceType.Simulated:
            self.network_interface = TTNetworkInterfaceSim(sim)
        elif interface_type == TTNetworkInterfaceType.UDP_IP:
            self.network_interface = TTNetworkInterfaceUDP(
                ip_addr=ip_addr,
                port=port,
                receiver_function=receiver_function)
        else:
            raise TypeError('Network Interface type not recognized')

        self.routing_table = {}

    def send_message(self, message, ensure=False):
        '''
        Send a message through the network interface; the message should have
        already been created with ``TTNetwork.create_message``

        :param message: The message to send

        :type message: TTNetworkMessage

        :param ensure: Whether to attempt guaranteed delivery to the recipient
            ensemble

        :type ensure: bool

        :return: None
        '''
        self.network_interface.send(message, ensure=ensure)

    def create_message(self, recipient_name, obj):
        '''
        Create a network message for some object, to be sent to a specific
        ensemble. This will handle translating the object to send the
        destination into whatever the network interface expects

        :param recipient_name: The name of the ensemble the message should be
            sent to

        :type recipient_name: string

        :param obj: The payload of the message to send. It can be anything, so
            long as it can be serialized (using the python 'pickle' format)

        :type obj: Any

        :return: The message to be sent

        :rtype: TTNetorkMessage or a child class
        '''
        recipient_address  = self.get_recipient_address(recipient_name)

        if self.interface_type == TTNetworkInterfaceType.Simulated:
            payload = obj
            message = TTNetworkMessageSim(recipient_address, payload)
        elif self.interface_type == TTNetworkInterfaceType.UDP_IP:
            payload = bytearray(pickle.dumps(obj))

            recipient_ip = recipient_address.split(':')[0].strip()
            recipient_port = int(recipient_address.split(':')[1].strip())

            message = TTNetworkMessageUDP(
                recipient_ip = recipient_ip,
                recipient_port=recipient_port,
                payload_byte_array=payload)
        else:
            raise ValueError(
                f'Unknown Network Interface Type: {self.interface_type}')

        return message


    def add_route(self, recipient_ensemble_name, recipient_address):
        '''
        Add an address to the network interface routing table

        :param recipient_ensemble_name: The name of the ensemble to send to;
            this must be unique among all ensembles in the system

        :type recipient_ensemble_name: string

        :param recipient_address: An identifier for the addres of the ensemble;
            specific to the network interface type. The format depends on the
            ``TTNetworkInterface`` implementation.

        :type recipient_address: string | TTEnsemble
        '''
        self.routing_table[recipient_ensemble_name] = recipient_address

    def update_route(self, recipient_ensemble_name, recipient_address):
        '''
        Update an entry in the routing table

        :param recipient_ensemble_name: The name of the ensemble to send to;
            this must be unique among all ensembles in the system

        :type recipient_ensemble_name: string

        :param recipient_address: An identifier for the addres of the ensemble;
            specific to the network interface type

        :type recipient_address: string | ``TTEnsemble``
        '''
        # Updating simply means reassigning the value in the dictionary based on
        # what should be the same recipient ensemble name
        self.add_route(recipient_ensemble_name, recipient_address)

    def get_recipient_address(self, recipient_ensemble_name):
        '''
        Retrieve the address from a routing table dictionary based on the name
        of a ``TTEnsemble``

        :param recipient_ensemble_name: The name of the ensemble to send to;
            this must be unique among all ensembles in the system

        :type recipient_ensemble_name: string
        '''
        try:
            recipient = self.routing_table[recipient_ensemble_name]
        except KeyError:
            recipient = None
        return recipient

    def close(self):
        self.network_interface.cleanup()
