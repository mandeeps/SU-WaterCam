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

The ``TTExecuteProcess`` is one of the primary processes that handles
runtime mechanisms on a ``TTEnsemble``; specifically, this handles the
execution portion of a ``TTSQ``. It implements the ``TTSQExecute``
portion, which runs the code within the SQ. When an SQ has executed,
this process sends the output token to the ``TTNetworkManager``,
where it will be forwarded to downstream SQs. For some types of SQs/Firing
Rules, this SQ will also send a control token back to the
``TTInputTokenProcess``

All TT*Process classes follows the same design pattern. They implement
a singular input queue from which they read new ``Messages``, which
self-identify their function.  After processes are created, they exchange
interfacing information, primarily in the form of callback functions. After
configuring interfaces, the processes start. Each of these processes spends
its idle time waiting for new inputs within a 'run loop', responding to
messages as they arrive; the responses will modify internal process state
and produce new messages for other processes implemented on the Ensemble,
which 'owns' the processes.

'''

import queue
import math

from abc import ABC
from abc import abstractmethod
from . import SQExecute
from . import Clock
from . import DebugLogger
from .IPC import Message
from .IPC import ExecuteMsg
from .IPC import Recipient
from .IPC import NetMsg
from .IPC import SyncMsg
from .IPC import SendTokenListMessage
from .IPC import NetMsgToken
from .IPC import FinishedException
from .Engine import TTExecutingEngine, EngineOutput
from .EngineSimulated import SimulatedEngine
from .ExecuteProcessInterface import TTExecutionContext

from .Empty import TTEmpty

logger = DebugLogger.get_logger('executeProcess')

import os


class EPConnectors:

    def __init__(self, itp_queue, ntwk_queue):
        '''
        :param itp_func: A callback function for providing
            ``Message`` inputs to the ``TTInputTokenProcess``

        :type itp_func: function

        :param ntwk_func: A callback function for providing
            ``Message`` inputs to the ``TTNetworkManager``

        :type ntwk_func: function
        '''
        self.itp_queue = itp_queue
        self.ntwk_queue = ntwk_queue


class TTExecuteProcess(ABC):
    '''
    A process to handle the execution of SQ's specifically the ``TTSQExecute``
    portion. This process is owned and managed by the ``TTEnsemble``.

    The process maintains a dictionary of these and the relevant clocks; these
    are populated as SQs are instantiated at runtime. It is fed data via an
    input queue from other processes (``TTNetworkManager`` for instantiating
    clocks & SQs and ``TTInputTokenProcess`` for sets of inputs to execute an
    SQ on).

    This process also maintains a singular root clock that it uses to read
    'real' time.

    :param input_queue: The input queue that this process will receive inputs on

    :type input_queue: ``queue.Queue`` | ``multiprocess.Queue``
    '''

    def __init__(self, input_queue, ensemble_name=None):
        self.input_queue = input_queue
        # dictionary of sqs; the sq name is the key, and the value is a
        # TTSQExecute object
        self.sqs = {}
        self.clocks = []
        # perhaps we should generalize this? The root_clock has a 'now' function
        # to read the current time. We may want to instead just have a clock
        # associated with each SQ (that needs one).
        self.root_clock = None

        self.ensemble_name = ensemble_name
        self.logger = DebugLogger.get_logger('ExecutionProcess-' +
                                             ensemble_name)

        self.engine = None

    def remove_sq(self, sq_name):
        '''
        Evict an SQ from the process; use the SQ's name to identify it

        :param sq_name: The name of an SQ

        :type sq_name: string
        '''
        if sq_name in self.sqs:
            del self.sqs[sq_name]

    def get_sq(self, sq_name):
        '''
        Retreive an SQ based on its name

        :param sq_name: The name of an SQ

        :type sq_name: string

        :return: The SQ of interest; return None if not present

        :rtype: TTSQExecute | None
        '''
        return self.sqs.get(sq_name, None)

    def handle_message(self, msg):
        '''
        This will handle IPC (Inter Process Communication) messages arriving to
        this process via the singular input queue. This will include messages at
        the data, control, and management planes, which will have designators to
        specify how they should be handled (using process-specific enumeration)

        The execution process will handle messages to instantiate/remove SQs
        (only the execution portion) and to run an SQ's execution section on a
        ``TTExecutionContext`` received from the synchronization (input token)
        process. When this completes, it will stamp tokens with a new tag and
        send to the network manager process, which will communicate them to
        downstream SQs on whichever ensembles they are mapped

        :param msg: A message read off of the input queue. This must be an
            ``Message`` with a msg_type of ``ExecuteMsg`` and
            process_recipient of ``ProcessExecute``, else it will be ignored without
            notification

        :type msg: Message

        :return: None; any 'return-like' behavior will produce an IPC message
            for another process
        '''
        if not isinstance(msg, Message):
            return
        if not isinstance(msg.msg_type, ExecuteMsg):
            return
        if not msg.process_recipient == Recipient.ProcessExecute:
            return

        msg_type = msg.msg_type
        self.logger.debug('New message %s', msg)

        if msg_type == ExecuteMsg.NewExecutionContext:
            execution_context = msg.payload
            assert isinstance(execution_context,
                              TTExecutionContext), 'Not an execution context'
            execution_context.rereference_token_times(self.clocks)
            # execute on the named SQ within the execution_context on the
            # inputs provided
            self.submit_job(execution_context)

        elif msg_type == ExecuteMsg.StatefulExecutionContext:

            execution_context = msg.payload
            # FIXME: anything extra to do for stateful? This may be an
            # overspecification
            self.submit_job(execution_context)

        elif msg_type == ExecuteMsg.InstantiateSQ:
            if not isinstance(msg.payload, SQExecute.TTSQExecute):
                # not acutally in use..
                sq_execute = SQExecute.TTSQExecute.from_json(msg.payload)
            else:
                sq_execute = msg.payload

            # Message for SQ instantiation at the ExecutionProcess must be
            # of type SQExecute.TTSQExecute
            assert isinstance(sq_execute, SQExecute.TTSQExecute), (
                'Message must be of type TTSQExecute; '
                f'was {type(sq_execute)}')

            # The sq needs to be provided some instantiation information at
            # runtime, including clocks this is also when we prepare the SQ
            # for invocation by setting up a namespace and analyzing keyword
            # argments
            self.logger.info('Instantiate SQ %s', sq_execute.sq_name)

            sq_execute.instantiate_at_runtime(self.clocks)
            self.sqs[sq_execute.sq_name] = sq_execute
            self.engine.add_sq(sq_execute)

        elif msg_type == ExecuteMsg.UpdateCode:
            raise NotImplementedError

        elif msg_type == ExecuteMsg.RemoveSQ:
            raise NotImplementedError

            # del self.sqs[msg.payload]

        elif msg_type == ExecuteMsg.AddClocks:
            # add a set of clocks to be held by this process; ideally, this is
            # identical to what the InputTokenProcess has
            new_clocks = msg.payload
            for clk in new_clocks:
                if clk.is_root():
                    if self.root_clock is not None:
                        self.logger.warning(
                            "Root clock is getting overwritten!")

                    self.root_clock = clk

                    self.engine.set_root_clock(clk)
                    self.set_root_clock(clk)

                    self.logger.debug('Added new root clock: %s',
                                      self.root_clock)
                    self.logger.debug("Current time on root clock: %d",
                                      self.root_clock.now())

            # must be a list of TTClocks. Should this check for duplicates and
            # only append new ones?
            self.clocks.extend(new_clocks)

        elif msg_type == ExecuteMsg.RemoveClocks:
            # There is not yet a case for this. It may involve traversing the
            # set of SQs and making sure they do not carry a reference to the
            # removed clock
            raise NotImplementedError

        elif msg_type == ExecuteMsg.UpdateClocks:
            # There is not yet a case for this. We assume TTClocks are
            # effectively immuatable at runtime (even if there are no
            # protections...)
            raise NotImplementedError

        elif msg_type == ExecuteMsg.EndExecution:
            raise FinishedException

    def send_tokens(self, engine_output: EngineOutput):
        net_msgs = []

        for payload in engine_output.ntwk_payloads:
            token = payload['token']
            # enable conditional sending
            if isinstance(token.value, TTEmpty):
                token_type = NetMsg.EmptyToken
            else:
                token_type = NetMsg.SendToken

            net_msgs.append(NetMsgToken(token_type, payload))

        # TODO: exposes control and data arcs together
        # we need a better abstraction here
        for feedback_sequence_token in engine_output.itp_tokens:
            if feedback_sequence_token is not None:
                # ensemble name shouldn't be exposed to the engine
                feedback_sequence_token.tag.e = self.ensemble_name

                feedback_token_msg = Message(SyncMsg.InputToken,
                                             feedback_sequence_token,
                                             Recipient.ProcessInputTokens)
                self.ep_connectors.itp_queue.put(feedback_token_msg)

        self.ep_connectors.ntwk_queue.put(
            SendTokenListMessage(net_msgs, Recipient.ProcessNetwork,
                                 engine_output.source_sq_name))

    def cleanup(self):
        self.engine.cleanup()

    @abstractmethod
    def run_event_loop(self):
        ...

    @abstractmethod
    def set_root_clock(self):
        ...

    @abstractmethod
    def submit_job(self, ex_ctx: TTExecutionContext):
        ...

    @staticmethod
    def generate_end_message():
        return Message(ExecuteMsg.EndExecution, None, Recipient.ProcessExecute)


class TTEPSim(TTExecuteProcess):

    def __init__(self, input_queue, ensemble_name):
        super().__init__(input_queue, ensemble_name)
        self.engine = SimulatedEngine(self.root_clock)

    def set_root_clock(self, clk):
        # the explicit 1,000,000 are not ideal, but they are
        # otherwise present in defaults.. Still hacky to handle
        # it in this way. TODO: improve solution. Relevant
        # functions are right here, as well as wait/wait_until
        # in TimedEventProcess.
        Clock.TTClock.__set_root_now__(now_func=lambda: self.sim.now * 1000000,
                                       ticks_per_second=1000000,
                                       root=clk)

    def generate_sim_process(self, ep_connectors, sim):
        self.ep_connectors = ep_connectors
        self.sim = sim
        self.sim_process = sim.process(self.run_event_loop(self.sim))
        return self.sim_process

    def input_msg(self, message):
        '''
        Input an ``Message`` to this process. If running in a simulation
        environment, this will interrupt

        :param message: A message to provide to this process. Does not need to
            be called within the same process (i.e. it is not only thread-safe
            but inter-process safe)

        :type message: Message
        '''
        self.input_queue.put(message)
        if (self.sim and self.sim_process
                and self.sim.active_process != self.sim_process):
            self.sim_process.interrupt()

    def get_next_input(self):
        '''
        Pull the next input off the input queue.
        '''
        return self.input_queue.get_nowait()

    def run_event_loop(self, sim):
        '''
        The main run loop for a runtime environment using simulated processes,
        which runs on a single core and can implement many ensembles. This will
        listen to the input queue and call a handler for any messages that
        arrive

        This must be instantiated using the sim.process() interface, as this
        function is technically a generator due to its usage of 'yield' (an
        essential component of simpy event processing)

        This will listen to the input queue and call a handler for any messages
        that arrive.
        '''
        import simpy

        self.logger.info('run sim loop Execute')

        next_msg = None
        try:
            while True:
                try:
                    next_msg = self.get_next_input()

                except queue.Empty:
                    try:
                        # we wait infinitely because an arriving input should
                        # simpy interrupt the process
                        yield self.sim.timeout(math.inf)
                    except simpy.Interrupt:
                        continue
                except simpy.Interrupt:
                    # this should not actually occur since simpy is a
                    # single-threaded event loop and get_next_input does not
                    # wait
                    continue

                if next_msg is not None:
                    self.handle_message(next_msg)

                next_msg = None

        except KeyboardInterrupt:
            raise
        except GeneratorExit as e:
            self.logger.info('simpy execute generator exited')
            raise e
        except BaseException:
            self.logger.exception('Caught BaseException')
            raise

    def submit_job(self, ex_ctx: TTExecutionContext):
        engine_output = self.engine.submit_job(ex_ctx)
        self.send_tokens(engine_output)


class TTEPPhy(TTExecuteProcess):

    def __init__(self, input_queue, ensemble_name):
        super().__init__(input_queue, ensemble_name)

    def set_root_clock(self, root_clock):
        # uses default 'now' function , which calls
        # time.time().  May require more customization here in
        # physical case
        Clock.TTClock.__set_root_now__(root=root_clock)

    def input_msg(self, message):
        '''
        Input an ``Message`` to this process. If running in a simulation
        environment, this will interrupt

        :param message: A message to provide to this process. Does not need to
            be called within the same process (i.e. it is not only thread-safe
            but inter-process safe)

        :type message: Message
        '''
        self.input_queue.put(message)

    def get_next_input(self):
        '''
        Pull the next input off the input queue.
        '''
        return self.input_queue.get(timeout=0.01)

    def run_event_loop(self, ep_connectors):
        '''
        The main run loop for a runtime environment using physical processes,
        which can take advantage of multi-core processors.

        It is expected that this will run in its own distinct
        ``multiprocess.Process`` (at the level of the OS with its own virtual
        memory).

        This will listen to the input queue and call a handler for any messages
        that arrive.

        '''
        self.logger.debug('run phy loop Execute')
        self.logger.debug(f'exec process is on pid {os.getpid()}')
        self.ep_connectors = ep_connectors
        self.engine = TTExecutingEngine(self.root_clock)

        try:
            while True:
                try:
                    next_msg = self.get_next_input()
                except queue.Empty:
                    next_msg = None

                if next_msg is not None:
                    self.handle_message(next_msg)

                engine_outputs = self.engine.get_finished_jobs()
                for output in engine_outputs:
                    self.send_tokens(output)

        except (FinishedException, KeyboardInterrupt):
            return
        except Exception:
            self.logger.exception('EP has stopped')
            raise
        finally:
            self.cleanup()

    def submit_job(self, ex_ctx: TTExecutionContext):
        return self.engine.submit_job(ex_ctx)
