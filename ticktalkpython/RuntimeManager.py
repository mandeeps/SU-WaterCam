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
A runtime manager is a higher level entity in the TickTalk system that serves to
coordinate the setup and teardown of the TickTalk system and runtime, including
notifying ``TTEnsembles`` of each other, generating a mapping (with
``TTMapper``) of the graph, distributing the SQs to ensembles, injecting initial
tokens into the system to kickstart graph interpretation and logging final
output tokens for future analysis. In other words, the runtime manager handles
the management plane of the system.

The ``TTRuntimeManager`` is effectively another ``TTEnsemble``, but differs in
that it implements an extra process, ``TTRuntimeManagerProcess``. In essence,
the TTRuntimeManager is really a wrapper for this process, and is best suited as
the user-facing device so the user can see other ensembles in the system connect
and personally trigger a graph to be instantiated and interpretation started
once the system is setup per their needs.
'''

import math
from abc import ABC
import queue
import os

from .Graph import TTGraph
from . import DebugLogger
from . import Mapper
from .SQ import TTSQ
from .SQExecute import TTSQExecute
from .TTToken import TTToken
from . import Clock
from . import Tag
from . import Time
from .IPC import Message
from .IPC import NetMsg
from .IPC import Recipient
from .IPC import RuntimeMsg
from .IPC import SyncMsg
from .IPC import ExecuteMsg
from .IPC import FinishedException
from .Constants import RUNTIME_MANAGER_ENSEMBLE_NAME
from .Query import TTEnsembleInfo

logger = DebugLogger.get_logger('RuntimeManager')

class TTRuntimeManager(ABC):
    '''
    An entity to manage the environment at runtime. The program starts from
    here, getting mapped to ensembles either dynamically or according to
    extant information within the SQs in the graph. This is technically an
    ensemble in that it has a network interface. Simulated and physical
    variants exist for this as child classes, similar to the network
    interfaces.

    The log file is used to record the tokens on the graph's output arcs.
    '''

    def __init__(self,
                 log_file_name,
                 output_func,
                 name=RUNTIME_MANAGER_ENSEMBLE_NAME):
        # the ensemble will go through ordinary setup procedures, which are
        # somewhat specific to the runtime environment (physical vs.
        # simulation)
        # TODO: Refactor this to not need a local import to avoid circular
        # dependency
        from . import Ensemble
        self.log_file_name = log_file_name
        self.output_func = output_func
        self.manager_ensemble = Ensemble.TTEnsemble(log_file_name,
                                                    name,
                                                    output_func,
                                                    is_runtime_mgr=True)

        # self.connected_ensembles = []
        # this is effectively a copy of the routing table, but may also
        # contain additional metadata about ensemble capabilities

    def send_to_runtime(self, msg):
        '''
        Only the runtime manager process will actually interact with the rest of
        the system; this simply serves as a proxy from the user-level
        environment (the main process on the machine hsoting the runtime
        manager)

        :param msg: The message to pass to the actual runtime manager process

        :type msg: Message
        '''
        self.manager_ensemble.runtime_mgr_proc.input_msg(msg)

    def instantiate_and_map_graph(self, graph: TTGraph):
        '''
        Signal the runtime manager process to instanatiate the graph for
        execution by generating a mapping to of SQs to ensembles and
        distributing those SQs accordingly

        :param graph: The graph representing a TTPython program to execute

        :type graph: TTGraph
        '''
        # TODO; allow a statically-produced mapping to be provided here as well.
        # In that case, the graph and mapping should be set as the payload in a
        # tuple (graph, mapping).
        graph_msg = Message(
            RuntimeMsg.InstantiateAndMapGraph,
            graph,
            Recipient.ProcessRuntimeManager)
        self.send_to_runtime(graph_msg)



class TTRuntimeManagerSim(TTRuntimeManager):
    '''
    A simulated runtime manager. Can directly access any reference to another
    ensemble, clock, SQ, etc.; uses a simulated network interface. This is is
    mainly used to configure the ensemble acting as the Runtime Manager

    :param ensembles: A list of the ensembles that compose the system. This may
        be empty, in the case where the other ensembles are created *after*
        the runtime manager starts (such that they join the TickTalk system as
        any physical ensemble would).

    :type ensembles: [TTEnsemble]
    '''
    def __init__(self,
                 log_file_name,
                 ensembles,
                 sim,
                 output_func,
                 delay=0,
                 name=RUNTIME_MANAGER_ENSEMBLE_NAME):
        super().__init__(log_file_name, output_func, name=name)
        self.ensembles = ensembles
        self.sim = sim

        self.manager_ensemble.setup_queues(is_sim=True)
        self.manager_ensemble.setup_simulation_processes(sim=self.sim,
                                                         delay=delay)
        # this will block the rest
        # of execution until an uncaught exception or KB interrupt occurs
        # self.manager_ensemble.enter_steady_state()

        ens_description = TTEnsembleInfo(RUNTIME_MANAGER_ENSEMBLE_NAME,
                                         self.manager_ensemble,
                                         self.manager_ensemble.components)

        add_self_to_routing_msg = Message(
                RuntimeMsg.JoinTickTalkSystem,
                ens_description,
                Recipient.ProcessRuntimeManager)
        self.send_to_runtime(add_self_to_routing_msg)


class TTRuntimeManagerPhysical(TTRuntimeManager):
    '''
    A runtime manager on a physical device; one ensemble will take on this
    coordination role.

    :param ip: The IPv4 address of the runtime manager. Must be accessible by
        all other ensembles that wish to join the system.

    :type ip: string

    :param rx_port: The port the runtime manager ensemble expects to receive
        input messages from

    :type rx_port: int

    :param tx_port: The port the runtime manager plans to use for sending
        outputs to other ensembles in the system

    :type tx_port: int
    '''
    def __init__(self,
                 ip,
                 rx_port,
                 tx_port,
                 log_file_name,
                 output_func,
                 name=RUNTIME_MANAGER_ENSEMBLE_NAME):
        super().__init__(log_file_name, output_func, name=name)
        self.manager_ensemble.setup_queues(is_sim=False)
        self.manager_ensemble.setup_physical_processes(
            network_ip=ip,
            rx_network_port=rx_port,
            tx_network_port=tx_port)
        # this will block the rest
        # of execution until an uncaught exception or KB interrupt occurs
        # self.manager_ensemble.enter_steady_state()

        ens_description = TTEnsembleInfo(RUNTIME_MANAGER_ENSEMBLE_NAME,
                                         f'{ip}:{rx_port}',
                                         self.manager_ensemble.components)

        # add self to the routing table
        add_self_to_routing_msg = Message(RuntimeMsg.JoinTickTalkSystem,
                                          ens_description,
                                          Recipient.ProcessRuntimeManager)
        self.send_to_runtime(add_self_to_routing_msg)


class TTRuntimeManagerProcess():
    '''
    A priveleged process included only on the runtime manager ensemble that can
    receive from and send into the ``TTNetworkManager`` local to itself.
    It is responsible for forwarding routing-table additions to all connected
    ensembles, mapping SQs from the graph (and sending the corresponding
    messages), sending initial input tokens to trigger graph execution, and
    logging output tokens.

    All TT*Process classes follows the same design patterns. They implement a
    singular input queue from which they read new ``Messages``, which
    self-identify their function.  After processes are created, they exchange
    interfacing information, primarily in the form of callback functions. After
    configuring interfaces, the processes start. Each of these processes spends
    its idle time waiting for new inputs within a 'run loop', responding to
    messages as they arrive; the responses will modify internal process state
    and produce new messages for other processes implemented on the Ensemble,
    which 'owns' the processes.

    :param input_queue: An input queue to serve new data (as ``Messages``) to
        this process

    :type input_queue: queue.Queue | multiprocess.Queue

    :param ensemble_name: The name of this ensemble

    :type ensemble_name: string
    '''

    def __init__(self, log_file_name, output_func, input_queue, ensemble_name=None):
        self.log_file_name = log_file_name
        self.output_func = output_func
        self.input_queue = input_queue
        # this is effectively a copy of the routing table, but may also contain
        # additional metadata about ensemble capabilities to inform mapping
        self.connected_ensembles = {}

        self.instantiated_graphs = {}

        self.ensemble_name = ensemble_name
        self.sim = None
        self.sim_process = None

        self.logger = DebugLogger.get_logger(
            f'RuntimeManager({ensemble_name})')
        self.input_network_func = None

    def setup_proc_intfc(self, input_network_func, sim_process=None):
        '''
        Configure the interface to this process, meaning the callback functions
        for sending outputs to the other processes. This process needs a
        callback for each other runtime process, as it may receive inputs for
        any other process through the network.

        :param input_network_func: A callback function for providing
            ``Message`` inputs to the ``TTNetworkManager``

        :type input_network_func: functiond process that this class runs inside
            of. Mainly used for interrupting the simulated variant on input
            messages. dDefaults to None

        :param sim_process: A reference to the simulated process that this class
            runs inside of. Mainly used for interrupting the simulated variant
            on input messages. Defaults to None

        :type sim_process: ``simpy.Process`` | None
        '''
        self.input_network_func = input_network_func
        self.sim_process = sim_process

    def input_msg(self, message):
        '''
        Callback use to provide messages to this process's input queue.

        If this is a simulated environment, we interrupt the process, which is
        otherwise waiting indefinitely for data to arrive on the queue.

        :param message: The message intended for this same ensemble

        :type message: Message
        '''
        self.input_queue.put(message)
        if (self.sim is not None and self.sim_process is not None
                and self.sim.active_process != self.sim_process):
            self.logger.log(2, 'Interrupting!: t=%f', self.sim.now)
            # would this generate too many interrupts if there are many inputs
            # all at one time?
            self.sim_process.interrupt()



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
        which runs on a single core and can implement many ensembles. Must be
        run as a ``simpy.Process``
        '''
        import simpy

        self.sim = sim
        self.logger.debug('run sim loop RuntimeManager')
        next_msg = None
        try:
            while True:
                try:
                    next_msg = self.get_next_input()
                except queue.Empty:
                    try:
                        yield self.sim.timeout(math.inf)
                    except simpy.Interrupt:
                        continue
                except simpy.Interrupt:
                    continue
                if next_msg is not None:
                    self.logger.debug(f'*** Message: {next_msg}')
                    self.handle_message(next_msg)
                next_msg = None
        except KeyboardInterrupt:
            raise
        except GeneratorExit as e:
            self.logger.debug('runtime simpy generator exited')
            raise e
        except BaseException as e:
            self.logger.exception(f'Base exception {e}')
            raise

    def run_phy(self, input_network_func):
        '''
        The main run loop for a runtime environment using physical processes,
        which can take advantage of multi-core processors.
        '''
        self.logger.debug('run phy loop RuntimeManager')
        self.logger.debug(f'runtime mgr is on pid {os.getpid()}')
        self.input_network_func = input_network_func
        try:
            while True:
                try:
                    next_msg = self.get_next_input()
                except queue.Empty:
                    next_msg = None

                if next_msg is not None:
                    self.handle_message(next_msg)

        except (FinishedException, KeyboardInterrupt):
            return
        except Exception:
            self.logger.exception(f'RTM has ended')
            raise
        finally:
            del self.input_network_func

    def handle_message(self, msg):
        '''
        Respond to an incoming message meant for this process. If the message
        type and recipient do not match expectations, this will return without
        notification
        '''

        if not isinstance(msg, Message):
            return
        if not isinstance(msg.msg_type, RuntimeMsg):
            return
        if not msg.process_recipient == Recipient.ProcessRuntimeManager:
            return

        msg_type = msg.msg_type
        self.logger.debug('New message %s', msg)

        if msg_type == RuntimeMsg.LogOutputToken:
            # user-defined output function to log
            self.output_func(msg)

        elif msg_type == RuntimeMsg.InstantiateAndMapGraph:
            # Instantiate the graph by mapping it to ensembles. Currently, that
            # mapping happens here at runtime, but it could be done statically
            # prior to this, so long as the set of ensembles in the expected
            # system match those that are actually connected by the time this
            # message arrives
            if not isinstance(msg.payload, tuple):
                graph:TTGraph = msg.payload
            else:
                # TODO: define the format; may be an already-mapped graph
                graph:TTGraph = msg.payload[0]

            assert isinstance(
                graph,
                TTGraph), 'Graph should be a TTGraph, output from the compiler'

            # FIXME: provide more mapping options
            mapped_sqs = Mapper.static_mapping(
                graph, list(self.connected_ensembles.values()))
            mapped_ports = Mapper.generate_mapping(graph, mapped_sqs)
            logger.debug(f'Mapped Ports: {mapped_ports}')

            # distribute the clocks to each ensemble. This is before sending SQs
            # because the SQ instantiation process often searches for a clock
            # that will be used for marking new TTTime's or setting local
            # timeouts. The clocks should already be known to those ensembles.
            # FIXME: only send the necessary clocks to each ensemble
            for ens_name in list(self.connected_ensembles.keys()):
                # send the default clock if none were specified in the program.
                clock_list = graph.clock_dictionary
                if 0 == len(clock_list):
                    # TODO: what are our default clocks? clock specification
                    # seems overengineered, ideally remove Clock notion
                    self.logger.warning(
                        'Clock Dictionary is empty. Execute at your own risk.')
                    clock_list = {'root_clock': Clock.TTClock.root()}

                msg_clocks_sync = Message(
                    SyncMsg.AddClocks, list(clock_list.values()),
                    Recipient.ProcessInputTokens)
                msg_clocks_execute = Message(
                    ExecuteMsg.AddClocks,
                    list(clock_list.values()),
                    Recipient.ProcessExecute)
                # Should the network manager have any knowledge of clocks?
                # potential TODO.
                network_payload = (ens_name,
                                   [msg_clocks_sync, msg_clocks_execute])
                network_msg = Message(NetMsg.ForwardNetworkMessage,
                                      network_payload,
                                      Recipient.ProcessNetwork)
                self.logger.info('Send clocks to Ensemble(%s)', ens_name)
                self.input_network_func(network_msg)
            self.logger.debug('Done sending clocks')

            # for each SQ, make a message to send the sync and execute parts.
            # Arc destinations should be held in the output_arc's list of
            # destinations, which go into the SQForward. Send the 3 messages to
            # the same recipient ensemble (all wrapped into an array of
            # Messages)
            for this_sq in graph.sqs:
                assert isinstance(
                    this_sq, TTSQ), 'graph.sq_list should only contain TTSQ\'s'
                # key is SQ name, value is the name of the ensemble it should
                # be mapped to
                ensemble_name = mapped_sqs[this_sq.sq_name]
                self.logger.debug(f'sending SQ {this_sq} to {ensemble_name}')

                msg_instatiate_sync = Message(
                    SyncMsg.InstantiateSQ, this_sq.generate_runtime_sqsync(),
                    Recipient.ProcessInputTokens)
                msg_instantiate_execute = Message(ExecuteMsg.InstantiateSQ,
                                                  TTSQExecute(this_sq),
                                                  Recipient.ProcessExecute)
                msg_instantiate_forwarding = Message(
                    NetMsg.InstantiateSQ,
                    (this_sq.sq_name, mapped_ports[this_sq]),
                    Recipient.ProcessNetwork)

                network_payload = (ensemble_name, [
                    msg_instatiate_sync, msg_instantiate_execute,
                    msg_instantiate_forwarding
                ])
                network_msg = Message(NetMsg.ForwardNetworkMessage,
                                      network_payload,
                                      Recipient.ProcessNetwork)
                self.input_network_func(network_msg)


            self.instantiated_graphs[graph.graph_name] = (graph, mapped_sqs)


        elif msg_type == RuntimeMsg.ExecuteGraphOnInputs:
            # Start execution of the graph by sending the set of provided inputs
            # to all SQs that receive from graph inputs. Tokens will be produced
            # and percolate throughout the graph. Expected format is a graph and
            # a dictionary whose keys are input-arc symbols and values are
            # initial token values.
            graph = msg.payload[0]
            assert isinstance(graph, TTGraph)
            input_dict = msg.payload[1]
            graph_name = graph.graph_name

            _, mapping = self.instantiated_graphs.get(graph_name)

            self.logger.info(
                f"Starting execution of graph '{graph_name}' "
                f"with inputs {input_dict}", )

            arg_len = len(input_dict)
            params = graph.source_var_names()
            param_len = len(params)
            # check inputs vs. the input arcs
            assert arg_len == param_len, (
                f'The GRAPHified function {graph.graph_name} '
                f"has {param_len} parameters ({', '.join(params)}) "
                f"where only {arg_len} was provided "
                f"({(', ').join(input_dict)})")

            ipp_to_sq = graph.get_ipp_to_sq_dict()

            for argument in input_dict.keys():
                if argument not in params:
                    raise ValueError(f"Argument '{argument}' not found in "
                                     f"parameter list ({', '.join(params)}). "
                                     'kwargs currently not supported.')

            self.logger.debug('Input check passed')

            # find root clock for initial time values
            root_clock = None
            for clock_name in graph.clock_dictionary.keys():
                clock = graph.clock_dictionary.get(clock_name)
                if clock.is_root():
                    root_clock = clock

            # just assign to root clock if none is found
            if root_clock is None:
                self.logger.warning('No root clock specified, '
                                    'defaulting to the root clock.')
                root_clock = Clock.TTClock.root()

            # initial inputs carry infinite timestamps -- synchronization will
            # be trivial
            clock_spec = Clock.TTClockSpec.from_clock(root_clock)
            base_time = Time.TTTimeSpec.infinite(clock_spec)

            for argument, input_value in input_dict.items():
                dest_sqs = ipp_to_sq[argument]

                # create a token; we'll replicate it for each SQ
                base_tag = Tag.TTTag(context=Tag.DEFAULT_CONTEXT_ID)
                base_token = TTToken(input_value,
                                     base_time,
                                     is_streaming=False,
                                     tag=base_tag)

                for dest, port_num in dest_sqs:
                    # one output arc may have be used more than once in the same
                    # downstream SQ. We support this.

                    # duplicate the token and set tag components for where
                    # exactly this token should go
                    token_to_send = base_token.copy_token()
                    token_to_send.tag.sq = dest.sq_name
                    token_to_send.tag.p = port_num
                    token_to_send.tag.e = mapping[dest.sq_name]

                    # create a message to carry this token into the network
                    # interface on this ensemble then into the
                    # synchronization process on the recipient ensemble.
                    token_input_message = Message(
                        SyncMsg.InputToken, token_to_send,
                        Recipient.ProcessInputTokens)
                    network_msg_payload = (mapping[dest.sq_name],
                                            token_input_message)
                    token_input_network_message = Message(
                        NetMsg.ForwardNetworkMessage, network_msg_payload,
                        Recipient.ProcessNetwork)

                    self.input_network_func(token_input_network_message)

        elif msg_type == RuntimeMsg.JoinTickTalkSystem:
            # an ensemble has asked to join the network. It's request includes
            # its name and the address it prefers to receive on (this is used to
            # add an entry to the routing table)
            ensemble_info = msg.payload
            # what does this message actually contain? Must at least include a
            # name and routing information (in sim, a TTEnsemble reference; in
            # phy, a network address), a TTEnsembleInfo object
            self.connected_ensembles[ensemble_info.name] = ensemble_info

            # add this ensemble to the routing table
            add_to_routing_table_message = Message(
                NetMsg.AddRoutingTableEntry,
                (ensemble_info.name, ensemble_info.address),
                Recipient.ProcessNetwork)
            self.input_network_func(add_to_routing_table_message)

            # propagate the rest of the routing table
            propagate_routing_table_message = Message(
                NetMsg.PropagateRoutingTable,
                ensemble_info.name,
                Recipient.ProcessNetwork)
            self.logger.info('Sending PropagateRoutingTable message to '
                             f"device '{ensemble_info.name}'")
            self.input_network_func(propagate_routing_table_message)

        elif msg_type == RuntimeMsg.EndExecution:
            raise FinishedException

    @staticmethod
    def generate_end_message():
        return Message(RuntimeMsg.EndExecution, None, Recipient.ProcessRuntimeManager)
