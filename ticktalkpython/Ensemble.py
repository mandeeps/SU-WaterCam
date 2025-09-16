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
An ensemble is defined as a collection of (possibly simulated) hardware
elements like processor(s), memory, storage, network interfaces, sensors,
actuators, clocks, etc.
It is the catch-all term for a device in the system, though we call them
'ensembles' to better reflect their heterogeneous nature.

The ensembles can be used in simulated and physical environments without
relatively little difference from the user's perspective and none from the
program-development perspective. The TTEnsemble handles the top-level
mechanisms for the ensemble. The main roles include instantiating the
processes and their interfaces to each other.
One ensemble exists as a ``TTRuntimeManager``, which helps set up the
network among all devices and distribute the graph. That ensemble runs
an additional process to handle this management plane.

The Ensemble setup process is as follows:

*  Create a set of thread/process-safe communication channels (queues)
*  Create a set of processes for SQ synchronization, execution, and
   networking/forwarding
*  Configure the interfaces between each process so they can exchange
   information at run time
*  Set up the ``TTNetworkInterface`` (within the ``TTNetworkManager``)
*  Contact the Runtime Manager Ensemble to request this ensemble 'join' the
   network
*  Configure the network interface to hold routing information to the other
   Ensembles; this information is provided bythe Runtime Manager
*  Await incoming network messages in a 'steady state'; an example of a
   message is a new SQ to be instantiated on this ensemble
*  Perform SQ synchronization, execution, and forwarding as input tokens
   arrive as part of Graph Interpretation
*  Tear down processes and network interfaces after some timeout or s
   hutdown signal

