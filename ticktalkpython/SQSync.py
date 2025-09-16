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
The TTSQSync encapsultes the synchronization mechanisms for SQs, which is one of
the most essential and novel parts of the timed dataflow graph computation model
TTPython uses.

Traditionally, dataflow uses synchronization barriers to ensure the right values
are present before executing the code on them. We expand this by adding time,
such that we generate streams of data and label tokens with time-intervals,
where an overlap in time between tokens establishes a notion of concurrency that
we use to evaluate the firing rule in most cases. However, there are other types
of firing rules and add-on mechanisms like deadlines that increase complexity.
The firing rule itself may require parameterization to set how to behave when a
timeout occurs or when to rerun the SQ to generate a periodic stream of data. In
this way, the synchronization portion of an SQ performs the bulk of the work at
the control layer, in that it decides when and on what data an SQ should
execute.

The TTSQSync implements the firing rule and a 'Waiting-Matching' section that
stores inputs until they are ready for use
'''

from enum import Enum
from intervaltree import Interval
from intervaltree import IntervalTree

from .Time import TTTime
from .Time import TTTimeSpec
from .TTToken import TTToken
from .Clock import TTClock, TTClockSpec
from .FiringRule import TTFiringRule
from .FiringRule import TTFiringRuleType
from . import Time
from . import Tag
from . import ExecuteProcessInterface
from . import DebugLogger
from .Constants import get_readable_time

logger = DebugLogger.get_logger('SQSync')


class TTSQSync:
    '''
    The part of an SQ engaged in token synchronization. It will accept new
    tokens tagged with this SQ's name/ID. It maintains storage for tokens, and
    upon accepting new ones, will check against the ``TTFiringRule`` to see if
    there are sufficient tokens to fire. If so, it pulls the tokens from
    storage, wraps them into an executable context, sends it onward to the
    scheduler. If it is not ready to fire, it stores the token until it is ready
    to be used later.

    NB: this will be added onto as necessary to account for more types of firing
    rules and, perhaps, more efficient structures for storing tokens for that
    type of rule. We may also have to modify this to support multiple
    application contexts (that, or duplicate SQs, which is harder to manage)

    :param firing_rule: A description of the firing rule, using an enumerated
        type

    :type firing_rule: TTFiringRule

    :param n_input_ports: The number of input ports associated with this SQ

    :type n_input_ports: int

    :param is_streaming: An indicator for whether this SQ will processing a
        stream of tokens or not. This is used to determine if the tokens that
        arrive on ports should be 'Sticky' or not (meaning they persist longer
        than one invocation). Defaults to False

    :type is_streaming: bool

    :param use_deadline: An indicator whether or not this SQ uses a deadline.
        The exact semantics of deadlines are TBD. Defaults to False

    :type use_deadline: bool
    '''

    def __init__(self,
                 firing_rule,
                 n_input_ports,
                 sq,
                 is_streaming=False,
                 has_exposed_control=False,
                 is_singleton=False,
                 use_deadline=False):
        assert isinstance(firing_rule, TTFiringRule)
        self.firing_rule = firing_rule
        self.n_input_ports = n_input_ports
        self.sq_name = sq.sq_name
        self.is_streaming = is_streaming
        self.has_exposed_control = has_exposed_control

        self.unfired_deadlines = {}
        self.has_delayed = False

        # TODO: may want to make this into a dictionary of token-storages based
        # on the application context
        self.token_store = TTSQInputTokenStorage(n_input_ports,
                                                 firing_rule,
                                                 sq,
                                                 is_streaming=is_streaming,
                                                 is_singleton=is_singleton,
                                                 use_deadline=use_deadline)

    # TODO: lacks new experimental fr_types
    @staticmethod
    def from_json(json_in):
        '''
        Create from the 'sync' portion of the SQ's specification in JSON
        (represented as a dictionary)
        '''
        try:
            sq_name = json_in['sq_name']
            # See SQ.TTSQ.json_firing_rule
            firing_rule = json_in['firing_rule']
            fr_type = firing_rule['type']
            if fr_type == 'strict':
                firing_rule = TTFiringRule(TTFiringRuleType.Strict)
            elif fr_type == 'timed':
                firing_rule = TTFiringRule(TTFiringRuleType.Timed)
            elif fr_type == 'timed_retrigger':
                firing_rule = TTFiringRule(TTFiringRuleType.TimedRetrigger)
            elif fr_type == 'sequential_retrigger':
                firing_rule = TTFiringRule(
                    TTFiringRuleType.SequentialRetrigger)
            else:
                raise KeyError(f'Unknown firing rule {fr_type}')

            # is it possible to have keyword arguments that would invalidate
            # this?
            n_input_ports = len(json_in['input_ports'])

            if 'streaming' in json_in:
                is_streaming = json_in['is_streaming']
            else:
                is_streaming = False
            return TTSQSync(firing_rule,
                            n_input_ports,
                            sq_name,
                            is_streaming=is_streaming)
        except KeyError as err:
            logger.error(err)
            raise KeyError(f'Unable to convert json into TTSQSync: {json_in}')

    def create_execution_context(self, tokens, time_overlap, est_runtime=0):
        '''
        Creeate an execution context for the ``TTExecuteProcess`` to accept
        and execute this SQ on.

        :param tokens: The set of tokens to execute on

        :type tokens: list(``TTTokens``)

        :param time_overlap: A time interval describing the overlap in Time
            tags on all the ``tokens``

        :type time_overlap: ``TTTime``

        :param est_runtime: An estimate of the runtime

        :type est_runtime: int
        '''
        exec_context = ExecuteProcessInterface.TTExecutionContext(
            self.sq_name, tokens, time_overlap, est_runtime)
        # FIXME: this modifies the tokens in place, causing sticky tokens to
        # run with TTTimeSpec. This may be an issue considering all tokens are
        # first converted to TTTime.
        exec_context.dereference_token_times()

        return exec_context

    def calculate_next_trigger_time(self, start_from=None):
        '''
        Determine the next sampling time for a streaming node; when it will need
        to retrigger itself next (or timeout). Base this on the curent time,
        rather than the last iteration or we may get trapped in a perpetual
        state of lateness (or negative delays)

        :param start_from: calculate the next trigger time based on some
            starting time; it should be within one period of this start time.
            Defaults to None, in which case we read the clock associated with
            this SQ for the current time and calculate it from there.

        :type start_from: int
        '''

        if start_from is None:
            clock = self.firing_rule.clock
            if not clock.is_root():
                logger.warning(
                    "TTClock.root() didn't give the same root as tracing "
                    "the tree... this is a diagnostic")
            start_from = clock.now()

        assert isinstance(
            start_from,
            int), ('Base point for the next trigger time must be an integer, '
                   'like all timestamps in our system.')
        # time until we fire next. Modulo forces this to [0, period), even
        # though phase-now is almost guaranteed to be negative.
        time_until_next_iteration = (
            self.firing_rule.phase - start_from) % self.firing_rule.period
        if time_until_next_iteration == 0:
            # special case, mainly for simulation. Anyways, if there's some
            # tiny amount of time until the next iteration, we obviously
            # cannot hit this in real time
            time_until_next_iteration += self.firing_rule.period
        time_of_next_iteration = time_until_next_iteration + start_from

        return time_of_next_iteration

    def receive_token(self, token):
        '''
        Receive a token meant for this particular SQ. This causes us to check
        the firing rule.

        It will be compared with the existing tokens across the ports,
        potentially returning an executable context and a control token
        (depending on the rule). An execution context will signal that we have
        found a set of viable tokens, and will attempt to execute on them in the
        ``TTExecuteProcess``.

        :param token: An input token to this SQ

        :type token: TTToken

        :return: An executable context for the scheduler and a control token.
            Either of these may be None, depending on how the firing rule
            operates

        :rtype: list(TTToken), TTToken
        '''

        if (self.firing_rule.rule_type == TTFiringRuleType.Strict
                or self.firing_rule.rule_type == TTFiringRuleType.Timed):
            control_token = None
            execution_context = self.check_basic_firing_rule_on_input(token)
            return execution_context, control_token

        elif self.firing_rule.rule_type == TTFiringRuleType.TimedRetrigger:
            logger.debug('Timed retrigger firing rule call')
            execution_context, control_token = self.check_timed_retrigger_firing_rule(
                token)
            return execution_context, control_token

        elif self.firing_rule.rule_type == TTFiringRuleType.SequentialRetrigger:
            logger.debug('Sequential retrigger firing rule call')
            execution_context, control_token = self.check_sequential_retrigger_firing_rule(
                token)
            return execution_context, control_token

        elif self.firing_rule.rule_type == TTFiringRuleType.Immediate:
            logger.debug('Immediate untimed firing rule call')
            control_token = None
            execution_context = self.immediate_firing_rule(token)
            return execution_context, control_token

        elif self.firing_rule.rule_type == TTFiringRuleType.Deadline:
            logger.debug('Deadline trigger firing rule call')
            execution_context, control_token = self.check_deadline_firing_rule(
                token)
            return execution_context, control_token

        else:
            logger.warning(
                "Unsure what to do with token %s due to unknown firing rule %s",
                token, self.firing_rule.rule_type)

    def check_basic_firing_rule_on_input(self, token):
        '''
        Check a standard Timed/Strict firing rule using all the data ports and
        the new token. If we find a match (i.e. an overlap) among the tokens,
        then create an execution context.

        If we pass the firing rule check, the tokens used for execution will be
        removed from their ports unless the ports are designated 'Sticky'

        NB: if the firing rule is Strict, then all tokens time-labels must be
        identical.

        :param token: The newest input token.

        :type token: TTToken

        :return: an execution context to run tokens on, if a match if found.
            Else, None.

        :rtype: TTExecutionContext | None
        '''
        # try to find a set of matching tokens based on their time tags, only
        # among the input data ports. There are no control ports for this firing
        # rule.
        time_overlap, readied_tokens = self.token_store.match_token(
            token, token.tag.p, self.token_store.ports)

        if time_overlap is not None and readied_tokens is not None:
            if len(readied_tokens) == self.n_input_ports:
                # remove the readied tokens from their ports
                self.clean_ports(readied_tokens,
                                 token,
                                 time_overlap,
                                 remove_sticky=False)

                logger.debug('New Execution context for SQ %s', self.sq_name)
                logger.debug(
                    'Build Execution context with tokens: %s', readied_tokens)
                return self.create_execution_context(readied_tokens, time_overlap)

            else:
                # if there is a mismatch in the returned tokens and expected
                # number of ports, just add that token. In reality, this should
                # never be the case
                logger.warning(
                    'number of tokens does not match number of input ports (%d) for SQ %s!',
                    self.n_input_ports, self.sq_name)
                logger.warning(readied_tokens)
                self.token_store.insert_token(token, token.tag.p)

        else:
            # if we couldn't find a match, just add the token to the port it
            # is tagged for, and return None
            self.token_store.insert_token(token, token.tag.p)
            return None

    def check_timed_retrigger_firing_rule(self, input_token):
        '''
        Check the TimedRetrigger firing rule for this new token. This requires
        a control token and special timing behavior, so it is more complex.

        This firing rule executes by looking at the overlap between data
        ports, and using that to define over what period of time is should be
        producing values. Then, it will attempt to synchronize and produce new
        tokens according to the period, phase and clock associated with the
        firing rule. A local feedback token will cause the SQ to be
        retriggered according to this period and phase until the current time
        (according to the clock associated with this function) is beyond the
        stop_tick of the time overlap between the input tokens (whose values
        will be kept and reused until then)

        The first time it runs, this will not have anything for the control
        input, so it is ignored. The token is meant to best represent the time
        that a sample in the stream is produced, so the first retrigger token
        will be generated based on the current time. After that, it will produce
        a ``TTTimedEvent`` to establish the next iteration's release time. A
        separate process will wait until that time has arrived, and release the
        control token back such that this function will run again.

        :param token: The newest input token.

        :type token: TTToken

        :return: an execution context to run tokens on, if a match if found.
            Else, None. Also return a trigger token that is timed to be released
            when the next iteration should run

        :rtype: TTExecutionContext, TTToken | None, None
        '''
        # known weakness: if multiple sets of tokens are added to the data
        # ports such that there are multiple overlaps, there may be multiple
        # iterations of this SQ making control tokens at the same time. During
        # some of those overlaps, multiple invocations of this SQ may occur
        # near simultaneously.

        # else, it is from the control port, which will only start receiving
        # tokens after this has successfully started
        if input_token.tag.p < len(self.token_store.ports):
            # first time the SQ runs! Ignore the control port. This should
            time_overlap, readied_tokens = self.token_store.match_token(
                input_token, input_token.tag.p, self.token_store.ports)
            if (time_overlap and readied_tokens
                    and len(readied_tokens) == self.n_input_ports):
                if len(readied_tokens) == self.n_input_ports:
                    # successfully matched
                    logger.debug('Matched tokens: %s', readied_tokens)
                    clock = time_overlap.clock
                    # TODO: relax this at some point, at least if there's reason to
                    assert clock.is_root(
                    ), 'We should only be using the root clock for the stream-generators.'

                    next_trigger_time = self.calculate_next_trigger_time()

                    logger.debug(
                        'Next trigger time should be '
                        f'{clock.time_to_str(next_trigger_time)}; '
                        'stop-tick for sticky tokens is '
                        f'{clock.time_to_str(time_overlap.stop_tick)}')
                    if next_trigger_time < time_overlap.start_tick:
                        # not ready to start yet; set to be the start tick of
                        # the interval
                        next_trigger_time = self.calculate_next_trigger_time(
                            start_from=time_overlap.start_tick)
                    elif time_overlap.stop_tick < next_trigger_time:
                        # already ended; it's too late to do anything
                        logger.warning(
                            'Too Late; cannot produce stream. '
                            f'Remove sticky tokens for {self.sq_name}')
                        logger.warning(time_overlap)
                        self.clean_ports(readied_tokens,
                                         input_token,
                                         time_overlap,
                                         remove_sticky=True)
                        return None, None

                    # all the ordinary inputs are here, and we know when we
                    # need to fire. Save away this last input token
                    self.token_store.insert_token(input_token,
                                                  input_token.tag.p)

                    # create the triggering token. This should have enough
                    # information to find the same sticky tokens when the
                    # retrigger token is released to we can use those values
                    # during execution.
                    trigger_token_time = TTTime(clock, time_overlap.start_tick,
                                                next_trigger_time)
                    trigger_token_time = TTTimeSpec.from_time(
                        trigger_token_time)
                    base_tag = readied_tokens[0].tag
                    trigger_token_tag = Tag.TTTag(context=base_tag.u,
                                                  sq=base_tag.sq,
                                                  port=len(
                                                      self.token_store.ports),
                                                  ensemble_name=base_tag.e)
                    trigger_token = TTToken(None,
                                            trigger_token_time,
                                            tag=trigger_token_tag)

                    # no returned execution context; we'll make that from these
                    # same tokens once we can run again, but at that point we'll
                    # be closer to the right phase
                    execution_context = None
                    return execution_context, trigger_token
            else:
                self.token_store.insert_token(input_token, input_token.tag.p)
                execution_context = None
                trigger_token = None
                return execution_context, trigger_token
        else:
            # we are triggering execution based on a timed-retrigger token
            logger.debug('Received retrigger token %s ', input_token)
            time_overlap, readied_tokens = self.token_store.match_token(
                input_token, input_token.tag.p, self.token_store.ports)
            # TODO: very hacky way to encode a retrigger token
            # This should be represented in the actual list of input ports
            # +1 for the control (retrigger) token
            if (time_overlap and readied_tokens
                    and len(readied_tokens) == self.n_input_ports + 1):
                logger.debug('Found tokens with overlap %s: %s',
                                time_overlap,
                                readied_tokens)

                # two relevant tokens are the trigger (sets interval) and
                # iteration (set next time)
                trigger_token = readied_tokens[-1]
                assert trigger_token.tag.p == len(self.token_store.ports), (
                    'Trigger token should have a port that is technically '
                    'not in the set of data ports')

                next_trigger_time = self.calculate_next_trigger_time()

                if 0 < self.firing_rule.first_delay and not self.has_delayed:
                    clock = time_overlap.clock
                    logger.info('Delay for the first instance specified')
                    logger.debug('old trigger time was '
                                 f'{clock.time_to_str(next_trigger_time)}')
                    self.has_delayed = True
                    next_trigger_time += self.firing_rule.first_delay

                clock = self.firing_rule.clock

                # find the overlap better the data tokens; ignore the trigger
                # token
                interval_overlap = Interval(readied_tokens[0].time.start_tick,
                                            readied_tokens[0].time.stop_tick,
                                            None)
                # TODO: redoing matching when already matched because
                # control tokens are implicitly defined.
                for token in readied_tokens[1:-1]:
                    interval_overlap = interval_intersection(
                        interval_overlap,
                        Interval(token.time.start_tick, token.time.stop_tick))
                    if interval_overlap is None:
                        # this should never be the case, since we already
                        # checked for overlaps in MatchTokenOnPort...
                        logger.error(
                            'Interval overlap in timed-retrigger failed')
                        self.clean_ports(readied_tokens,
                                         trigger_token,
                                         time_overlap,
                                         remove_sticky=True)
                        return None, None

                # time overlap of only data tokens
                data_time_overlap = TTTime(trigger_token.time.clock,
                                           interval_overlap.begin,
                                           interval_overlap.end)

                logger.debug('Next trigger time should be '
                             f'{clock.time_to_str(next_trigger_time)}; '
                             'stop-tick for sticky tokens is '
                             f'{clock.time_to_str(data_time_overlap.stop_tick)}')
                logger.debug(f'Current time is {clock.time_to_str(clock.now())}')

                # the stop tick of the interval-intersection of data tokens
                # defines when we should stop sampling. In that case, do not
                # loopback a trigger token
                if data_time_overlap.stop_tick < next_trigger_time:
                    logger.info(
                        'Finished stream generation from sticky inputs: %s',
                        readied_tokens[:-1])
                    self.clean_ports(readied_tokens,
                                     trigger_token,
                                     data_time_overlap,
                                     remove_sticky=True)

                # cleaning ports will make sure that the retrigger token is useless
                retrigger_token = trigger_token.copy_token()
                retrigger_token.time.start_tick = time_overlap.stop_tick
                retrigger_token.time.stop_tick = next_trigger_time

                execution_context = self.create_execution_context(
                    readied_tokens[:-1], data_time_overlap)

                return execution_context, retrigger_token

            else:
                # we will discard the last retrigger token if not matched
                logger.debug('ignoring retrigger control token '
                             'as it has no data tokens to match')
                return None, None

    def check_sequential_retrigger_firing_rule(self, input_token):
        '''
        This firing rule enforces sequential (technically, chronological is more
        accurate) processing on input token streams by never allowing the SQ to
        synchronize on tokens older than the most recent iteration. This is
        mainly used in SQs that use internal state. A feedback token is used in
        the control port such that its time interval starts at the start tick of
        the previous iteration and stop tick at an effectively infinite
        timestamp. Any old sets of tokens, even if they have a valid overlap,
        will fail to align with this control token that is generated in the
        ``TTExecuteProcess`` after completing.

        A valid execution context will not contain the control token(s)

        :param input_token: The newest input token.

        :type input_token: TTToken

        :return: an execution context to run tokens on, if a match if found.
            Else, None.

        :rtype: TTExecutionContext | None
        '''
        # determine if control input or not (port nubmer is >= number of input
        # data ports
        logger.debug('Check sequential firing rule on input %s', input_token)
        control_token = None
        execution_context = None

        # check for matching on both data AND control ports
        time_overlap, readied_tokens = self.token_store.match_token(
            input_token, input_token.tag.p,
            self.token_store.ports + self.token_store.control_ports)

        if time_overlap is not None and readied_tokens is not None:
            logger.debug('Token matching returned time overlap %s and tokens %s',
                time_overlap,
                readied_tokens)

            if (len(readied_tokens) == self.n_input_ports +
                    len(self.token_store.control_ports)):
                # remove tokens from the ports, including the control tokens
                self.clean_ports(readied_tokens, input_token, time_overlap)

                execution_context = self.create_execution_context(
                    readied_tokens[:self.n_input_ports], time_overlap)
                logger.debug('Sending execution context %s', execution_context)

        else:

            port_n = input_token.tag.p
            # the port number determine whether this is control or data port.
            # For N input arcs, >=N is control, and <N is data.
            if port_n < self.n_input_ports:
                self.token_store.insert_token(input_token, port_n)
            else:
                self.token_store.insert_token_control_port(
                    input_token, port_n - self.n_input_ports)

        return execution_context, control_token

    def immediate_firing_rule(self, input_token: TTToken):
        time = input_token.time
        port = input_token.tag.p
        # need to mock TTTag as well
        readied_tokens = [
            TTToken(None, time, tag=Tag.TTTag(input_token.tag.u))
            for _ in range(len(self.token_store.ports))
        ]
        readied_tokens[port] = input_token
        return self.create_execution_context(readied_tokens, time)

    def check_deadline_firing_rule(self, input_token: TTToken):
        logger.debug(f"deadline fire token: {input_token}")
        port_n = input_token.tag.p

        clock: TTClock = self.firing_rule.clock
        curr_time = clock.now()

        # check if it's a data token or a control token
        if port_n < len(self.token_store.ports):  # data token
            # 1. attempt to match with control token
            # very similar code to basic_firing_rule, coalesce
            # differences are in the cleaning the ports, since we don't
            # want to deep clean control tokens

            time_overlap, readied_tokens = self.token_store.match_token(
                input_token, input_token.tag.p,
                self.token_store.ports + self.token_store.control_ports)

            if time_overlap and readied_tokens:
                # TODO: this scenario is almost the same as the control token
                # matching, antipattern!
                if len(readied_tokens) == self.n_input_ports + 1:
                    # remove the readied tokens from their ports
                    self.clean_ports(readied_tokens,
                                    input_token,
                                    time_overlap,
                                    remove_sticky=False,
                                    tracking_zombie=True)

                    logger.debug('Data came on time for SQ %s', self.sq_name)
                    return self.create_execution_context(readied_tokens[:-1], time_overlap), None

                else:
                    # if there is a mismatch in the returned tokens and expected
                    # number of ports, just add that token. In reality, this should
                    # never be the case
                    logger.warning(
                        'number of tokens does not match number of input ports (%d) for SQ %s!',
                        self.n_input_ports, self.sq_name)
                    logger.warning(readied_tokens)
                    self.token_store.insert_token(input_token, input_token.tag.p)
                    return None, None

            else:
                # if we couldn't find a match, just add the token to the port it
                # is tagged for, and return None
                self.token_store.insert_token(input_token, input_token.tag.p)
                return None, None

        else:  # control token
            deadline_end_tick = input_token.value
            logger.debug((f"found a control token sequence in deadline!, "
                          f"end_tick is {clock.time_to_str(deadline_end_tick)}"))
            logger.debug(f'ctrl tokens are: {self.token_store.control_ports}')
            logger.debug(f'ctrl token zombies are: {self.token_store.zombie_ctrl_tokens}')

            if curr_time < deadline_end_tick:
                # 1st check if it's matchable
                time_overlap, readied_tokens = self.token_store.match_token(
                    input_token, input_token.tag.p,
                    self.token_store.ports + self.token_store.control_ports)

                if time_overlap and readied_tokens:
                    # TODO: we want to just discard the deadline token as its
                    # job is done. The readied tokens should be deadline token
                    # + all the data ports
                    if len(readied_tokens) == self.n_input_ports + 1:
                        # remove the readied tokens from their ports
                        # this only affects the data port as never added ctrl
                        # port
                        self.clean_ports(readied_tokens,
                                        input_token,
                                        time_overlap,
                                        remove_sticky=False,
                                        tracking_zombie=False)

                        logger.debug('Data came on time for SQ %s', self.sq_name)
                        return self.create_execution_context(readied_tokens[:-1], time_overlap), None

                    else:
                        # if there is a mismatch in the returned tokens and expected
                        # number of ports, just add that token. In reality, this should
                        # never be the case
                        logger.warning(
                            'number of tokens does not match number of input ports (%d) for SQ %s!',
                            self.n_input_ports, self.sq_name)
                        logger.warning(readied_tokens)
                        # this will break because needs offset from the tag
                        self.token_store.insert_token_control_port(input_token, input_token.tag.p)
                        return None, None

                # 2. data has not come in, sleep until wakeup
                # set end tick to when it will need to wake up
                input_token.time.stop_tick = deadline_end_tick

                # NOTE: no need to worry about data coming in before control
                # token is inserted. This is running on the same thread in the
                # process.
                # insert the token into the control port
                self.token_store.insert_token_control_port(
                    input_token, port_n - self.n_input_ports)

                logger.debug(f"new deadline token: {input_token}")
                return None, input_token

            else:  # deadline has reached
                logger.debug(f"checking deadline")

                # Data may have consumed this time token
                # check if its zombie (already consumed)
                if self.token_store.is_zombie_control_token(input_token):
                    logger.debug('control token has already been consumed')
                    self.token_store.remove_zombie_control_token(input_token)
                    return None, None

                # Case 2: corresponding data token has not come in
                # execute PlanB: supply a None token to the Deadline SQ
                # because it is a control token, we need to modify the port it should go to
                logger.debug(f"executing Plan B!!")

                # TODO: force clean data ports before this time?
                # NOTE: still attempts to clean if control token arrives with
                # the deadline already passed
                readied_tokens = [input_token]
                self.clean_ports(readied_tokens,
                                 None,
                                 input_token.time,
                                 remove_sticky=False,
                                 tracking_zombie=True)

                input_token.value = None

                return self.create_execution_context(readied_tokens,
                                                     input_token.time), None

    def clean_ports(self,
                    readied_tokens,
                    unadded_token,
                    time_overlap,
                    remove_sticky=False,
                    tracking_zombie=False):
        '''
        Remove tokens from the ports. Tokens that are ready to be executed on
        should be removed, but the newest token that triggered firing rule
        execution should not be, since it was not added to the ports in the
        first place. Some ports may be 'sticky' such that their values may
        persist for more than one iteration; these may be optionally removed,
        but are not by default. We also do some time-based garbage collection by
        looking at the token overlap here, and removing anything particularly
        old (by default, 30M ticks of the associated clock, which should be
        equivalent to at least 30 seconds).

        :param readied_tokens: The set of tokens to be executed on; these should
            be in a returned ``TTExecutionContext``

        :type readied_tokens: list(TTToken)

        :param unadded_token: The token whose arrival triggered the firing rule
            to be satisfied. This should not have been added to the ports in the
            waiting matching section, we should not try to remove it.

        :type unadded_token: TTToken

        :param time_overlap: The time overlap between the set of readied tokens.
            Primarily used to clean out sufficiently old tokens.

        :type time_overlap: TTTime

        :param remove_sticky: An indicator to determine whether sticky ports
            should be considered or not for cleaning/garbage collection.
            Defaults to False

        :type remove_sticky: bool

        :return: None
        '''
        # remove each of the readied_tokens from their port (unless its the
        # newest one)
        for ready_token in readied_tokens:
            if ready_token == unadded_token:
                # this token was never added to the ports because it cause the
                # firing rule to be satisfied
                continue

            # clear from data port
            if ready_token.tag.p < self.n_input_ports:
                data_port_num = ready_token.tag.p
                # do be careful if the ports/tokens are sticky
                if (self.token_store.ports[data_port_num].port_type !=
                        TTPortType.Sticky or remove_sticky):
                    self.token_store.remove_token(
                        ready_token, data_port_num)
            # clear from control port
            # control port tokens should be conditionally cleaned since ctrl
            # tokens arrive in two states: it has been seen before (and
            # delayed until now) or never seen before
            else:
                control_port_num = ready_token.tag.p - self.n_input_ports
                # do be careful if the ports/tokens are sticky
                if (self.token_store.control_ports[control_port_num].port_type
                        != TTPortType.Sticky or remove_sticky):
                    self.token_store.remove_token_control_port(
                        ready_token, control_port_num, tracking_zombie)

        # FIXME: should this run every time?
        self.time_based_garbage_collection(time_overlap,
                                           remove_sticky=remove_sticky,
                                           remove_control=True)

    def time_based_garbage_collection(self,
                                      recent_time_overlap,
                                      staleness_duration=30000000,
                                      remove_control=False,
                                      remove_sticky=False):
        '''
        Remove old tokens based on how long they have been around, according to
        their time tags (specifically, the stop-tick)

        :param recent_time_overlap: The overlap in time between a set of tokens
            that are ready to be executed on

        :type recent_time_overlap: TTTime

        :param staleness_duration: A parameter to determine how old is too old
            for tokens to still be used. If they're too old, they're considereed
            'stale' and are removed. By default, this is equivalent to 30
            seconds in the root domain (assuming microsecond granularity). This
            will be important to parameterize (perhap using the firing rule and
            knowledge about timescales in the application) in certain types of
            system/applications. Defaults to 30000000.

        :type staleness_duration: int

        :param remove_control: An indicator for whether to consider control
            ports as well when removing tokens. Defaults to False

        :type remove_control: bool

        :param remove_sticky: An indicator for whether to consider sticky ports
            as well when removing tokens. Defaults to False

        :type remove_control: bool
        '''
        # clean out older tokens based on their token times.
        recent_start = recent_time_overlap.start_tick

        stale_before_time = recent_start - staleness_duration
        # we only remove them if the start and end are both older than that
        # time.
        logger.debug(f'Time-based garbage collection for SQ {self.sq_name}; '
                     'Removing everything with time intervals older than '
                     f'{stale_before_time}')

        for port in self.token_store.ports:
            if port.port_type == TTPortType.Sticky and not remove_sticky:
                continue

            len_before = len(port.storage)
            port.storage.remove_envelop(-TTTime.MAX_TIMESTAMP,
                                        stale_before_time)
            len_after = len(port.storage)

            if len_before != len_after:
                logger.debug('We removed %d tokens with garbage collection!',
                    len_before-len_after)

        if remove_control:
            for port in self.token_store.control_ports:
                if port.port_type == TTPortType.Sticky and not remove_sticky:
                    continue

                len_before = len(port.storage)
                port.storage.remove_envelop(-TTTime.MAX_TIMESTAMP,
                                            stale_before_time)
                len_after = len(port.storage)

                if len_before != len_after:
                    logger.debug('We removed %d tokens with garbage collection!',
                        len_before-len_after)

    def instantiate_at_runtime(self,
                               clocks,
                               initial_context=Tag.DEFAULT_CONTEXT_ID):
        '''
        Instantiate the SQ at runtime, performing operations like registering
        the correct clock (for retriggering firing rules) or adding default
        tokens to ports that require them.

        :param clocks: A set of clocks to search for the one designated by this
            SQ for operations like timed retriggering

        :type clocks: list(TTClock)

        :param initial_context: A context identifier for the context (u) portion
            of the token tag; it is assumed that all input tokens will have the
            same context tag.

        :type initial_context: string
        '''

        logger.info('Instantiate runtime SQ synchronization for SQ %s with firing rule %s',
            self.sq_name,
            self.firing_rule.rule_type)

        # connect the clock expected by the SQ with the one provided by its
        # runtime environent. TTClockSpec is integral to this.
        self.firing_rule.update_clock(clocks)

        # if we have a sequential retriggering function, then we initialize it
        # with a control token that can be synchronized with anything
        if self.firing_rule.rule_type == TTFiringRuleType.SequentialRetrigger:
            if hasattr(self.firing_rule, 'clock'):
                clock = self.firing_rule.clock
            else:
                # pull out some clock and trace to its root; use that as the
                # default when unspecified
                clock = clocks[0].trace_to_root()

            # *FIXME* inf_time should be referenced to the root clock
            if clock is None:
                inf_time = TTTime.infinite(TTClock.root())
            else:
                inf_time = TTTime.infinite(clock)
            # assume the control port numbering starts at N and goes up from
            # there; there should be only 1 retriggering port for the
            # SequentialRetrigger rule. Unclear if the initial context will
            # actually work well in practice, but it should be fine if the
            # context never changes. Otherwise, each new context will probably
            # need to be instantiated in a similar way.
            initial_control_tag = Tag.TTTag(context=initial_context,
                                            sq=self.sq_name,
                                            port=self.n_input_ports)
            initial_control_token = TTToken(None,
                                            inf_time,
                                            tag=initial_control_tag)
            # add token to the control port; for consistency, recalculate that
            # port number (though it should be 0)
            self.token_store.insert_token_control_port(
                initial_control_token,
                initial_control_token.tag.p - self.n_input_ports)


def interval_intersection(interval_1, interval_2):
    '''
    Search for an intersection between two intervals

    :param interval_1: The first interval

    :type interval_1: ``intervaltree.Interval``

    :param interval_2: The second interval

    :type interval_2: ``intervaltree.Interval``

    :return: An interval representing the intersection of the two Intervals.
        None if there is not one found. Reuse the data from the first interval
        if an interval is found

    :rtype: ``intervaltree.Interval``
    '''
    data = interval_1.data
    if interval_1.begin < interval_2.begin:
        lower = interval_1
        higher = interval_2
    else:
        lower = interval_2
        higher = interval_1
    if lower.end < higher.begin:
        # No overlap
        return None
    return Interval(higher.begin, min(lower.end, higher.end), data)


def interval_union(interval_1, interval_2):
    '''
    Search for a union between two intervals

    :param interval_1: The first interval

    :type interval_1: ``intervaltree.Interval``

    :param interval_2: The second interval

    :type interval_2: ``intervaltree.Interval``

    :return: An interval representing the union of the two Intervals.  Reuse the
        data from the first interval if an interval is found

    :rtype: ``intervaltree.Interval``
    '''
    data = interval_1.data
    if interval_1.begin < interval_2.begin:
        lower = interval_1
        higher = interval_2
    else:
        lower = interval_2
        higher = interval_1
    return Interval(lower.begin, max(lower.end, higher.end), data)


class TTPortType(Enum):
    '''
    The port type is necessary for streaming graphs and certain types of
    firing rules.

    * Vanilla ports are normal; a token comes in, synchronizes with others,
      and gets consumed on execution. It will be removed when it finds another
      set of tokens.

    * Sticky ports will force the token to stick around for more than one
      iteration so that it can be reused. These are necessary for periodic
      sources (TimedRetrigger firing rule) and when a streaming SQ receives
      input from streaming and non-streaming sources.

    * Control ports are extra special, in that they receive from arcs that are
      not within the compiled graph; they exist to *control* the mechanisms to
      force sequentiality or release a token/execution context on a timed
      schedule, e.g. stateful SQ or a deadline, resp. They may be used in
      several different ways, but are never returned as part of an execution
      context during synchronization.
    '''

    Vanilla = 0
    Sticky = 1
    Control = 2  # used for retriggering; not described by graph itself


class TTInputPort:
    '''
    A TTInputPort holds tokens for a port, and is enumerated with a port type
    to better distinguish how to handle values as they are inserted/removed
    from the port.

    :param port_type: The type of input port to create

    :type port_type: TTPortType
    '''

    def __init__(self, port_type):
        self.port_type = port_type
        self.storage = IntervalTree()

    def __repr__(self):
        return f'{self.storage}'


class TTSQInputTokenStorage:
    '''
    The Token Storage for an SQ; alternatively called a *waiting-matching*
    section, but only for this SQ. Tokens will be stored in ports and
    synchronized based on their time tags, which are intervals. We use the
    ``intervaltree`` library to search for intersections between stored
    tokens.  This should be a sufficiently efficient data structure for these
    operations.

    :param n_ports: The number of input (data) ports

    :type n_ports: int

    :param firing_rule: The firing rule that will be cheecking this input token
        storage

    :type firing_rule: TTFiringRule

    :param sq: A reference to the SQ this will be used for determining port
        types by analyzing the arcs.

    :type sq: TTSQ

    :param is_streaming: An indicator for whether this SQ operates on streams of
        data or not. Also used for determining port types while analyzing arcs.
        Defaults to False

    :type is_streaming: bool

    :param use_deadline: An indicator for wheter this SQ implements deadlines
        during synchronization. Defaults to False

    :type use_deadline: bool
    '''
    # For each input port to this SQ, allocate an IntervalTree

    def __init__(self,
                 n_ports,
                 firing_rule,
                 sq,
                 is_streaming=False,
                 is_singleton=False,
                 use_deadline=False):
        self.ports = []
        self.clock = None
        self.is_streaming = is_streaming
        self.control_ports = []
        self.zombie_ctrl_tokens = set()
        self.sq_name = sq.sq_name
        # DO NOT SAVE A REFERENCE TO THE SQ OR ANY ARCS HERE. This would result
        # in references that, when serialized, will copy the whole graph.  Don't
        # do it.

        for i in range(n_ports):
            port_type = TTPortType.Vanilla
            # TODO: change the way Sticky ports are detected
            # if there is an external arc, might not have a parent sq
            port_is_streaming = sq.ipp_records[i].is_streaming

            if is_singleton:
                # Don't make anything sticky if singleton
                logger.debug(f"SQ {sq.sq_name} is a singleton, "
                            "preventing sticky tokens")

            elif self.is_streaming and not port_is_streaming:
                # if this node is streaming but one of the inputs is not, that
                # one should be reused (sticky)
                port_type = TTPortType.Sticky

            elif (self.is_streaming and firing_rule.rule_type
                  == TTFiringRuleType.TimedRetrigger):
                # sticky if this is a timed retriggering node so the inputs
                # can be reused for each triggering
                port_type = TTPortType.Sticky

            logger.debug('Create port for index %d as type %s', i, port_type)

            self.ports.append(TTInputPort(port_type))

        # Add control port for retrigger mechanics
        if (firing_rule.rule_type == TTFiringRuleType.TimedRetrigger or
                firing_rule.rule_type == TTFiringRuleType.SequentialRetrigger):
            logger.debug("Add control port for SQ %s", self.sq_name)
            self.control_ports.append(TTInputPort(TTPortType.Control))

        if use_deadline:
            logger.debug("Add control port for deadline")
            self.control_ports.append(TTInputPort(TTPortType.Control))

    # We've made the decision that streamed tokens arriving at a node make that
    # node a streaming node, and in that case, all the token clocks need to
    # be the same.
    def check_token_clock(self, token):
        '''
        All times labeled on tokens should be using the same clock. If this
        fails, we will raise an exception

        :param token: An input token to check the clock for against the clock
            the other tokens use

        :type token: TTToken

        :return: None
        '''
        if self.clock is None:
            # Capture the clock from the first token, in case we find out
            # later that this is a streaming SQ
            self.clock = token.time.clock
        if self.is_streaming or token.is_streaming:
            self.is_streaming = True
            if (isinstance(token.time, TTTime)
                    and self.clock != token.time.clock):
                raise Exception(
                    "WaitingMatchingError",
                    f"STREAMing SQ tokens must be from the same clock -- "
                    f"did you forget to RESAMPLE? {self.clock}, {token.time.clock}"
                )

            elif (isinstance(token.time, TTTimeSpec) and
                  TTClockSpec.from_clock(self.clock) != token.time.clockspec):
                raise Exception(
                    "WaitingMatchingError",
                    f"STREAMing SQ tokens must be from the same clock -- "
                    f"did you forget to RESAMPLE? {self.clock}, {token.time.clockspec}"
                )
        return

    # Insert the token into the appropriate (based on port number) IntervalTree
    # using ticks of the root clock as the basis for representing the interval
    # associated with this token
    def insert_token(self, token, port_number):
        '''
        Insert the token into the port at the designated number (determined by
        the token's tag). The port's storage is implemented as an interval
        tree, and this interval is none other than start and stop ticks on the
        token's
        ``TTTime``.

        :param token: The token to add to the port

        :type token: TTToken

        :param port_number: The port number the token should be added to

        :type port_number: int

        :return: None
        '''
        logger.debug('Add token %s to port number %d', token, port_number)
        self.check_token_clock(token)
        self.ports[port_number].storage[token.time.start_tick:token.time.
                                        stop_tick] = token
        logger.debug(self)

    def insert_token_control_port(self, token, port_number):
        '''
        Insert the token into the control port at the designated number
        (determined by the token's tag). The port's storage is implemented as
        an interval tree, and this interval is none other than start and stop
        ticks on the token's ``TTTime``.

        Control ports start at N within the token's tag, where N is the number
        of input arcs that accept ordinary data tokens, e.g., Control port 1
        would be port number N+1

        :param token: The token to add to the port

        :type token: TTToken

        :param port_number: The pork number the token should be added to

        :type port_number: int

        :return: None
        '''
        logger.debug('Add token %s to control port number %d',
                        token,
                        port_number)
        self.check_token_clock(token)
        self.control_ports[port_number].storage[token.time.start_tick:token.time.stop_tick] = token
        logger.debug(self)

    def remove_token(self, token, port_number):
        '''
        Delete a token from the port. If the token does not exist, ValueError
        will be thrown signaling the token is not present in the storage

        :param token: The token to remove to the port

        :type token: TTToken

        :param port_number: The port number the token should be added to

        :type port_number: int

        :return: None
        '''
        self.check_token_clock(token)
        interval = Interval(token.time.start_tick,
                            token.time.stop_tick,
                            data=token)
        self.ports[port_number].storage.remove(interval)

    def remove_token_control_port(self, token, port_number, tracking_zombie):
        '''
        Delete a token from the port. If the token does not exist, log that it
        does not exist in the storage

        :param token: The token to remove to the port

        :type token: TTToken

        :param port_number: The port number the token should be added to

        :type port_number: int

        :return: None
        '''
        self.check_token_clock(token)
        interval = Interval(token.time.start_tick,
                            token.time.stop_tick,
                            data=token)
        if interval in self.control_ports[port_number].storage:
            self.control_ports[port_number].storage.remove(interval)
        else:
            logger.debug(
                f'attempting to clean ctrl tok: {token}, but not in storage')

        if tracking_zombie:
            self.zombie_ctrl_tokens.add(token)

    def is_zombie_control_token(self, token):
        self.check_token_clock(token)
        return token in self.zombie_ctrl_tokens

    def remove_zombie_control_token(self, token):
        self.zombie_ctrl_tokens.remove(token)

    def match_token(self, token, port_number, ports=None):
        '''
        Try to find a set of candidate tokens that all align in time with the
        input token

        :param token: The token to remove to the port

        :type token: TTToken

        :param port_number: The port number the token should be added to

        :type port_number: int

        :param ports: The set of ports to match over. Defaults to the set of
            data ports

        :type ports: list(``TTInputPort``)

        :return: an overlap time, a set of matched tokens (one per port,
            including the one the ``token`` was intended for). If no match is
            found, returns None, None

        :rtype: TTTime |  None, list(TTToken) |  None
        '''
        if ports is None:
            ports = self.ports

        self.check_token_clock(token)
        # Find a match to this token's start time

        # only need to look at other ports, and specifically the interval
        # trees within those ports
        other_ports = ports[:port_number] + ports[port_number + 1:]
        trees_to_match = list(map(lambda x: x.storage, other_ports))

        # debugging
        print_ports = [len(port.storage) for port in ports]
        if port_number < len(print_ports):
            print_ports[port_number] = "Token's port"
        else:
            logger.debug(
                f"Token's port number {port_number} is larger than port "
                "list, likely control token")
        logger.debug(
            '%s tokens in other ports that are matchable for SQ %s',
            print_ports, self.sq_name)

        # start searching for intervals based on the first token
        interval_set = {
            Interval(token.time.start_tick, token.time.stop_tick, token)}
        for tree in trees_to_match:

            new_interval_set = set()
            for interval in interval_set:

                # Retrieve the intervals in the tree that match intervals in
                # the set
                overlap_set = tree[interval.begin:interval.end]
                # Intersect the interval with each of the results in the set.
                # The data from 'x' will be in the result that enters the set
                # (those from the tree, which is a new port we haven't checked
                # yet)
                new_interval_set = new_interval_set.union(
                    set(
                        map(lambda x: interval_intersection(x, interval),
                            overlap_set)))

            interval_set = new_interval_set

        # take the largest interval
        if len(interval_set) > 0:
            interval_list = list(interval_set)
            if len(interval_set) > 1:
                max_size = 0
                max_ind = 0
                # take the largest interval; that signifies the most
                # concurrency and helps avoid small overlaps. There may
                # several better choices (which may be context dependent)
                for i, interv in enumerate(interval_list):
                    size = interv.end - interv.begin
                    if size > max_size:
                        max_size = size
                        max_ind = i

                matching_interval = interval_list[max_ind]
            else:
                matching_interval = interval_list[0]

            # Return a tuple: first element is the overlap interval, second is
            # the list of tokens including the input token)
            overlap_time = TTTime(token.time.clock, matching_interval.begin,
                                  matching_interval.end)

            matched_token_list = []
            for i, tree in enumerate(trees_to_match):
                intervals = list(
                    tree[matching_interval.begin:matching_interval.end])
                # token with most intersection with the overlap time will be
                # returned. Disgusting one-liner. Map to look for intersection
                # interval with matching_interval, max on lambda that looks at
                # size of that intersection, and return the data within that
                # interval as the token
                largest_overlap_token = max(map(
                    lambda t: interval_intersection(t, matching_interval),
                    intervals),
                                            key=lambda t: t.end - t.begin).data
                matched_token_list.append(largest_overlap_token)

            # matched_token_list = list(map(lambda x:
            # list(x[matching_interval.begin:matching_interval.end])[0].data,
            # trees_to_match))
            # is this correct? why is index 0? We chose the
            # largest matching interval, but how do we get the exact right
            # tokens? SHould probably have maximum overlap with this set. TODO.
            # all_token_list = matched_token_list[:port_number] + [token] +
            # matched_token_list[port_number+1:] # no need to only use beyond
            # port_number+1; port_number: is fine because we used fewer trees
            # for matching
            all_token_list = matched_token_list[:port_number] + [
                token
            ] + matched_token_list[port_number:]
            return overlap_time, all_token_list
        else:
            return None, None

    def match_token_strict(self, token, port_number):
        '''
        Match tokens strictly, meaning that the intervals must align exactly

        :param token: The token to remove to the port

        :type token: TTToken

        :param port_number: The port number the token should be added to

        :type port_number: int

        :return: an overlap time, a set of matched tokens (one per port,
            including the one the ``token`` was intended for). If no match is
            found, None

        :rtype: TTime |  None, list(TTToken) |  None
        '''
        overlap_time, all_token_list = self.match_token(
            token, port_number)
        if overlap_time == token.time:
            return overlap_time, all_token_list

        else:
            return None, None

    def __repr__(self):
        return f'SQ {self.sq_name} TokenStorage <ports:{self.ports}, ctrl:{self.control_ports}>'

    def reap_tokens_on_timeout(self, deadline_interval, ports):
        raise DeprecationWarning(
            'reap_tokens_on_timeout is reused code from the '
            'CPS Week implementation, and may not be well suited for TTPython')
        deadline_start = deadline_interval.begin
        deadline_stop = deadline_interval.end

        # allocate an array with a default value of None (may need to update to be something else; result of callback>)
        readied_tokens = [None for i in range(len(ports))]

        # search for a set of tokens to use for the deadline expiration. the start tick of the dealine should be within their interval
        for i, port in enumerate(ports):

            # We will remove ANYTHING that's older than the deadline's start
            waiting_token_intervals = port.storage[-TTTime.MAX_TIMESTAMP:deadline_start]
            # but keep the tokens between the deadline start and end
            token_intervals_since_deadline_start = port.storage[deadline_start:deadline_stop]
            for interval in waiting_token_intervals:
                token = interval.data
                # check if the interval also included in those since the
                # deadline's start; those ones are still valid.
                if interval in token_intervals_since_deadline_start:
                    # have we found a token for the i-th port yet?
                    if readied_tokens[i] is None:
                        # Shouldn't have this case if we have nonintersecting
                        # intervals stamped on tokens
                        readied_tokens[i] = token

                    elif interval.begin < readied_tokens[i].time.start_tick:
                        # token for that port already exists.. but if this one
                        # is older (but still newer than the deadline start),
                        # then select that instead
                        readied_tokens[i] = token

                else:
                    # remove only those that haven't been created within or
                    # since the deadline started
                    self.remove_token(token, i)

        # Look for an interval overlap, ignoring the missing tokens. Clocks
        # should be the same
        overlap_interval = Interval(-TTTime.MAX_TIMESTAMP,
                                    TTTime.MAX_TIMESTAMP, None)
        clock = None
        for tok in readied_tokens:
            if tok:
                overlap_interval = interval_intersection(
                    Interval(tok.time.start_tick, tok.time.stop_tick, None), overlap_interval)
                if clock is not None and clock != tok.time.clock:
                    raise ValueError(
                        'Synchronized tokens with different clocks!')
                elif clock is None:
                    clock = tok.time.clock

        if overlap_interval.begin >= overlap_interval.end:
            logger.warning(
                'end time of the interval is not greatre than the beginning; failed deadline check')
            return None, None

        overlap_time = Time.TTTime(
            clock, overlap_interval.begin, overlap_interval.end)

        return overlap_time, readied_tokens
