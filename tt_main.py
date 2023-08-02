from logging import root
from ticktalkpython.SQ import SQify, STREAMify, GRAPHify
from ticktalkpython.Clock import TTClock
from ticktalkpython.Instructions import COPY_TTTIME, READ_TTCLOCK, VALUES_TO_TTTIME
import tt_imu

@GRAPHify
def main(trigger):
    A_1 = 1
    with TTClock.root() as root_clock:
        start_time = READ_TTCLOCK(trigger, TTClock=root_clock)
        N = 30
        # Setup the stop-tick of the STREAMify's firing rule
        stop_time = start_time + (1000000 * N) # sample for N seconds

        # create a sampling interval by copying the start and stop tick from
        # TTToken values to the token time interval
        sampling_time = VALUES_TO_TTTIME(start_time, stop_time)

        # copy the sampling interval to the input values to the STREAMify
        # node; these input values will be treated as sticky tokens, and
        # define the duration over which STREAMify'd nodes must run
        sample = COPY_TTTIME(1, sampling_time)

        # do the sampling with streamify'd SQs. Only one of the inputs needs
        # the special sampling time interval (but it wouldn't hurt if all did)
        # because the other const values have infinite timestamps
        euler = tt_imu.get(sample,
                                  1,
                                  1,
                                  TTClock=root_clock,
                                  TTPeriod=500000,
                                  TTPhase=0,
                                  TTDataIntervalWidth=100000)

        #return euler

        #result = tt_imu.get(trigger, TTClock=root_clock, TTPeriod=500000, TTPhase=1)