'''

import time
import math
import multiprocess as mp
import os
import queue
import pickle

from . import NetworkInterfaceUDP
from .Component import TTComponent
from .Query import TTEnsembleInfo
from .Error import TTComponentError
from . import InputTokenProcess
from . import ExecuteProcess
from . import NetworkManager
from . import RuntimeManager
from . import TimedEventProcess
from .IPC import Message
from .IPC import NetMsg
from .IPC import Recipient
from .IPC import RuntimeMsg
from .Constants import RUNTIME_MANAGER_ENSEMBLE_NAME
from .Constants import get_readable_time
from .Profiler import run_func_profiled

from . import DebugLogger

logger = DebugLogger.get_logger('Ensemble')


class TTEnsemble():
    '''
    Each device in a TTPython graph interpretation environment is represented by
    an instance of ``TTEnsemble`` or an 'ensemble' in our parlance.

    An Ensemble also contains a collection of ``TTComponent`` instances
    representing the device's capabilities. These can be added, removed, and
    queried for using ``TTComponent`` instances. As such, a ``TTEnsemble``
    should be viewed as both the specification and realization of a
    TTPython-compliant device.

    Each ``TTEnsemble`` implements a set of communicating processes (i.e.,
    ``TTInputTokenProcess``, ``TTExecuteProcess``, and
    ``TTNetworkManager``), which follow the same paradigm: each reads
    data (in the form of ``Messages``) from a single input queue, and the
    data self-describes its function, which the process handles in turn; these
    processes handle nearly all runtime operations. ``TTEnsemble`` creates and
    manages these processes
    '''

    class PhysProcess:

        def __init__(self, process, cls, queue):
            self.process = process
            self.cls = cls
            self.queue = queue

    '''
    :param name: A **unique** name for the ensemble. This is used to globally
        refer to an ensemble in the system

    :type name: string

    :param is_runtime_mgr: An indicator to tell this ensemble to act as a
        runtime manager, which entails an additional runtime proceess
        (``TTRuntimeManagerProcess``)

    :type is_runtime_mgr: bool, optional
    '''

    def __init__(self, log_file_name, name, output_func, is_runtime_mgr=False):
        logger.info(f"Creating a new TTEnsemble: {name}")
        self.log_file_name = log_file_name
        self.sqs = []
        self.name = name
        self.output_func = output_func
        self.component_name_map = {}
        self.components = []
        self.sim = None
        self.logger = DebugLogger.get_logger(f'Ensemble({name})')
        self.is_runtime_mgr = is_runtime_mgr
        self.net_mgr_queue = None
        self.input_token_queue = None
        self.exec_queue = None
        self.runtime_mgr_queue = None
        self.in_token_proc = None
        self.address = None
        self.net_mgr_proc_handle = None
        self.in_token_proc_handle = None
        self.exec_proc_handle = None
        self.exec_proc = None
        self.runtime_mgr_proc_handle = None
        self.net_mgr_proc = None
        self.runtime_mgr_proc = None
        self.proc_pool = None

    def connect_to_TickTalk_network(
            self,
            runtime_manager_address,
            runtime_manager_name=RUNTIME_MANAGER_ENSEMBLE_NAME):
        '''
        Initiate a connection to the TickTalk network by sending a message to
        the Runtime Manager ensemble, whose address must be provided. To join
        the network, this ensemble will send a message over the network to the
        runtime manager, including its own address so the runtime manager can
        add that information to its routing table and propagate that information
        to all other connected ensembles. That version of the routing table will
        be propagated to this ensemble.

        :param runtime_manager_address: The address that the runtime manager can
            be access from over the network. In a simulated environment, this is
            simply a reference to that ensemble object (assuming the simulation
            environment is simpy, which runs in a single process). In a
            'physical' environment (i.e., using a real network interface), this
            should be a port and ip (ip:port, e.g. '127.0.0.1:8425')

        :type runtime_manager_address: TTEnsemble | string

        :param runtime_manager_name: The name of the runtime manager ensemble.
            Defaults to Constants.RUNTIME_MANAGER_ENSEMBLE_NAME

        :type runtime_manager_name: string
        '''

        # add the runtime manager to the routing table first
        # better get this right, or we'll fail to join entirely
        runtime_manager_routing_table_entry = (runtime_manager_name,
                                               runtime_manager_address)
        add_runtime_to_routing_table_msg = Message(
            NetMsg.AddRoutingTableEntry, runtime_manager_routing_table_entry,
            Recipient.ProcessNetwork)
        self.net_mgr_proc.input_msg(add_runtime_to_routing_table_msg)

        # send a message to said runtime manager to join the network; must be
        # sent over the network, so encapsulte Message in TTNetworkMessage
        self_info = TTEnsembleInfo(self.name, self.address, self.components)
        join_msg = Message(RuntimeMsg.JoinTickTalkSystem, self_info,
                           Recipient.ProcessRuntimeManager)
        network_msg_payload = (runtime_manager_name, join_msg)
        network_ipc_msg = Message(NetMsg.ForwardNetworkMessage,
                                  network_msg_payload,
                                  Recipient.ProcessNetwork)
        self.net_mgr_proc.input_msg(network_ipc_msg)

    def add_components(self, *components):
        '''
        Add one or more ``TTComponent`` instances to this ``TTEnsemble`` If a
        component is already present as a member of the ensemble, it will be
        ignored, unless it is a different instance or TTComponent with the same
        'name' property.

        :param components: a list of TTComponent objects.

        :type components: TTComponent

        '''
        for component in components:
            if isinstance(component, TTComponent):
                if component.name in self.component_name_map:
                    if id(component) != id(
                            self.component_name_map[component.name]):
                        raise TTComponentError("A different TTComponent named "
                                               f"{component.name} is already "
                                               "a part of this TTEnsemble.")
                else:
                    self.components.append(component)
                    self.component_name_map[component.name] = component
                    if len(component.children) > 0:
                        self.add_components(*component.children)
            else:
                raise TTComponentError(
                    "Only objects of type TTComponent can be added "
                    "to an Ensemble.")

    def find_component(self, query):
        '''
        :param query: a TTQuery object for a given device or devices.
        :type query: TTQuery
        '''
        result = []
        for component in self.components:
            if query.test(component):
                result.append(component)
        return result

    def pickle_to_file(self, path):
        # unclear why this is part of TTEnsemble..
        file = open(path, "wb")
        pickle.dump(self, file)
        file.close()

    def remove_component(self, component):
        '''
        :param component: the TTComponent instance to be removed
        :type component: TTComponent
        '''
        raise NotImplementedError(
            'remove_component is not yet implemented. '
            'When would we remove hardware from an ensemble?')

    def setup_queues(self, is_sim=False):
        '''
        Create the set of inter-process communication queues for this ensemble;
        there is one per process. In the simulated environment, we use the
        ordinary queue (which is faster than multiprocess.Queue due to
        virtual memory isolation). In the physical (non-simulated) environment,
        each process runs as a distinct process in the OS, so we use the
        multiprocess version of Queue.

        :param is_sim: a boolean indicator to tell whether this is a simulated
            runtime environment or not. Defaults to False

        :type is_sim: bool, optional

        :return: None
        '''
        if is_sim:
            q = queue.Queue
        else:
            q = mp.Queue

        self.net_mgr_queue = q()
        self.input_token_queue = q()
        self.exec_queue = q()
        if self.is_runtime_mgr:
            self.runtime_mgr_queue = q()
        else:
            self.runtime_mgr_queue = None

    def setup_simulation_processes(self, sim, delay):
        '''
        Setup the simulated processes for this ensemble, including one to handle
        & synchronize all arriving input tokens and another to schedule and
        execute enabled SQs.

        :param sim: A reference to a Simpy Environment, which is the backbone of
            simulation time and causality in the standalone graph simulator

        :type sim: ``simpy.Environment``

        :param delay: Specify a delay on when to send the token. This can be
            used to simualte a slow network.

        :type delay: int

        :return: None
        '''
        # These processes are implemented as threads within the same Python 3
        # process, unlike the 'phy' version which uses multiple processes at the
        # OS level. That has higher overhead, but can utilize multiple cores.
        # That is not an option here, since all ensembles need to access the
        # same simpy execution environment.
        if self.exec_queue is None:
            raise ValueError(
                'Queues should be setup before creating processes: '
                'call TTEnsemble.setup_queues first')

        self.sim = sim
        if self.sim is None:
            raise ValueError(
                'Simulation environment (with simpy) must be configured '
                'before creating the processes')

        self.address = self  # the address in the simulated version is actually

        # create the custom process objects
        self.in_token_proc = InputTokenProcess.TTInputTokenProcess(
            self.input_token_queue,
            ensemble_name=self.name,
            wait_func=TimedEventProcess.wait,
            wait_until_func=TimedEventProcess.wait_until)
        self.exec_proc = ExecuteProcess.TTEPSim(self.exec_queue,
                                                ensemble_name=self.name)
        self.net_mgr_proc = NetworkManager.TTNetworkManager(
            self.net_mgr_queue, delay=delay, ensemble_name=self.name)

        # start the processes in the simulation environment. They will not
        # run until self.sim.run() does
        self.in_token_proc_handle = self.sim.process(
            self.in_token_proc.run_sim(self.sim))
        ep_connectors = ExecuteProcess.EPConnectors(
            self.in_token_proc.input_msg, self.net_mgr_proc.input_msg)
        self.exec_proc_handle = self.exec_proc.generate_sim_process(
            ep_connectors, self.sim)
        # self.timed_event_process_handle =
        # self.sim.process(self.timed_event_process.run())
        self.net_mgr_proc_handle = self.sim.process(
            self.net_mgr_proc.run_sim(self.sim))

        # keep track of each process
        self.proc_pool = [
            self.in_token_proc_handle, self.exec_proc_handle,
            self.net_mgr_proc_handle
        ]

        # if this is a runtime manager, we need an additional process to handle
        # that management plane
        if self.is_runtime_mgr:
            self.runtime_mgr_proc = RuntimeManager.TTRuntimeManagerProcess(
                self.log_file_name,
                self.output_func,
                self.runtime_mgr_queue,
                ensemble_name=self.name)
            self.runtime_mgr_proc_handle = self.sim.process(
                self.runtime_mgr_proc.run_sim(self.sim))
            self.proc_pool.append(self.runtime_mgr_proc_handle)

        # Setup the process interfaces, primarily meaning the functions they
        # should call to pass values to each other. We do this after creating
        # them so they can hold onto a reference to their own process (mainly to
        # avoiding interrupting themselves and throwing RuntimeErrors)
        self.in_token_proc.setup_proc_intfc(
            self.exec_proc.input_msg, sim_process=self.in_token_proc_handle)

        # if this is a runtime manager, then setup that interface; that process
        # and the network manager directly communicate with each other
        if self.is_runtime_mgr:
            self.net_mgr_proc.setup_proc_intfc(
                self.in_token_proc.input_msg,
                self.exec_proc.input_msg,
                input_runtime_manager_func=self.runtime_mgr_proc.input_msg,
                sim_process=self.net_mgr_proc_handle)
            self.runtime_mgr_proc.setup_proc_intfc(
                self.net_mgr_proc.input_msg,
                sim_process=self.runtime_mgr_proc_handle)
        else:
            self.net_mgr_proc.setup_proc_intfc(
                self.in_token_proc.input_msg,
                self.exec_proc.input_msg,
                sim_process=self.net_mgr_proc_handle)

    def setup_physical_processes(self,
                                 network_ip,
                                 rx_network_port=NetworkInterfaceUDP.RX_PORT,
                                 tx_network_port=NetworkInterfaceUDP.TX_PORT):
        '''
        Setup the distinct processes that will manage the SQ synchronization
        (input tokens), execution, and network managemement. These are
        implemented to take advantage of multicore ensembles like a Jetson
        TX/TX2.

        They must communicate using queues, which need to be created and shared
        between them *before* starting the processes to prevent runtime errors
        related to memory sharing.

        Setting up these processes includes setting up the
        ``TTNetworkManager``, which uses a UDP interface by default. It's
        configuration requires a network IP and ports for transmit and receive

        :param network_ip: the IP (v4) address of this ensemble

        :type network_ip: string (format 255.255.255.255)

        :param rx_network_port: The port this ensemble will expect to receive
            inputs from the network on. Defaults to
            ``NetworkInterfaceUDP.RX_PORT``

        :type rx_network_port: int, optional

        :param tx_network_port: The port this ensemble will use to send inputs.
            Our implementation of a UDP stack includes handshaking and
            acknowledged delivery; using a single port helps accomplish this.
            Defaults to ``NetworkInterfaceUDP.TX_PORT``

        :type tx_network_port: int, optional

        :return: None
        '''
        # These processes do not directly share any memory, although the
        # ``InputTokenProcess`` and ``ExecuteProcess`` both use the same
        # syscalls to access the synchronized clock (of which the ``TTClocks``
        # derive their current timestamps from). This is an architectural
        # decision meant to provide consistency and easily extensible
        # interfaces. However, this abstraction does carry nontrivial overhead,
        # particularly in terms of how long it takes to send data between
        # processes (copying virtual memory, serializing objects,
        # context-switching at the OS level). Implementing these processes as
        # threads reduces context-switching and memory-sharing overhead, but
        # prevents efficient use of multi-core processors.
        if self.exec_queue is None:
            raise ValueError(
                'Queues should be setup before creating processes: '
                'call TTEnsemble.setup_queues first')

        self.address = network_ip + ':' + str(rx_network_port)

        # Create the process classes
        self.in_token_proc = InputTokenProcess.TTInputTokenProcess(
            self.input_token_queue,
            ensemble_name=self.name,
            wait_func=TimedEventProcess.wait,
            wait_until_func=TimedEventProcess.wait_until)
        self.exec_proc = ExecuteProcess.TTEPPhy(self.exec_queue,
                                                ensemble_name=self.name)
        self.net_mgr_proc = NetworkManager.TTNetworkManager(
            self.net_mgr_queue, ensemble_name=self.name)

        # enable local logging only if
        # 1. not the runtime manager
        # 2. `output_func` is specified (specified in `runens.py`)
        # TODO: `log_file_name` seems deprecated from how generalized
        # `output_func` is.
        if not self.is_runtime_mgr and self.output_func is not None:
            self.net_mgr_proc.change_output_func(self.output_func)

        # configure the interfaces BEFORE starting processes; for simulated,
        # setup the interfaces afterwards.

        if self.is_runtime_mgr:
            self.runtime_mgr_proc = RuntimeManager.TTRuntimeManagerProcess(
                self.log_file_name, self.output_func, self.runtime_mgr_queue,
                self.name)

        # Create the actual processes for the CPU/OS
        self.in_token_proc_handle = mp.Process(
            target=self.in_token_proc.run_phy, args=[self.exec_queue, self.net_mgr_queue])
        ep_connectors = ExecuteProcess.EPConnectors(
            self.input_token_queue, self.net_mgr_queue)
        self.exec_proc_handle = mp.Process(
            target=self.exec_proc.run_event_loop, args=[ep_connectors])
        self.net_mgr_proc_handle = mp.Process(
            target=self.net_mgr_proc.run_phy,
            args=[
                network_ip, rx_network_port, tx_network_port,
                self.in_token_proc.input_msg, self.exec_proc.input_msg,
                self.runtime_mgr_proc.input_msg if self.runtime_mgr_proc else None
            ])

        self.proc_pool = [
            self.PhysProcess(self.in_token_proc_handle, self.in_token_proc,
                             self.input_token_queue),
            self.PhysProcess(self.exec_proc_handle, self.exec_proc,
                             self.exec_queue),
            self.PhysProcess(self.net_mgr_proc_handle, self.net_mgr_proc,
                             self.net_mgr_queue)
        ]

        if self.is_runtime_mgr:
            self.runtime_mgr_proc_handle = mp.Process(
                target=self.runtime_mgr_proc.run_phy,
                args=[self.net_mgr_proc.input_msg])
            self.proc_pool.append(
                self.PhysProcess(self.runtime_mgr_proc_handle,
                                 self.runtime_mgr_proc,
                                 self.runtime_mgr_queue))
            self.runtime_mgr_proc_handle.start()

        # and start!
        self.in_token_proc_handle.start()
        self.exec_proc_handle.start()
        self.net_mgr_proc_handle.start()

        self.logger.info("Device started")

    def enter_steady_state(self, timeout=math.inf):
        '''
        Put the ensemble into a steady state after all processes have been
        spawned. This will let them receive and exchange messages through their
        IPC queues, but the processes will not return or join unless an error
        has occurred.

        This should NOT return unless a process fails or ends.

        :param timeout: The amount of time before the ensemble will end the
            processes and exit, defaults to no timeout (math.inf)

        :type timeout: float | int

        :return: None
        '''
        self.logger.debug('Begin steady state')
        if self.sim is not None:
            # the function contain s a 'yield', so it must be created as a
            # simulted process. We will return after this.
            self.sim.process(
                self._enter_steady_state_simulated(timeout=timeout))
        else:
            # This function will block until timeout expiry or an error is
            # triggered
            self._enter_steady_state_physical(timeout=timeout)

    def _enter_steady_state_simulated(self, timeout=math.inf):
        import simpy
        # Setup the ensemble to behave in a waiting state such that if a timeout
        # expires or an internal process fails, the ensemble will signal error
        # and exit for simulated environment, this means yieling to any

        if self.sim is not None:
            # using simulated version; yield on processes. Environment should
            # already be running (self.sim.run() called somewhere higher in the
            # simulation environment (graph simulator))
            try:
                self.logger.debug('Entering ensemble steady state (simulated)')
                # if any return, kill the other processes and destruct
                yield simpy.AnyOf(self.sim, [
                    simpy.events.AnyOf(self.sim, self.proc_pool),
                    self.sim.timeout(timeout)
                ])
                self.logger.debug('Exiting ensemble steady state(simulated)')

            except GeneratorExit:
                self.logger.debug('a simpy generator exited')
                raise

            except BaseException as e:
                self.logger.exception(e)
                raise

            finally:
                self.logger.warning("Shutting down child processes")
                for proc in self.proc_pool:
                    try:
                        proc.interrupt(
                            'Process returned --> Failure somewhere. Exit')
                    except (RuntimeError, KeyboardInterrupt):
                        self.logger.exception(
                            'Runtime error in Ensemble processing (1)')
                        pass
        else:
            raise ValueError("Using simulated mode in the steady state, "
                             "but a simulation environment does not exist!")

    def _enter_steady_state_physical(self, timeout=math.inf):
        # Setup the ensemble to behave in a waiting state such that if a timeout
        # expires or an internal process fails, the ensemble will signal error
        # and exit For physical environment, we'll simply poke the instantiated
        # proocess periodically to see if they're alive. If so, keep going (but
        # also check for timeout)
        self.logger.debug(f'top level process is on pid {os.getpid()}')
        if self.sim is None:
            # using real processes; suspend the main thread/process until
            # something fails
            start_time = time.time()
            try:
                self.logger.debug('Entering ensemble steady state')
                while True:
                    for phys_proc in self.proc_pool:
                        # longish timeout on awaiting join (only happens if
                        # process returns) to ensure we don't add much load
                        # to the processor
                        phys_proc.process.join(timeout=5)
                        if not phys_proc.process.is_alive():
                            self.logger.critical(
                                f'process {type(phys_proc.cls).__name__} has '
                                'died, closing TTPython')
                            raise mp.ProcessError
                        if time.time() >= start_time + timeout:
                            self.logger.critical(
                                f'Timeout of {timeout} secs hit '
                                f'(started at {get_readable_time(start_time)}, '
                                f'end at {get_readable_time(time.time())}); '
                                'exiting ensemble')
                            raise TimeoutError

            except TimeoutError:
                pass
            except KeyboardInterrupt:
                self.logger.critical(
                    '*****Keyboard interrupt; exiting steady state****')
            except:
                self.logger.exception(
                    'Runtime error in Ensemble processing (2)')
                raise
            finally:
                self.logger.warning('Exiting ensemble steady state')
                self.logger.warning("Shutting down child processes")
                for phys_proc in self.proc_pool:
                    proc_name = {type(phys_proc.cls).__name__}
                    try:
                        if phys_proc.process.exitcode is None:
                            self.logger.debug(f'{proc_name} send end msg')
                            end_msg = phys_proc.cls.generate_end_message()
                            phys_proc.queue.put(end_msg)
                        else:
                            self.logger.debug(f'{proc_name} already exited')
                    except:
                        self.logger.exception(
                            f'trouble with closing {proc_name}')

                time.sleep(0.5)

                for phys_proc in self.proc_pool:
                    proc_name = {type(phys_proc.cls).__name__}
                    self.logger.debug(f'joining {proc_name}')
                    phys_proc.process.join(timeout=2)
                    exit_code = phys_proc.process.exitcode
                    self.logger.debug(f'{proc_name} exited with {exit_code}')

                    if exit_code is None:
                        self.logger.warning(
                            f'has not exited: {proc_name}, terminating')
                        phys_proc.process.terminate()

        else:
            raise ValueError("Using physical mode in the steady state, but a "
                             "simulation environment exists!")

    def receive_network_message(self, message):
        # this is really only used by the simulated environment since we
        # directly share ensemble references in lieu of a real network interface
        self.net_mgr_proc.input_msg(message)
