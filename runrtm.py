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

import argparse
import pickle
import time
#import timedinput
import logging

from typing import Dict, Any, TYPE_CHECKING

from ticktalkpython import DebugLogger
from ticktalkpython import RuntimeManager
from ticktalkpython import Graph
from ticktalkpython.IPC import *
from ticktalkpython.Constants import get_readable_time

from output_functions import get_applied_output_func


def unpack_graph(filename):
    inpickle = open(filename, 'rb')
    graph = pickle.load(inpickle)
    assert isinstance(graph, Graph.TTGraph)
    return graph


# TODO: input values should be modifiable by the user.
# What format should this be in? perhaps JSON? {input: val}
def send_input_tokens(graph: Graph.TTGraph,
                      logger,
                      runtime_manager: RuntimeManager.TTRuntimeManager,
                      inputs: Dict[str, Any] = None):
    if inputs is not None:
        graph_inputs = inputs
    else:
        graph_inputs = {}
        for input_var in graph.source_var_names():
            graph_inputs[input_var] = 0xdeadbeef

    execute_graph_message = Message(RuntimeMsg.ExecuteGraphOnInputs,
                                    (graph, graph_inputs),
                                    Recipient.ProcessRuntimeManager)

#    logger.info('Sending token inputs\n\n\n')

    runtime_manager.send_to_runtime(execute_graph_message)


def run_application_rtm(name,
                        pickled_graph_file_path,
                        ip,
                        port,
                        output_func,
                        log_file_name,
                        logger,
                        timeout,
                        subscription_time,
                        in_jupyter=False):

    try:
        #        with open(log_file_name, 'a') as logfile:
#            logfile.write(f'\nstart execution of phy ({name}) at '
#                          f'{get_readable_time(time.time())}\n')

        rtm = RuntimeManager.TTRuntimeManagerPhysical(ip, port, port + 1,
                                                      log_file_name,
                                                      output_func)

#        time.sleep(0.5)
        # timedinput doesn't play nice with jupyter notebooks, hack around it
        # It also doesn't work when running runrtm.py through a systemd unit file
        #if in_jupyter:
#        time.sleep(subscription_time)
        #else:
        #    timedinput.timedinput(
        #        f'wait for {subscription_time} secs for devices '
        #        'to connect... hit enter\n\n', subscription_time, ' ')

        graph = unpack_graph(pickled_graph_file_path)
        instantiate_graph_msg = Message(RuntimeMsg.InstantiateAndMapGraph,
                                        graph, Recipient.ProcessRuntimeManager)
        rtm.send_to_runtime(instantiate_graph_msg)

 #       time.sleep(2)
        send_input_tokens(graph, logger, rtm)

        if timeout <= 0:
            rtm.manager_ensemble.enter_steady_state()
        else:
            rtm.manager_ensemble.enter_steady_state(timeout)

    except KeyboardInterrupt:
        print('KB interrupt; exit physical test')


def main():
    parser = argparse.ArgumentParser(
        description='instantiate the runtime manager for a TTPython program')

    parser.add_argument('file',
                        metavar='F',
                        type=str,
                        help='the pickled dataflow graph to execute')
    parser.add_argument(
        '--ip',
        default='127.0.0.1',
        help='the ip of the runtime manager (default: localhost:127.0.0.1)')
    parser.add_argument('port', help='the port of the runtime manager')
    parser.add_argument(
        '--timeout',
        default=60,
        type=float,
        help='runtime manager timeout (default: 60 (sec), 0 for infty)')
    parser.add_argument(
        '-o',
        '--output_func',
        type=str,
        default=['log_msg_to_file', './output.log'],
        nargs='*',
        help='the str name of the function found in module '
        '"output_functions" used to send tokens with no correponding '
        'downstream SQ (default: -o log_msg_to_file ./output.log). You '
        'mustprovide the argument list to execute said function. '
        'The function assumes the last argument is a class type Msg '
        '(in IPC.py) to write, which should not be included in the provided '
        'arg list.')
    parser.add_argument(
        '--log',
        default='./output.log',
        help='specify the log file used to capture runtime behavior')
    parser.add_argument(
        '-d',
        '--debug',
        nargs='?',
        const=logging.DEBUG,
        default=logging.INFO,
        help='set level for Python logger. '
        'Add an additional argument to only get function call profiling')
    parser.add_argument(
        '-s',
        '--sub_time',
        default=3600,
        type=int,
        help='how many seconds to wait for devices to connect (default 1 hr)')
    parser.add_argument(
        '-j',
        '--jupyter_compat',
        action='store_true',
        help='set this argument if you use this in a Jupyter Notebook.')

    args = parser.parse_args()
    file_path = args.file
    ip = args.ip
    port = int(args.port)
    timeout = args.timeout
    output_list = args.output_func
    log_file_name = args.log
    debug_level = args.debug
    subscription_time = args.sub_time
    in_jupyter = args.jupyter_compat

    # get partially applied output function
    output_func_args = output_list[1:]
    output_func_name = output_list[0]
    applied_func = get_applied_output_func(output_func_name, output_func_args)

    # remove .pickle file extension
    name = file_path.split('/')[-1][:-7]

    logger = DebugLogger.get_logger(name)

    DebugLogger.set_base_logger_info()
    if debug_level is logging.DEBUG:
        DebugLogger.set_base_logger_debug()
    elif debug_level is not logging.INFO:
        DebugLogger.set_base_logger_profiling()

    run_application_rtm(name, file_path, ip, port, applied_func, log_file_name,
                        logger, timeout, subscription_time, in_jupyter)

    print("runtime shutdown. program output using "
          f"output function '{output_func_name}'")


if __name__ == "__main__":
    main()
