from logging import root
from ticktalkpython.SQ import SQify, STREAMify, GRAPHify
from ticktalkpython.Clock import TTClock
from ticktalkpython.Instructions import COPY_TTTIME, READ_TTCLOCK, VALUES_TO_TTTIME
import tt_imu

@GRAPHify
def main(trigger):
    with TTClock.root() as root_clock:
        print("test")
        #print(tt_imu.get(trigger))
        print(tt_imu.get(trigger, TTClock=root_clock, TTPeriod=500000, TTPhase=1))


main(0xdeadbeef)
