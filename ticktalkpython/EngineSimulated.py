# Copyright 2024 Carnegie Mellon University
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

from .ExecuteProcessInterface import TTExecutionContext
from . import DebugLogger
from . import Engine
from . import SQ

logger = DebugLogger.get_logger('sim_engine')


class SimulatedEngine(Engine.Engine):

    def __init__(self, root_clk):
        super().__init__(root_clk)
        self.sq_executes = {}

    # This function is equivalent to calling spawn_sq_job, executing the SQ to
    # completion, and calling handle_sq_output. This is necessary to keep to
    # keep simulated time execution working, as simulation doesn't work well
    # with parallel execution
    def submit_job(self, ex_ctx: TTExecutionContext):
        '''
        Execute a ``TTSQExecute`` on a new execution context. This will
        provide the inputs (in the execute_context) and set of stored keyword
        arguments (within the TTSQExecute) to be executed in a private
        namespace with access this SQ's state
        '''
        # mark when we started
        execute_time = self.root_clock.now()

        # find SQ to execute
        try:
            sq_ex = self.sq_executes[ex_ctx.sq_name]

        except KeyError:
            logger.error('Failed to find SQ named %s', ex_ctx.sq_name)
            return

        logger.info('Execute for SQ %s', sq_ex.sq_name)
        assert isinstance(sq_ex.interpreter,
                          SQ.TTInterpreter), "Unsupported interpreter"
        if sq_ex.interpreter == SQ.TTInterpreter.Python3:

            # The sq should have already been instantiated (or at least
            # 'prepared')
            if not hasattr(sq_ex, 'namespace'):
                # TODO: should the namespace be distinct based on the context
                # tag 'u' within the set of tokens?
                # self.sim is True here
                sq_ex.instantiate_at_runtime(self.clocks, True)

            if len(ex_ctx.inputs) != sq_ex.num_inputs:
                # raise error instead? Likely that a runtime error will be
                # thrown. If we provided some default or null (None) input,
                # those should still be here in the proper index
                logger.warning("Execute SQ %s on %d inputs -- %d exepected",
                               sq_ex.sq_name, len(ex_ctx.inputs),
                               sq_ex.num_inputs)

            # provide kwargs if present; else, just the inputs
            if sq_ex.kwargs:
                return_token_list = sq_ex.function(*ex_ctx.inputs,
                                                   **sq_ex.kwargs)
            else:
                return_token_list = sq_ex.function(*ex_ctx.inputs)
            logger.debug('returned %s', return_token_list)

            # if simulation, it would be nice to wait for this period, but
            # that is difficult; simpy uses 'yield' to insert delays, but that
            # would make this function a generator such that it will not
            # complete in the phy version. If we need that functionality, it
            # should be carefully designed. Nonessential for now. TODO.
            # if self.sim and sq_context.estimate_runtime > 0:
            #     self.wait_function(sq_context.estimate_runtime)

            completion_time = self.root_clock.now()

            sq_ex.state = sq_ex.namespace['sq_state']

            return self.prep_tokens(return_token_list, sq_ex, ex_ctx,
                                    execute_time, completion_time)

        else:
            raise ValueError('Interpreter not supported')

    def cleanup():
        pass
