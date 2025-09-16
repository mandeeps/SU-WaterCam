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


from .Component import TTComponent
from .Measurement import TTFrequencyUnit
from .Error import TTComponentError
from .Query import TTQComponent

class TTOscillator(TTComponent):
    def __init__(self, name, frequency_value, frequency_unit, stability, custom_fields=None):
        super().__init__(name, {} if custom_fields is None else custom_fields)
        if isinstance(frequency_value, float) and frequency_value > 0:
            if not isinstance(frequency_unit, TTFrequencyUnit()):
                raise TTComponentError("A TTOscillator component's frequency unit must be of type TTFrequencyUnit.")
        else:
            raise TTComponentError("A TTOscillator component's frequency must be a positive float.")
        if not isinstance(stability, int) or stability <= 0:
            raise TTComponentError("A TTOscillator component's stability must be a positive integer.")

class TTQOscillator(TTQComponent):
    pass

def oscillator():
    pass
