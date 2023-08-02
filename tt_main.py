from logging import root
from ticktalkpython.SQ import SQify, STREAMify, GRAPHify
from ticktalkpython.Clock import TTClock
from ticktalkpython.Instructions import COPY_TTTIME, READ_TTCLOCK, VALUES_TO_TTTIME
import tt_imu

# All functions called in GRAPHify must be SQify'd, tt_imu is
@GRAPHify
def main():
    with TTClock.root() as root_clock:
        # timestamp
        #start_time = READ_TTCLOCK(trigger, TTClock=root_clock)
        N = 30
        #stop_time = start_time + (1000000 * N) # sample for N seconds
        #sampling_time = VALUES_TO_TTTIME(start_time, stop_time)

        #get_one = COPY_TTTIME(1, sampling_time)
        
        print("test")
        #print(tt_imu.get(trigger))
        print(tt_imu.get(TTClock=root_clock, TTPeriod=500000, TTPhase=0))


main()
