# Copyright 2022 Carnegie Mellon University
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

import pickle, simpy, time
import argparse

from ticktalkpython import DebugLogger
from ticktalkpython import RuntimeManager
from ticktalkpython import Graph
from ticktalkpython.IPC import *

from output_functions import get_applied_output_func


def unpack_graph(filename):
    inpickle = open(filename, 'rb')
    graph = pickle.load(inpickle)
    assert isinstance(graph, Graph.TTGraph)
    return graph


def send_input_tokens(graph, runtime_manager, logger, inputs=None):
    trigger_inputs = {'trigger': 0xdeadbeef} if inputs is None else inputs
    execute_graph_message = Message(RuntimeMsg.ExecuteGraphOnInputs,
                                    (graph, trigger_inputs),
                                    Recipient.ProcessRuntimeManager)

    logger.info('Sending token inputs\n\n\n\n\n\n')

    runtime_manager.send_to_runtime(execute_graph_message)


def send_graph_sim(runtime_manager, sim, filename, logger):
    yield sim.timeout(0)
    logger.info("Distribute Graph to ensembles")
    graph = unpack_graph(filename)
    instantiate_graph_msg = Message(RuntimeMsg.InstantiateAndMapGraph, graph,
                                    Recipient.ProcessRuntimeManager)
    runtime_manager.send_to_runtime(instantiate_graph_msg)

    yield sim.timeout(0)
    send_input_tokens(graph, runtime_manager, logger)


def main():
    parser = argparse.ArgumentParser(
        description=
        'simulate an execution of a compiled TTPython dataflow graph. '
        'logical ticks are equivalent to seconds')

    parser.add_argument('file',
                        metavar='F',
                        type=str,
                        help='the pickled dataflow graph to simulate')
    parser.add_argument(
        '--timeout',
        type=int,
        default=1_000_000_000,
        help='simulation timeout (default: 1_000_000_000 (logical ticks))')
    parser.add_argument('-n',
                        '--ntwk_delay',
                        type=float,
                        default=0,
                        help='specify network delay. '
                        'simulates time for token to travel between SQs.'
                        '(default: 0 (logical ticks))')
    parser.add_argument(
        '-o',
        '--output_func',
        type=str,
        default=['log_msg_to_file', './output.log'],
        nargs='*',
        help='the str name of the function found in module '
        '"output_functions" used to send tokens with no correponding '
        'downstream SQ (default: log_to_file). You must provide the '
        'argument list to execute said function. The function assumes '
        'the last argument is a class type Msg (in IPC.py) to write, which '
        'should not be included in the provided arg list.')
    parser.add_argument(
        '--log',
        default='./output.log',
        help='specify the log file used to capture runtime behavior')
    parser.add_argument('-d',
                        '--debug',
                        action='store_true',
                        help='flag whether to show debug information')

    args = parser.parse_args()
    file_path = args.file
    timeout = args.timeout
    delay = args.ntwk_delay
    output_list = args.output_func
    log_file_name = args.log
    is_debug = args.debug

    name = file_path.split('/')[-1][:-7]

    # get partially applied output function
    output_func_args = output_list[1:]
    output_func_name = output_list[0]
    applied_func = get_applied_output_func(output_func_name, output_func_args)

    logger = DebugLogger.get_logger(name)
    DebugLogger.set_base_logger_info()
    if is_debug:
        DebugLogger.set_base_logger_debug()

    with open(log_file_name, 'a') as f:
        f.write(
            f"\nstart execution of simulation ({name}) at {time.time()}\r\n")

    logger.info('setup sim')
    sim = simpy.Environment(initial_time=0)

    logger.info('setup ensembles')
    rtm = RuntimeManager.TTRuntimeManagerSim(log_file_name, [],
                                             sim,
                                             applied_func,
                                             delay=delay)

    logger.info('send graph inputs')
    sim.process(send_graph_sim(rtm, sim, file_path, logger))

    rtm.manager_ensemble.enter_steady_state(timeout=timeout)

    sim.run(until=timeout)

    print("simulation finished. program output using "
          f"output function '{output_func_name}'")


if __name__ == "__main__":
    main()
