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
A process to manage the abstract network interface for the ensemble. This
follows the familiar pattern in other processes, in which the main body of the
process awaits inputs through a singular queue, and all incoming items contain
self-identification information so this process knows how to treat the data.
This includes forwarding data meant for other processes when said data arrives
on the network interface.

All Process/Manager classes follows the same design pattern. They implement a
singular input queue from which they read new ``Messages``, which
self-identify their function.  After processes are created, they exchange
interfacing information, primarily in the form of callback functions. After
configuring interfaces, the processes start. Each of these processes spends its
idle time waiting for new inputs within a 'run loop', responding to messages as
they arrive; the responses will modify internal process state and produce new
messages for other processes implemented on the Ensemble, which 'owns' the
processes.

'''

import pickle
import math
import queue  # for queues in threaded runtime vs. process-based runtime
import os

from .Network import TTNetwork
from .NetworkInterface import TTNetworkMessage
from .NetworkInterface import TTNetworkInterfaceType
from .TTToken import TTToken
from . import Tag
from .Port import TTMappedPort
from . import DebugLogger
from .IPC import Message
from .IPC import NetMsg
from .IPC import Recipient
from .IPC import RuntimeMsg
from .IPC import SyncMsg
from .IPC import FinishedException
from .Constants import RUNTIME_MANAGER_ENSEMBLE_NAME
from .TimedEventProcess import wait


class TTNetworkManager():
    '''
    A process to manage the network and token forwarding on behalf of SQs.

    This process maintains the network interface, including sending and
    receiving; the network interface itself handles the exact details, which may
    be simulated or physical (e.g., UDP layer).

    This process maintains a mapping for each SQ so that when an output token is
    ready, it can duplicate said token and send it to each output destination.
    This will include handling control layer outputs like deadline tokens

    :param input_queue: A queue into which all messages arrive, both
        ``Message`` and ``TTNetworkMessage`` types

    :type input_queue: ``queue.Queue`` or ``multiprocess.Queue``, depending
        on simulated or physical runtime (physical meaning actually distinct
        processes at the OS level)

    :param ensemble_name: The name of the ensemble this process resides on

    :type ensemble_name: string

    :param sim: The simulation environment, if present

    :type sim: ``simpy.Environment``
    '''

    def __init__(self, input_queue, delay=0, ensemble_name=None):

        self.input_queue = input_queue  # receive tokens here
        self.ensemble_name = ensemble_name
        self.logger = DebugLogger.get_logger(
            f'NetworkManager({self.ensemble_name})')

        # each should be an array to help fill in the within the ensemble
        # destination for outgoing tokens. Key is sq-name and value is an array
        # of output destinations for the _singular_ output port
        self.sq_mappings = {}

        self.sim = None
        self.sim_process = None

        # TODO: function usage seems like a configuration architecture
        # TODO: decision rather than at the module level. Needs a large
        # TODO: refactor with all 3 processes
        # insert delay for the network level. Good place to do some
        # sensitivity analysis by increasing/decreasing simulated/emulated
        # delays
        self.delay = delay
        if 0 < delay:
            self.send = self.create_and_send_message_delay
        else:
            self.send = self.create_and_send_message

        self.rx_network = None
        self.network = None
        self.input_token_func = None
        self.input_execute_func = None
        self.input_runtime_manager_func = None

        # only enabled for devices NOT running the rtm manager
        self.output_func = None

    # only enable for devices NOT running the rtm manager
    def change_output_func(self, output_func):
        self.output_func = output_func

    def receive_from_self(self, message):
        '''
        Receive a message intended for this process (that would otherwise go
        through the network)

        :param message: The message intended for this same ensemble

        :type message: TTNetworkMessage
        '''
        self.input_queue.put(message)

        # May be asleep because of simulated wait
        if (self.sim is not None and self.sim_process is not None
                and self.sim.active_process != self.sim_process):
            self.sim_process.interrupt()

    def receive_from_network(self, message):
        '''
        Receive a message through the ``TTNetworkInterface``; intended to be
        used as a callback function.

        :param message: The message intended for this same ensemble

        :type message: TTNetworkMessage
        '''
        self.input_msg(message)

    def input_msg(self, message):
        '''
        Callback used to provide messages to this process's input queue.

        If this is a simulated environment, we interrupt the process, which is
        otherwise waiting indefinitely for data to arrive on the queue.

        :param message: The message intended for this process

        :type message: TTNetworkMessage |  Message
        '''
        self.logger.debug('*** Received input message %s', message)
        self.input_queue.put(message)

        if (self.sim and self.sim_process
                and self.sim.active_process != self.sim_process):
            # self.logger.debug('Indefinite wait: t=%f', self.sim.now)
            self.sim_process.interrupt()

    def create_and_send_message(self,
                                dest_ensemble_name,
                                payload,
                                ensure=False):
        '''
        Create a ``TTNetworkMessage`` to be sent to a destination ensemble.
        Option to guarantee delivery

        :param dest_ensemble_name: The (unique) name of the ensemble to send to.
            This ensemble must be registered in the ``TTNetwork`` routing table,
            else a ``KeyError`` will be thrown

        :type dest_ensemble_name: string

        :param payload: The payload for the message. This is simply the
            value/object to send. This does **not** need to be serialized prior
            to creating the message

        :type payload: Any

        :param ensure: An indicator for whether this message should require
            guaranteed devlivery or not. Defaults to false.

        :type ensure: bool

        :return: The message that is ready to be sent over the network interface

        :rtype: TTNetworkMessage (or a subclass thereof)
        '''
        network_message = self.network.create_message(dest_ensemble_name,
                                                      payload)
        if dest_ensemble_name == self.ensemble_name:
            self.receive_from_self(network_message)
        else:
            self.network.send_message(network_message, ensure=ensure)

    def create_and_send_message_delay(self,
                                      dest_ensemble_name,
                                      payload,
                                      ensure=False):
        '''
        Create a delayed ``TTNetworkMessage`` to be sent to a destination
        ensemble. Delay is specified by self.delay.  Option to guarantee
        delivery.

        :param dest_ensemble_name: The (unique) name of the ensemble to send to.
            This ensemble must be registered in the ``TTNetwork`` routing table,
            else a ``KeyError`` will be thrown

        :type dest_ensemble_name: string

        :param payload: The payload for the message. This is simply the
            value/object to send. This does **not** need to be serialized prior
            to creating the message

        :type payload: Any

        :param ensure: An indicator for whether this message should require
            guaranteed devlivery or not. Defaults to false.

        :type ensure: bool

        :return: The message that is ready to be sent over the network interface

        :rtype: TTNetworkMessage (or a subclass thereof)
        '''

        wait(self.delay, [dest_ensemble_name, payload, ensure],
             self.create_and_send_message,
             sim=self.sim)

    def create_network_interface(self,
                                 network_interface_type,
                                 sim=None,
                                 ip=None,
                                 port=None):
        '''
        Create and start the network interface for this ensemble

        :param interface_type: The type of ``TTNetworkInterface`` to be created.
            Depending on the value, the set of arguments will be used in different
            ways

        :type interface_type: TTNetworkInterfaceType

        :param sim: If using a simulated form of network, this is the simulation
            environment. Defaults to None

        :type sim: ``simpy.Environment``

        :param ip: If using a physical network interface that invokes the IP
            layer, include that as a string here in IPv4 format. Defaults to None

        :type ip: string

        :param port: The port to use for the network interface. Must be between
            1024 and 65535 and must not be used by another other process on the
            machine. Defaults to None

        :type port: int
        '''
        # probably an overspecification... The number of optional parameters
        # are quite ugly
        self.logger.debug('create network interface %s ',
                          network_interface_type)
        self.network = TTNetwork(self.ensemble_name,
                                 network_interface_type,
                                 sim=sim,
                                 ip_addr=ip,
                                 port=port,
                                 receiver_function=self.receive_from_network)

    def setup_proc_intfc(self,
                         input_token_func,
                         input_execute_func,
                         input_runtime_manager_func=None,
                         sim_process=None):
        '''
        Configure the interface to this process, meaning the callback functions
        for sending outputs to the other processes. This process needs a
        callback for each other runtime process, as it may receive inputs for
        any other process through the

        :param input_token_func: A callback function for providing
            ``Message`` inputs to the ``TTInputTokenProcess``

        :type input_token_func: function

        :param input_execute_func: A callback function for providing
            ``Message`` inputs to the ``TTExecuteProcess``

        :type input_execute_func: function

        :param input_runtime_manager_func: A callback function for providing
            ``Message`` inputs to the ``TTRuntimeManagerProcess``. This should
            only be provided if the ensemble is a runtime manager. Defaults to None

        :type input_runtime_manager_func: function

        :param sim_process: A reference to the simulated process that this class
            runs inside of. Mainly used for interrupting the simulated variant on
            input messages. dDefaults to None

        :type sim_process: ``simpy.Process`` | None
        '''
        self.input_token_func = input_token_func
        self.input_execute_func = input_execute_func
        self.input_runtime_manager_func = input_runtime_manager_func

        self.sim_process = sim_process

    def get_next_input(self):
        '''
        Pull the next input off the input queue.
        '''
        if self.sim is not None:
            return self.input_queue.get_nowait()
        else:
            # FIXME: timeout value should be more configurable
            return self.input_queue.get(block=True, timeout=1)

    def run_sim(self, sim):
        '''
        The main run loop for a runtime environment using simulated processes,
        which runs on a single core and can implement many ensembles. This
        function be initially run like any other simpy process to allow proper
        'yield' interpretation (it is technically a generator, thus the run_sim,
        run_phy distinction)

        The network is not created until this point, so we require input
        arguments unlike the other runtime processes

        :param sim: The simulation environment

        :type sim: ``simpy.Environment``
        '''
        import simpy

        self.logger.info('run sim loop NetworkManager')

        self.sim = sim
        self.create_network_interface(TTNetworkInterfaceType.Simulated,
                                      sim=self.sim)
        next_msg = None
        try:
            while True:
                next_msg = None
                try:
                    next_msg = self.get_next_input()

                    if next_msg is not None:
                        if isinstance(next_msg, Message):
                            self.handle_IPC_message(next_msg)
                        elif isinstance(next_msg, TTNetworkMessage):
                            self.handle_network_message(next_msg)
                except queue.Empty:
                    try:
                        yield self.sim.timeout(math.inf)
                    except simpy.Interrupt:
                        continue
                except simpy.Interrupt:
                    continue

        except KeyboardInterrupt:
            raise
        except GeneratorExit:
            self.logger.info('ntwk simpy generator exited')
            raise
        except BaseException:
            raise

    def run_phy(self,
                ip,
                rx_port,
                tx_port,
                input_token_func,
                input_execute_func,
                input_runtime_manager_func=None):
        '''
        The main run loop for a runtime environment using physical processes,
        which can take advantage of multi-core processors.

        The network is not created until this point, so we require input
        arguments unlike the other runtime processes

        :param ip: This ensemble's IP address

        :type ip: string

        :param rx_port: The port to receive inputs on. Defaults to the RX_PORT
            for the UDP interface (TICK in 9-key -> 8425)

        :type rx_port: int

        :param tx_port: The port to send outputs on. Defaults to the TX_Port for
            the UDP interface (TALK in 9-key -> 8225)
        '''
        self.logger.debug('run phy loop NetworkManager')
        self.logger.debug(f'ntwk manager process is on pid {os.getpid()}')
        if self.output_func is not None:
            self.logger.warning('ntwk manager handling unmapped opps with '
                                f'{self.output_func}')
        self.setup_proc_intfc(input_token_func, input_execute_func,
                              input_runtime_manager_func)

        # yes, there are two network interfaces. This is not ideal. The UDP
        # stack was design to use a single port for sending traffic and
        # receiving traffic. It does not handle both particularly well. I am
        # working with what I was given
        #
        # configure the TX network; that will be self.network
        self.create_network_interface(TTNetworkInterfaceType.UDP_IP,
                                      ip=ip,
                                      port=tx_port)
        self.rx_network = TTNetwork(
            self.ensemble_name,
            TTNetworkInterfaceType.UDP_IP,
            ip_addr=ip,
            port=rx_port,
            receiver_function=self.receive_from_network)

        try:
            while True:
                try:
                    next_msg = self.get_next_input()
                    if next_msg is not None:
                        if isinstance(next_msg, Message):
                            self.handle_IPC_message(next_msg)
                        elif isinstance(next_msg, TTNetworkMessage):
                            self.handle_network_message(next_msg)
                except queue.Empty:
                    continue

        except (FinishedException, KeyboardInterrupt):
            return
        except Exception:
            self.logger.exception('NM has ended')
            raise
        finally:
            self.network.close()
            self.rx_network.close()

    def handle_IPC_message(self, msg: Message):
        '''
        This will handle IPC (Inter Process Communication) messages arriving to
        this process via the singular input queue. This will include messages at
        the data, control, and management planes, which will have designators to
        specify how they should be handled (using process-specific enumeration)

        The network process will handle messages related to mapping, routing and
        outgoing tokens
        '''
        if not isinstance(msg, Message) or not isinstance(
                msg.msg_type, NetMsg):
            self.logger.warning(f'received unknown type {type(msg)}')
            return
        if not msg.process_recipient == Recipient.ProcessNetwork:
            self.logger.warning(f'recipient is not in network')
            return

        msg_type = msg.msg_type
        self.logger.debug('New message %s', msg)

        # need to send the whole list of token outputs as a IPCMessage to
        # determine which token is sent on which routed TTMappedPort list
        if msg_type == NetMsg.SendTokenList:
            # send a token along an output arc, including some information
            # about where this token came from.
            source_sq_name = msg.source_sq
            mapping = self.sq_mappings.get(source_sq_name)

            # If the output of this SQ is an input to other SQs, duplicate the
            # token and create tags for each of those destinations
            # NOTE: be less strict on length of mapping to allow control
            # tokens to be underspecified by a SQ. This is used in a
            # STREAMified node. This might be an antipattern.
            # TODO: Change mapping idx to not implicitly follow a list
            # structure. Right now, we have a zip betwen mapping and the list
            # payload. Better to include opp index in the payload to mapping.
            if mapping and len(msg.payload) <= len(mapping):
                for (arc_list, msg_token) in zip(mapping, msg.payload):
                    if 0 < len(arc_list):
                        # check if this is a conditional send
                        if msg_token.msg_type is not NetMsg.EmptyToken:
                            base_token = msg_token.payload['token']

                            for arc_dest in arc_list:
                                tag = Tag.TTTag(
                                    context=base_token.tag.u,
                                    sq=arc_dest.sq_name,
                                    port=arc_dest.port_number,
                                    ensemble_name=arc_dest.ensemble_name)
                                sendable_token = TTToken(base_token.value,
                                                         base_token.time,
                                                         tag=tag)
                                msg = Message(SyncMsg.InputToken,
                                              sendable_token,
                                              Recipient.ProcessInputTokens)

                                # add delay here
                                self.send(arc_dest.ensemble_name, msg)

                        else:
                            self.logger.debug(
                                "Found a TTEmpty token, ignoring this")

                    else:
                        # TODO: fix these extra cases everywhere.
                        # this else chain is too confusing
                        if msg_token.msg_type is not NetMsg.EmptyToken:
                            self.handle_output_arc(msg_token, source_sq_name)
                        else:
                            self.logger.debug(
                                "Found a TTEmpty token, ignoring this")
            # token outputs are longer than the mapping, just send it all to
            # device's output arc
            else:
                for m_token in msg.payload:
                    # TODO: should this check TTEmpty?
                    # or do we unconditionally always show output ports
                    if m_token.msg_type is not NetMsg.EmptyToken:
                        self.handle_output_arc(m_token, source_sq_name)
                    else:
                        self.logger.debug(
                            "Found a TTEmpty token, ignoring this")

        elif msg_type == NetMsg.InstantiateSQ:
            # Instantiate an SQ in this process on the ensemble. All this means
            # is recording the mapping of downstream SQs so we know where to
            # send outputs (if any) expected format is a tuple
            # (sq_name:list, destination_list:list(Port.TTMappedPort))
            if isinstance(msg.payload[1], list):
                sq_name = msg.payload[0]
                mapping = msg.payload[1]
                if (len(mapping) > 0
                        and all([isinstance(arc_list, TTMappedPort)]
                                for arc_list in msg.payload[1])):
                    self.sq_mappings[sq_name] = mapping
                else:
                    # if there is no output mapping for an SQ, we will send the
                    # output token back to the runtime manager, which will log
                    # the token
                    self.sq_mappings[sq_name] = []

            else:
                raise ValueError('Unexpected Payload in InstantiateSQ message')

        elif msg_type == NetMsg.RemoveSQ:
            raise NotImplementedError

        elif msg_type == NetMsg.UpdateMapping:
            raise NotImplementedError

        elif msg_type == NetMsg.AddRoutingTableEntry:
            # expected format is a dictionary with ensemble name and (port:ip OR
            # ensemble_reference:TTEnsemble) as key and value, respectively
            if isinstance(msg.payload, dict):
                for ens_name in msg.payload.keys():
                    address = msg.payload[ens_name]
                    self.network.add_route(ens_name, address)
            elif isinstance(msg.payload, tuple):
                self.network.add_route(msg.payload[0], msg.payload[1])

            self.logger.debug('Routing Table: %s', self.network.routing_table)

        elif msg_type == NetMsg.RemoveRoutingTableEntry:
            raise NotImplementedError
            # if msg.payload in self.routing_table:
            #     del self.routing_table[msg.payload]

        elif msg_type == NetMsg.UpdateRoutingTableEntry:
            if isinstance(msg.payload, dict):
                for ens_name in msg.payload.keys():
                    address = msg.payload[ens_name]
                    self.network.add_route(ens_name, address)
            elif isinstance(msg.payload, tuple):
                self.network.add_route(msg.payload[0], msg.payload[1])

        elif msg_type == NetMsg.PropagateRoutingTable:
            if not self.input_runtime_manager_func:
                self.logger.warning(
                    'A non-runtime manager ensemble is attempting to '
                    'propagate its routing table.')

            # payload should include the ensemble name to send the routing table
            # to. This should really only be used on the runtime manager. The
            # entire routing table (a dictionary) is copied and sent to another
            # ensemble.
            recipient_ensemble_name = msg.payload
            routing_table_message = Message(NetMsg.AddRoutingTableEntry,
                                            self.network.routing_table,
                                            Recipient.ProcessNetwork)

            self.create_and_send_message(recipient_ensemble_name,
                                         routing_table_message)

            # Assume the device we're propagating the routing table to is new to
            # the network, so we should tell all other connected ensembles of
            # this new device by adding an entry to their routing table
            for destination_ens in list(self.network.routing_table.keys()):
                if (destination_ens == self.ensemble_name
                        or destination_ens == recipient_ensemble_name):
                    # why bother with an extra message that would be loopback or
                    # already be included in the full-table sent prior
                    continue

                routing_table_message = Message(
                    NetMsg.AddRoutingTableEntry,
                    (recipient_ensemble_name,
                     self.network.routing_table[recipient_ensemble_name]),
                    Recipient.ProcessNetwork)
                self.create_and_send_message(destination_ens,
                                             routing_table_message)

        elif msg_type == NetMsg.ForwardNetworkMessage:
            # Forward 1+ Messages to another ensemble. Useful for things like
            # SQ instantiation payload should be tuple of
            # (ensemble_name, Message | [*Message])
            recipient_ensemble_name = msg.payload[0]
            ipc_message_to_send = msg.payload[1]

            # if there are control messages, we need these to arrive. Use
            # assured delivery mechanisms.
            if isinstance(ipc_message_to_send, Message):
                require_confirmation = ipc_message_to_send.msg_type.is_control(
                )
            else:
                require_confirmation = any(
                    [msg.msg_type.is_control() for msg in ipc_message_to_send])

            self.create_and_send_message(recipient_ensemble_name,
                                         ipc_message_to_send,
                                         ensure=require_confirmation)

        elif msg_type == NetMsg.EndExecution:
            raise FinishedException

        else:
            self.logger.warning('Received message of unexpected type %s',
                                msg_type)

    def handle_output_arc(self, msg_token: TTNetworkMessage, source_sq_name):
        # If there is no output mapping, then this is an output arc.
        # Send it back to the runtime manager so it can be recorded alng
        # with its source SQ and ensemble.

        self.logger.debug(
            f'No mapping for {msg_token} -- most likely an output arc. '
            'Sending token back to RTM' if (
                self.output_func is None) else 'Writing token locally')

        # send token back to the runtime manager or locally write it
        # let's assume there is one runtime manager going by the default
        # name in our routing table
        runtime_manager_ensemble_name = RUNTIME_MANAGER_ENSEMBLE_NAME
        output_token_msg = Message(
            RuntimeMsg.LogOutputToken,
            (msg_token.payload['token'], source_sq_name, self.ensemble_name),
            Recipient.ProcessRuntimeManager)

        if self.output_func is None:
            self.create_and_send_message(runtime_manager_ensemble_name,
                                         output_token_msg)
        else:
            self.output_func(output_token_msg)

    def handle_network_message(self, message: TTNetworkMessage):
        '''
        Respond to an incoming message directly from the  May contain multiple
        messages, but they should be contained as Messages so the recipient
        process and functionalities are known

        :param message: The received message from the network

        :type message: TTNetworkMessage or a child class thereof
        '''
        payload = message.payload

        if isinstance(payload, bytearray) or isinstance(payload, bytes):
            # may have issues with runtime imports? what if the payload contains
            # something created in a custom namespace within an SQ? User could
            # fix be doing serialization/deserialization themselves in their
            # function.
            payload = pickle.loads(payload)
            self.logger.debug("handle %d byte network message %s",
                              len(message.payload), payload)

        assert isinstance(payload, list) or isinstance(
            payload, Message
        ), 'Incoming payloads over the network should be Messages (list or singular)'
        if isinstance(payload, list):
            for ipc_msg in payload:
                self.handle_IPC_message_from_network(ipc_msg)
        else:
            self.handle_IPC_message_from_network(payload)

    def handle_IPC_message_from_network(self, ipc_msg: Message):
        '''
        Slightly different than ``handle_IPC_message`` because these ones from
        the network may not be only for the NetworkManager. The
        ``Message`` contains a process recipient, so use that to forward the
        message appropriately.

        :param message: A ``Message`` that arrived within a
            ``TTNetworkMessage``

        :type message: ``Message``

        :return: None
        '''
        process_recipient = ipc_msg.process_recipient

        self.logger.debug(
            'Received an IPC message through the network interface: %s',
            ipc_msg)

        if process_recipient == Recipient.ProcessNetwork:
            self.handle_IPC_message(ipc_msg)

        elif process_recipient == Recipient.ProcessInputTokens:
            self.input_token_func(ipc_msg)

        elif process_recipient == Recipient.ProcessExecute:
            self.input_execute_func(ipc_msg)

        elif process_recipient == Recipient.ProcessRuntimeManager:
            if self.input_runtime_manager_func:
                self.input_runtime_manager_func(ipc_msg)
            else:
                self.logger.warning(
                    "Runtime manager message arrived on an ensemble "
                    "that is not a runtime manager: %s", ipc_msg)

        else:
            self.logger.error('Unrecognized Recipient: %s', process_recipient)
            raise ValueError('Unrecognized Recipient: %s', process_recipient)

    @staticmethod
    def generate_end_message():
        return Message(NetMsg.EndExecution, None, Recipient.ProcessNetwork)
