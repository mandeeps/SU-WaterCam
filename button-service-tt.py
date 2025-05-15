#!/home/pi/SU-WaterCam/venv/bin/python3
# Simple script to run as a daemon (with SystemD or other init)

import time
import signal

from gpiozero import Button

from ticktalkpython import DebugLogger
from ticktalkpython import RuntimeManager
from ticktalkpython.IPC import *

from runrtm import unpack_graph, send_input_tokens
from output_functions import get_applied_output_func

###### Config

pickled_graph_file_path = './output/ticktalk_main.pickle'
name = 'ondemand-cam'
log_file_name = './output.log'
ip = '127.0.0.1'
port = 8080
logger = DebugLogger.get_logger(log_file_name)
output_func_name = 'log_msg_to_file'
output_func = get_applied_output_func(output_func_name, [log_file_name])
rtm = None


# using signal as an alarm for testing
# def alarm_press(signum, stack)
#     print(f"Button DOWN on pin")
#     global graph, logger, rtm
#     send_input_tokens(graph, logger, rtm)


def single_press(button):
    print(f"Button DOWN on pin {button.pin}")
    global graph, logger, rtm
    send_input_tokens(graph, logger, rtm)

# Using GPIO 5 because it is HIGH by default and we connect it to ground
# by pushing the button in. Already using GPIO 6 for the Lepton reset function
# Adjust button GPIO as needed


if __name__ == "__main__":
    button = Button(5)

    with open(log_file_name, 'a') as logfile:
        logfile.write(f'\nstart execution of phy ({name}) at %f\n' %
                        time.time())

    rtm = RuntimeManager.TTRuntimeManagerPhysical(ip, port, port + 1,
                                                    log_file_name,
                                                    output_func)

    time.sleep(0.5)

    graph = unpack_graph(pickled_graph_file_path)
    instantiate_graph_msg = Message(RuntimeMsg.InstantiateAndMapGraph,
                                    graph, Recipient.ProcessRuntimeManager)
    rtm.send_to_runtime(instantiate_graph_msg)

    time.sleep(2)

    button.when_released = single_press # Call on release
    # signal.signal(signal.SIGALRM, single_press)
    # signal.alarm(3)

    rtm.manager_ensemble.enter_steady_state()
