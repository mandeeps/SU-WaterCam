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
The TTSQExecute encapsulates the portion of an SQ that is actually executed at
runtime after the input values have been gathered and synchronized to produce a
``TTExecutionContext``. This portion of the SQ contains the code to execute,
keyword arguments to provide, and local state.

The TTSQExecute can be serialized/pickleized without copying other parts of the
graph, making it easier to send through the network than a TTSQ would be, given
that the input arcs connecting the graph would cause the entire graph to be
serialized any time a TTSQ with valid arcs is.
'''

import inspect

from .Clock import TTClockSpec
from . import SQ
from . import DebugLogger

logger = DebugLogger.get_logger('SQExecute')


class TTSQExecute():
    '''
    The execution portion of an SQ. Created at compile time and instantiated
    with runtime elements (like clocks) after being distributed to the
    ensembles.

    :param sq_name: The name of the SQ

    :type sq_name: string

    :param interpreter: A enumeration for the interpretation environment type

    :type interpreter: TTInterpreter

    :param code: The code that composes the SQ's execution portion. Obtained
        by unparsing the defined function's abstract syntax tree

    :type code: string

    :param function_name: The name of the function within the code, which is
        decorated with 'SQify' or 'STREAMify'

    :type function_name: string

    :param pattern: The input/output pattern of this SQ

    :type pattern: TTSQPattern

    :param num_inputs: The number of inputs; used to check keyword and
        postitional arguments before executing the SQ

    :type num_inputs: int

    :param execution_kwargs: A set of keyword arguments that provide/overwrite
        default arguments to the SQ. Some may be used as specification for how
        to treat the SQ outside of the code. Defaults to an empty dictionary

    :type execution_kwargs: dict

    :param is_sequential: A boolean indicator for whether this function should
        be executed sequentially (chronologically) or not. This is based on
        whether it is a streaming node and uses internal state between
        invocations. If True, it will enforce execution on sequential tokens by
        sending feedback to the synchronization portion of the SQ. Must be
        paired with a ``SequentialRetrigger`` firing rule

    :type is_sequential: bool
    '''

    def __init__(self, sq: SQ):
        return self.raw_init(
                sq.sq_name,
                sq.interpreter,
                sq.function_source,
                sq.function_name,
                sq.pattern,
                sq.n_input_ports,
                execution_kwargs=sq.execution_kwargs,
                is_sequential=sq.run_sequentially,
                is_persistent=sq.is_persistent)

    def raw_init(self,
                 sq_name,
                 interpreter,
                 code,
                 function_name,
                 pattern,
                 num_inputs,
                 execution_kwargs=None,
                 is_sequential=False,
                 is_conditional=False,
                 is_persistent=False):
        self.sq_name = sq_name
        # TTInterpreter enum
        self.interpreter = interpreter
        self.code = code
        self.function_name = function_name
        # probably going to break if the keywords contain n object, esp. one
        # that's difficult to pickle-ize
        self.execution_kwargs = {} if execution_kwargs is None else execution_kwargs
        self.state = {}
        self.pattern = pattern
        self.num_inputs = num_inputs
        self.is_sequential = is_sequential
        self.is_conditional = is_conditional
        self.is_persistent = is_persistent

        if self.interpreter == SQ.TTInterpreter.Python3:
            self.inspect_function()

        # will be updated at runtime based on kwargs
        self.data_validity_interval = 0
        self.execute_on_full_token = execution_kwargs.get(
            'TTExecuteOnFullToken', False)
        self.compiled_code = None
        self.namespace = None
        self.function = None
        self.kwargs = None

    def inspect_function(self):
        '''
        Inspect the actual function definition to look for any meta arguments
        in the definition, like ``TTExecuteOnFullToken``
        '''
        # precompile the code; exec on the raw string takes about 50us longer
        # on extremely simple functions, probably more for longer ones
        compiled_code = compile(self.code, '', 'exec')

        # establish a namespace for the function to execute within such that
        # it can have private state with conflicting with other SQs' state. We
        # can provide other imports here if we wish, e.g. TTTime or TTToken
        namespace = {
            "SQify": SQ.SQify,
            "STREAMify": SQ.STREAMify,
            'sq_state': {}
        }
        # establish the execution environment, which includes the function object
        # representing the SQified user code
        exec(compiled_code, namespace)
        function = namespace[self.function_name]
        # inspect function signature to look for special keywords
        sig = inspect.signature(function)

        for argname, argval in sig.parameters.items():
            # if the argument is a metakeyword (starts with 'TT'), carries a
            # default value, and is not given one, then pull the default from
            # the function signature
            if (len(argname) > 2 and argname[0:2] == 'TT'
                    and argval.default is not inspect.Parameter.empty
                    and self.execution_kwargs.get(
                        argname, 'magic_value') == 'magic_value'):

                logger.debug(
                    'SQ execution prep: Meta keyword %s with default value %s'
                    % (argname, argval.default))
                self.execution_kwargs[argname] = argval.default

    def instantiate_at_runtime(self, clocks):
        '''
        Prior to execution, instantiate the SQ's execution environment.

        This speeds up the runtime by not having to interpret the code as
        generically by pre-compiling the code and creating a private namespace
        to store internal state and imports.

        This also looks at the set of keyword arguments and handles meta
        keywords like 'TTClock' or 'TTDataValidityInterval' that dictate how
        to time-label streaming sources (STREAMify)
        '''
        # TODO: also make this function optionally run the code on some Null
        # inputs as a way to instantiate objects within the SQ (like setting up a
        # sensor or hardware accelerator). Probably should use some
        # meta-parameter (e.g. TTExecuteOnInstantiate=bool) precompile the code;
        # exec on the raw string takes about 50us longer on extremely simple
        # functions, probably more for longer ones
        logger.debug('Instantiating TTSQExecute (%s) at runtime' %
                     self.sq_name)

        self.compiled_code = compile(self.code, '', 'exec')

        # establish a namespace for the function to execute within such that
        # it can have private state with conflicting with other SQs' state
        self.namespace = {
            "SQify": SQ.SQify,
            "STREAMify": SQ.STREAMify,
            'sq_state': self.state
        }

        # execution environment is handled by process execution, as functions
        # may be executed by different processes where persistent state
        # requires an order.
        # TODO: stateless functions do not need to "save" their state across
        # iterations. Can we remove exec requirement from those executions?
        exec(self.compiled_code, self.namespace)
        self.function = self.namespace[self.function_name]

        # unpack any special keyword arguments
        self.kwargs = {}
        for kw_key in self.execution_kwargs.keys():
            kw_value = self.execution_kwargs[kw_key]
            if (kw_key == 'TTDataIntervalWidth'
                    and self.pattern == SQ.TTSQPattern.TriggerInNOut):
                self.data_validity_interval = self.execution_kwargs[
                    'TTDataIntervalWidth']

            elif kw_key == 'TTClock':
                # replace the clock from whatever the compiler gave to an
                # equivalently defined one at runtime.
                logger.info(
                    'replacing clock during SQExecute runtime instantiation')
                updated_clock = False

                for c in clocks:
                    if TTClockSpec.from_clock(c) == TTClockSpec.from_clock(
                            self.execution_kwargs[kw_key]):
                        logger.debug('Replaced clock %s with %s in kwargs' %
                                     (self.execution_kwargs[kw_key], c))
                        self.kwargs[kw_key] = c
                        updated_clock = True
                        logger.debug('current time on this clock is %d' %
                                     c.now())

                if not updated_clock:
                    logger.warning(
                        'Could not find a clock to update for SQ %s' %
                        self.sq_name)
                    logger.warning(clocks)
                    logger.warning(self.execution_kwargs[kw_key])

            else:
                # we should have already vetted the kwargs when creating the
                # TTSQExecute, so this should be safe. Else, it will throw an
                # error due to an unexpected arg..
                # raise Exception('Malformed -- illegal keyword; arbitrary,
                # user-supplied kwargs are not currently supported')
                logger.debug("SQ '%s': Added keyword argument %s=%s" %
                             (self.sq_name, kw_key, kw_value))
                self.kwargs[kw_key] = kw_value

        logger.debug('Finished runtime instantation of TTSQExecute (%s)' %
                     self.sq_name)
        if len(self.kwargs) > 0:
            logger.debug('kwargs: %s' % self.kwargs)

    @staticmethod
    def from_json(json_in):
        '''
        Convert a JSON formatted SQ specification into a TTExecute object.
        Format follows ``TTSQ.json_execute``

        :param json: JSON dictionary containing the SQ (specifically, the
            Execute section (key 'program'))

        :type json: dict
        '''
        if 'execute' in json_in:
            json_exec = json_in['execute']

        if 'sync' in json_in:
            json_sync = json_in['sync']
            n_inputs = len(json_sync['input_ports'])
        else:
            n_inputs = 0
        try:
            if json_exec['program']['type'] == 'iota':
                interp = SQ.TTInterpreter.IoTA
            elif json_exec['program']['type'] == 'python3':
                interp = SQ.TTInterpreter.Python3
            else:
                raise ValueError('unknown interpreter',
                                 json_exec['program']['interpreter'])
            execute = TTSQExecute(json_exec['sq_name'], interp,
                                  json_exec['program']['instructions'],
                                  json_exec['program']['function_name'],
                                  json_exec['program']['execution_keywords'],
                                  n_inputs)

            return execute
        except KeyError:
            raise
