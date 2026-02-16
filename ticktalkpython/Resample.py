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
Deprecated; this should not be included in the documentation. Resample is no
longer supported (but may be in the future) as it appears to be an
overspecification and over-usage of the ``TTClock`` abstraction. It relies on
relabeling samples in a stream to be gapless and unit width intervals to avoid
complexities of synchronizing arbitrarily overlapping intervals for
synchronization, but it comes with a large loss of information. It is unclear if
the simple elegance of this solution/concept is practical enough for realistic
applications.
'''

raise NotImplementedError

from scipy.interpolate import interp1d

from .Clock import TTClock
from .Time import TTTime
from .TTToken import TTToken

def interpolate(resampler, tokens, output_time):
    '''
    Interpolate to order specified in __init__.

    Using a scipy library for the calculations; assume that data is one
    dimensional.
    '''

    # ensure all tokens are timestamped to the same clock
    if not all(map(lambda x: x.time.clock==resampler.input_clock, tokens)):
        raise Exception('Clock', "Tokens are not all of the same clock domain. Ancestor tracing not available in interpolator yet")

    # convert the output token's assumed time label into an ancestor domain;
    # assume the value on the token is interpolated to be the center of the
    # interval
    output_center_timestamp = output_time.ancestor_time(resampler.ancestor_clock).calculate_center_timestamp()

    # ditto for input tokens; convert to ancestor domain and find the center of
    # their interval, assuming that's where the value is meant to be
    # input_center_timestamps = [0 for i in len(tokens)] for i, t in
    # enumerate(tokens): t_ancestor_time = t.time.ancestor_time(ancestor_clock)
    #
    #     input_data_center_timestamps[i] = (t_ancestor_time.stop_tick + t_ancestor_time.start_tick)/2
    input_center_timestamps =  list(map(lambda x: x.time.ancestor_time(resampler.ancestor_clock).calculate_center_timestamp(), tokens))


    # TODO: calculate if input clock's rate is faster tha output clock. If so,
    # low pass filter. Either way, run kth order interpolation (assumed there
    # are k+1 tokens present)

    # use scipy.signal.iirfilter or .firwin

    # Do the interpolation
    #
    # this is an option for an interpolator that's known to be correct, but only
    # for 1-d data

    if resampler.interpolation_order == 0:
        interpolation_kind = 'nearest' #'zero'?
        if len(tokens)==1:
            return TTToken(tokens[0].value, output_time, streaming=True)
    elif resampler.interpolation_order == 1:
        interpolation_kind = 'linear'
        assert len(tokens) >=2
    elif resampler.interpolation_order == 2:
        interpolation_kind = 'quadratic'
        assert len(tokens) >=3
    elif resampler.interpolation_order >= 3:
        interpolation_kind = 'cubic'
        assert len(tokens) >=4

    input_values = list(map(lambda x: x.value, tokens))
    interpolated_history = interp1d(input_center_timestamps, input_values, kind=interpolation_kind)
    output_value = interpolated_history(output_center_timestamp) # will likely be a float; cast back if inputs all integers?

    output_token = TTToken(output_value, output_time, streaming=True)

    return output_token

# Should this be implemented as a function decorator? a child class of the SQ node?
class TTResample():
    '''
    The resampling SQ is meant to run rationally resample an input stream into
    an output stream at the rate of 1 sample per output clock

    The resampler only works on a certain class of inputs. Firstly, This is a
    streaming node that consumes a stream. Secondly, the values within the
    stream are assumed to be sampled periodically at a rate that satisifies the
    Nyquist criterion, thereby allowing continuous signal processing techniques
    like interpolation. Thirdly, the only primitively supported form of
    interpolation is that on 1-d data -- the resampler will not attempt to
    handle structured or vectorized data within the individual tokens, instead
    defaulting to the 'zeroth order' interpolation that simply copies the
    nearest value onto the corresponding output token.

    The input stream is assumed to be gapless with nonngeligible latency s.t.
    this node always has an values it immediately needs present in the waiting
    matching section

    '''
    def __init__(self, output_clock, interpolation_order=0, interpolation_function=None, filter_taps=0, filter_FIR=True):

        global __streaming_graph__
        __streaming_graph__ = True

        #SQ specific stuff
        # self.input_arcs = []
        # self.output_arc = None
        # self.function_name = "Resampler" #TODO make up a random name? add an input arg?
        # self.function_source = None
        # self.functionASTNode = self.execute # This is also the execution part of the SQ; it will produce an array of tokens.

        assert isinstance(output_clock, TTClock)
        self.output_clock = output_clock

        self.interpolation_order = interpolation_order
        # use all history available, but must at least contain interpolation_order+1

        #does this also depend on the input and output rates?
        num_stored_tokens = max(filter_taps, interpolation_order+1)

        self.input_token_history = [None for i in range(num_stored_tokens)]
        #self.output_token_history = None # Necessary if we are using an IIR LPF
        self.history_index = 0

        if interpolation_function:
            self.interpolation_function = interpolation_function
        else:
            self.interpolation_function = interpolate

    def config_clock(self, input_clock):

        assert isinstance(input_clock, TTClock)
        self.input_clock = input_clock

        self.ancestor_clock = TTClock.common_ancestor(input_clock, self.output_clock)

        # the input and output clocks are at different rates, and perhaps
        # periods. Consider (w.r.t. ancestor), input arrives every M ticks, and
        # output produced every N ticks Then we should produce N/M tokens per
        # input token. Obviously fractions don't make sense
        base_input_interval = TTTime(input_clock, 0, 1)
        ancestor_input_interval = base_input_interval.ancestor_time(self.ancestor_clock)
        # the number of ancestor ticks per input. Call this M
        self.in_ticks = ancestor_input_interval.n_ticks()

        base_output_interval = TTTime(self.output_clock, 0, 1)
        ancestor_output_interval = base_output_interval.ancestor_time(self.ancestor_clock)
        # the number of ancestor ticks per output. Call this N
        self.out_ticks = ancestor_output_interval.n_ticks()

        self.next_output_time = None #fill this in later

        # if the output rate is slower than the input, then we're going to need
        # a low-pass filter to satisfy the DSP overlords
        #
        # TODO: figure out filter characteristics. IIR vs. FIR. #taps. Cutoff
        # frequency (last can be assumed, right?) For now, just don't do it...
        # first things first

        self.useLPF = True if self.out_ticks>self.in_ticks else False


    def execute(self, new_token):

        #  ensure this is from the same clock as the others, else through an error
        if not new_token.time.clock == self.input_clock:
            print('New token with never before seen clock! Flush history and reset!!')
            self.config_clock(new_token.time.clock)

            self.input_token_history = [None for i in range(len(self.input_token_history))]
            self.history_index = 0
            # print(new_token.time)
            # print(self.input_clock)
            # print(self.input_token_history)

            # raise Exception('Clock', "Inconsistency in resampler clock input!
            # We're not smart enough to switch yet without changing context
            # 'u'!")

        # Add token to history
        # new_token.time = new_token.time.ancestor_time(self.ancestor_clock)
        # Overwrite any that are no longer needed.
        #  HARD ASSUMPTION: ORDER IS PRESERVED IN TOKEN ARRIVAL/RELEASE TO THIS SQ!
        self.input_token_history[self.history_index] = new_token
        self.history_index = (self.history_index + 1) % len(self.input_token_history)

        output_tokens = []

        if any(t is None for t in self.input_token_history):
            return output_tokens #not enough data to produce a value... so don't (interpolation won't work properly ...)

        if self.next_output_time is None:
            self.next_output_time = self.find_next_output_time(first=True)
        # else:
            # self.next_output_time = self.find_next_output_time()

        input_history = self.time_order_history(self.input_token_history)

        while self.is_output_time_valid(input_history, self.next_output_time):
            # print('interpolate and create new token for output time %s' % self.next_output_time)
            #interpolate and lPF
            #calculate new output time
            #lather rinse repeat

            #TODO: if token value is not interpolable (int or float) or
            #interpolation order is 0, the new token should simply copy the
            #nearest input token's value.
            new_output_token = self.interpolation_function(self,input_history, self.next_output_time)

            #generate new token and add to output array

            output_tokens.append(new_output_token)

            self.next_output_time = self.find_next_output_time()


        return output_tokens


        # exec_start_counter = self.counter while self.counter+self.out_ticks <=
        # exec_start_counter+self.in_ticks: #interpolate using history and
        # generate new value. Time-label on new value is centered nearest the
        # center of the supplied history (supplied history _may_ be a subset of
        # all of it.)

        #     self.counter = (self.counter + self.out_ticks) % self.mod

    def find_next_output_time(self, first=True):

        if self.next_output_time is None:
            #it's the first time. Put this near the center of the history, of
            #which there is enough to interpolate to necessary degree.
            pass
            # assume there is enough history to interpolate to necessary order.
            #  find center all the existing intervals, and put the next start
            #  time close to that assume we have enough history to do all
            #  further operations. We'll find the center of that entire
            #  interval, truncate to an earlier time-label in the output domain,
            #  and go from there (checking to ensure it's still within the )
            input_history = self.time_order_history(self.input_token_history)

            #calculate the entire interval across the history
            history_interval = TTTime(input_history[0].time.clock, input_history[0].time.start_tick, input_history[-1].time.stop_tick)


            #represent in ancestor domain
            ancestor_history_interval = history_interval.ancestor_time(self.ancestor_clock)
            #find the center of that interval
            center_timestamp = int(ancestor_history_interval.calculate_center_timestamp())
            #just doing this to make use of the child_time conversion function
            ancestor_center = TTTime(self.ancestor_clock, center_timestamp, center_timestamp+1)

            # convert into the output clock's domain
            #
            # function will truncate non-integer part of conversion, so both
            # times will be on the early side
            output_time = ancestor_center.child_time(self.output_clock)
            # output_time.stop_tick += 1

            # self.next_output_time = output_time
            return output_time


        else:
            output_time = TTTime(self.output_clock, self.next_output_time.start_tick+1, self.next_output_time.stop_tick+1)

            return output_time

    def is_output_time_valid(self, input_history, output_time, history_guard_index=0):
        '''
        Check if the supplied output time is valid within the supplied history.
        Optional 'guard' offset to look at a smaller set of inputs.
        '''


        assert(history_guard_index>=0 and isinstance(history_guard_index, int))

        output_ancestor_time = output_time.ancestor_time(self.ancestor_clock)

        # find the center timestamp (as a floating point) in the ancestor domain
        # for the output time and the first & last tokens in the history (NB:
        # first and last may not be conservative enough, and least ensures that
        # we are interpolating and not extrapolating)
        output_ancestor_center = output_ancestor_time.calculate_center_timestamp()
        input_first_ancestor_center = input_history[0+history_guard_index].time.ancestor_time(self.ancestor_clock).calculate_center_timestamp()
        input_last_ancestor_center = input_history[-1-history_guard_index].time.ancestor_time(self.ancestor_clock).calculate_center_timestamp()

        # if len(input_history==1):
        if self.interpolation_order == 0 or len(input_history)==1:

            assert input_first_ancestor_center==input_last_ancestor_center
            # print(output_ancestor_center)
            # print(input_first_ancestor_center)

            diff = output_ancestor_center - input_first_ancestor_center
            # should be using whichever input would be closest to this one
            is_nearest = abs(diff) <= abs(diff + self.in_ticks)
            # ensure we don't get ahead of ourselves; stay within a tick of the
            # output or input clock
            is_within_tick = abs(diff) < self.out_ticks or abs(diff) < self.in_ticks

            return is_nearest and is_within_tick
        else:
            if output_ancestor_center < input_first_ancestor_center:
                print("Output is too far in the past; how will we catch up??")

            # check that center of interval is between the first and last samples
            return (output_ancestor_center >= input_first_ancestor_center) and (output_ancestor_center <= input_last_ancestor_center)



    def low_pass_filter(self, tokens):
        #### For structured data, this is not tractable to generalize
        return tokens

    def time_order_history(self, history):
        '''
        Returns the tokens in a sorted array, which should simply mean slicing
        up the existing history in a circular buffer to be time ordered based on
        the tokens. Assumes that we want to use the entire history provided, not
        a subset.

        history: a list of tokens, stored as a circular buffer than should be
        reordered for simpler processing
        '''

        #organize by the start ticks, so we can reorder to be a list in
        #monotonic increasing order
        start_ticks = [token.time.start_tick for token in history]

        earliest = min(start_ticks)
        ind = start_ticks.index(earliest)

        # reorder by slicing around the minimum valued. May be necessary to
        # fully re-ort based on center timestamp or start_ticks?
        new_history  = history[ind:] + history[0:ind]

        #check for duplcates
        assert len(new_history) == len(history)
        #check for monotonicity
        for i in range(1,len(new_history)):
            assert new_history[i-1].time.start_tick < new_history[i].time.start_tick

        return new_history
