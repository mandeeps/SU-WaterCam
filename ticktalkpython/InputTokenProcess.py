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
The InputTokenProcess handles token waiting-and-matching by accepting new
tokens and passing them to SQs for synchronization and firing rule checks.
This process is implemented on a ``TTEnemble``, and only requires ``TTSQSync``
and ``TTClocks`` to perform its tasks, which includes SQ synchronization and,
in some cases, basic control flow for stream generation and deadline checking.
The process itself encapsulates these SQs and Clocks with good multiprocess
fundamentals to ensure consistency and memory safety. Most graph-processing
implementations are directly within the ``TTSQSync``.

All TT*Process classes follows the same design pattern. They implement a
singular input queue from which they read new ``Messages``, which
self-identify their function.  After processes are created, they exchange
interfacing information, primarily in the form of callback functions.
After configuring interfaces, the processes start. Each of these processes
spends its idle time waiting for new inputs within a 'run loop', responding
to messages as they arrive; the responses will modify internal process state
and produce new messages for other processes implemented on the Ensemble,
which 'owns' the processes.

'''

import queue
import math
import os

from . import ExecuteProcessInterface
from .TTToken import TTToken
from . import SQSync
from . import Clock
from . import Time
from .IPC import Message
from .IPC import ExecuteMsg
from .IPC import Recipient
from .IPC import SyncMsg
from .IPC import FinishedException
from .IPC import SendTokenListMessage
from .IPC import NetMsgToken
from .IPC import NetMsg
from . import DebugLogger

class TTInputTokenProcess():
    '''
    A process to manage incoming tokens, accepting them through a singular input
    queue. The process will listen to that queue, checking their tags for the SQ
    of interest. It will then check the firing rule against any present tokens
    for that tag and context. If the firing rule is triggered, then the relevant
    tokens are packaged into a ``TTExecutionContext`` and sent to the ensemble's
    ExecuteProcess, where they will be scheduled and executed. Exact behavior
    for token management depends strongly on the ``TTFiringRule``.

    '''

    def __init__(self,
                 input_queue,
                 ensemble_name=None,
                 wait_func=None,
                 wait_until_func=None):
        '''
        :param input_queue: An interprocess queue that serves inputs to this
            process; may be data, control, or management plane, but there is only
            one input queue

        :type input_queue: ``queue.Queue``

        :param output_execution_queue: An interprocess queue to send enabled
            execution contexts; this should feed into the execution process

        :type output_execution_queue: ``queue.Queue``

        :param sim: An optional parameter to provide access to the simulation
            environment

        :type sim: ``simpy.Environment``

        :param wait_func: A function to wait a set duration (in terms of the
            rootclock)

        :type wait_func: ``func``, args(duration, released_value, callback_func)

        :param wait_until_func: A function to wait until a particular time (in
            terms of the rootclock)

        :type wait_until_func: ``func``, args(clock, release time,
            released_value, callback_func)
        '''

        self.input_queue = input_queue

        self.wait = wait_func
        # example of canonical usage:
        # self.wait_until(self.root_clock, release_time, token, self.input_new_token)
        self.wait_until = wait_until_func

        self.ensemble_name = ensemble_name
        self.logger = DebugLogger.get_logger('InputTokenProcess-'+ensemble_name)

        self.sqs = {}
        self.clocks = []
        self.root_clock = None #assuming there is one clock tree with one root

        self.sim = None
        self.sim_process = None
        self.input_execute_func = None


    def setup_proc_intfc(self, input_execute_func, sim_process=None):
        '''
        Configure the interface to this process, meaning the callback functions
        for sending outputs to the ``TTExecuteProcess``

        :param input_execute_func: A callback function for providing
            ``Message`` inputs to the ``TTExecuteProcess``

        :type input_execute_func: function

        :param sim_process: A reference to the simulated process that this class
            runs inside of. Mainly used for interrupting the simulated variant on
            input messages. dDefaults to None

        :type sim_process: ``simpy.Process`` | None
        '''
        self.input_execute_func = input_execute_func

        self.sim_process = sim_process


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
        if (self.sim is not None and self.sim_process is not None
                and self.sim.active_process != self.sim_process):
            # self.logger.debug('Interrupting!: t=%f' % self.sim.now)
            self.sim_process.interrupt()


    def get_next_input(self):
        '''
        Pull the next input off the input queue.

        :return: If present, the next Message from the input queue
        :rtype: None | Message
        '''
        if self.sim is not None:
            return self.input_queue.get_nowait()
        else:
            # FIXME: timeout value should be more configurable
            return self.input_queue.get(block=True, timeout=1)


    def run_sim(self, sim):
        '''
        The main run loop for a runtime environment using simulated processes,
        which runs on a single core and can implement many ensembles. This will
        listen to the input queue and call a handler for any messages that
        arrive

        This must be instantiated using the sim.process() interface, as this
        function is technically a generator due to its usage of 'yield' (an
        essential component of simpy event processing)
        '''
        import simpy

        self.sim = sim
        self.logger.info('run sim loop InputTokens')
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
                    try:
                        self.handle_message(next_msg)
                    except BaseException:
                        raise
                next_msg = None

        except KeyboardInterrupt:
            raise
        except GeneratorExit:
            self.logger.info('simpy input token generator exited')
            raise
        except BaseException:
            raise

    def run_phy(self, execute_queue, ntwk_queue):
        '''
        The main run loop for a runtime environment using physical processes,
        which can take advantage of multi-core processors.

        It is expected that this will run in its own distinct
        ``multiprocess.Process`` (at the level of the OS with its own virtual
        memory).

        This will listen to the input queue and call a handler for any messages
        that arrive.

        '''
        # This must be a totally separate function than the simulated version
        # because the 'yield' statement converts that function into a generator,
        # which has substantially different behavior, especially in the context
        # of multiprocess. I tried, but this distinction must be made.
        self.logger.debug('run phy loop InputTokens')
        self.logger.debug(f'input token process is on pid {os.getpid()}')
        self.execute_queue = execute_queue
        self.ntwk_queue = ntwk_queue

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
            self.logger.exception('stopped ITP')
            raise

    def handle_message(self, msg):
        '''
        handle IPC (Inter Process Communication) messages arriving to this
        process via the singular input queue. This will include messages at the
        data, control, and management planes, which will have designators to
        specify how they should be handled (using process-specific enumeration)

        The input token process will handle messages related to synchronization
        of inputs for SQs, as well as control messages to instantiate, update,
        or remove SQs (only the synchronization portion). New tokens constitute
        the data layer interactions, in which they will be synchronized with
        other tokens incident on an SQ; when synchronization completes, a
        ``TTExecutionContext`` will be constructed and put into an outgoing
        queue to the ``TTExecuteProcess``.

        :param msg: A message read off of the input queue. This must be an
            ``Message`` with a msg_type of ``SyncMsg`` and process_recipient of
            ``ProcessInputTokens``, else it will be ignored without notification

        :type msg: Message

        :return: None; any 'return-like' behavior will produce an IPC message
            for another process
        '''
        if not isinstance(msg, Message):
            return
        if not isinstance(msg.msg_type, SyncMsg):
            return
        if not msg.process_recipient == Recipient.ProcessInputTokens:
            return

        msg_type = msg.msg_type
        self.logger.debug('New message %s', msg)

        if msg_type == SyncMsg.InputToken:
            token = msg.payload
            if isinstance(token.time, Time.TTTimeSpec):
                token.time = token.time.to_time(clock_list=self.clocks)

            self.logger.debug(f'Input token: {token}')
            self.find_sq_and_sync(token)
            # if synchronization completed, then a message will be send in the
            # output_execution_queue. If not, the token will be stored in
            # waiting-matching

        elif msg_type == SyncMsg.TimedInput:
            # use the delay function that should have been provided when the
            # process was created. Should either be a 'yield timeout(t)' or
            # 'time.time.sleep(t)'
            token = msg.payload
            if isinstance(token.time, Time.TTTimeSpec):
                token.time = token.time.to_time(clock_list=self.clocks)

            self.logger.debug('Timed input token: %s', token)
            release_time = token.time.stop_tick

            token_input_IPC_message = Message(SyncMsg.InputToken, token, Recipient.ProcessInputTokens)
            self.wait_until(self.root_clock,
                            release_time,
                            [token_input_IPC_message],
                            self.input_msg,
                            sim=self.sim)

        elif msg_type == SyncMsg.InstantiateSQ:
            sync_sq = msg.payload
            assert isinstance(
                sync_sq, SQSync.TTSQSync
            ), 'input message is for instantiating an SQ sync portion, but has the wrong type!'

            # The SQ may require some runtime instantiation, such as matching the
            # clock it expects to use for stream-generation to one that is
            # present in this process an connected to a hardware clock (i.e., it
            # can read the current time properly)
            sync_sq.instantiate_at_runtime(self.clocks)
            self.sqs[sync_sq.sq_name] = sync_sq

        elif msg_type == SyncMsg.UpdateFiringRule:
            raise NotImplementedError

        elif msg_type == SyncMsg.RemoveSQ:
            raise NotImplementedError
            # del self.sqs[msg.payload]

        elif msg_type == SyncMsg.AddClocks:
            new_clocks = msg.payload
            if isinstance(new_clocks, dict):
                new_clocks = list(new_clocks.values())
            assert isinstance(
                new_clocks, list
            ), 'The set of clocks to add should be in a list or dictionary!'

            for clk in new_clocks:
                if clk.is_root():
                    if self.root_clock is not None:
                        self.logger.warning("Root clock is getting overwritten!")

                    self.root_clock = clk
                    if self.sim is not None:
                        # this is hacky, but 1 tick should correspond to 1us
                        #
                        # the explicit 1,000,000 are not ideal, but they are
                        # otherwise present in defaults.. Hacky to handle it in
                        # this way.
                        # TODO: improve solution. Relevant functions
                        # are right here, as well as wait/wait_until in
                        # TimedEventProcess.
                        Clock.TTClock.__set_root_now__(
                            now_func=lambda: self.sim.now * 1000000,
                            ticks_per_second=1000000,
                            root=clk)
                    else:
                        # uses default 'now' function , which calls time.time().
                        # May require more customization here in physical case
                        Clock.TTClock.__set_root_now__(root = self.root_clock)

                    self.logger.debug('Added new root clock: %s',
                                      self.root_clock)
                    self.logger.debug("Current time on root clock: %d",
                                      self.root_clock.now())


            # must be a list of TTClocks
            self.clocks.extend(new_clocks)


        elif msg_type == SyncMsg.RemoveClocks:
            raise NotImplementedError

        elif msg_type == SyncMsg.UpdateClocks:
            raise NotImplementedError

        elif msg_type == SyncMsg.EndExecution:
            raise FinishedException


    def find_sq_and_sync(self, token):
        '''
        The provided token will now be compared to the other tokens currently
        stored for the SQ within the corresponding ``TTSQSync`` object which
        actually stores tokens and checks the firing rule. If it has a set of
        tokens that are ready to be executed on, it will return a list of them
        to be wrapped into a ``TTExecutionContext`` and sent to the
        ``TTExecuteProcess`` on this ensemble. These tokens are not returned; we
        complete synchronization here and forward the result to the
        ``TTExecuteProcess``

        :param token: The token to send to an SQ and check against the firing
            rule and extant tokens

        :type token: TTToken

        :return: None
        '''

        # find SQ of interest
        recipient_sq: SQSync.TTSQSync = self.sqs.get(token.tag.sq)
        if recipient_sq is None:
            raise ValueError("Token arrived for an SQ that does not exist!",
                             token)

        # pass onward to the SQ. Surely it knows what to do with it.
        execution_context, control_token = recipient_sq.receive_token(token)
        if (execution_context is not None
                and isinstance(execution_context,
                               ExecuteProcessInterface.TTExecutionContext)):

            execution_message = Message(
                ExecuteMsg.NewExecutionContext,
                execution_context,
                Recipient.ProcessExecute)
            self.execute_queue.put(execution_message)

        if control_token is not None and isinstance(control_token, TTToken):

            # send to self
            timed_control_token_message = Message(
                SyncMsg.TimedInput,
                control_token,
                Recipient.ProcessInputTokens)
            self.input_msg(timed_control_token_message)

            # need to skip the first iteration because we don't start on the
            # first control token coming in
            # syntax driven enabling
            # TODO: Implicit that last opp is timestamp, assigned in
            # CompilerRules
            if execution_context is not None and recipient_sq.has_exposed_control:
                self.logger.debug(
                    f'emitting periodic control token from SQ {recipient_sq.sq_name}'
                )
                dummy_payload = {
                    'token': control_token.copy_token(),
                    'source_sq': token.tag.sq
                }
                dummy_payload['token'].value = "shouldnt_occur"
                payload = {'token': control_token, 'source_sq': token.tag.sq}
                ntwk_msgs = [
                    NetMsgToken(NetMsg.EmptyToken, dummy_payload)
                    for _ in range(recipient_sq.n_input_ports)
                ] + [NetMsgToken(NetMsg.SendToken, payload)]
                self.ntwk_queue.put(
                    SendTokenListMessage(ntwk_msgs, Recipient.ProcessNetwork,
                                         token.tag.sq))

    @staticmethod
    def generate_end_message():
        return Message(SyncMsg.EndExecution, None, Recipient.ProcessInputTokens)
