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

from enum import Enum, unique, auto

class TTMeasurement():
    def __init__(self, unit, value):
        self.unit = unit
        self.value = value

@unique
class TTFrequencyUnit(Enum):
    HZ = auto()
    KHZ = auto()
    MHZ = auto()
    GHZ = auto()

@unique
class TTMeasurementUnit(Enum):
    # The following units and enum values are IEEE 1451.4 (TEDS) compliant
    DEG_K = 0
    DEG_C = 1
    STRAIN = 2
    U_STRAIN = 3
    N = 4
    LB = 5
    KGF = 6
    M_S2 = 7
    GA = 8
    NM_RAD = 9
    NM = 10
    OZ_IN = 11
    PA = 12
    PSI = 13
    KG = 14
    G = 15
    M = 16
    MM = 17
    IN = 18
    MS = 19
    MPH = 20
    FPS = 21
    RAD = 22
    DEG = 23
    RAD_S = 24
    RPM = 25
    HZ = 26
    G_L = 27
    KG_M3 = 28
    MOL_M3 = 29
    MOL_L = 30
    M3_M3 = 31
    L_L = 32
    KG_S = 33
    M3_S = 34
    M3_HR = 35
    GPM = 36
    CFM = 37
    L_MIN = 38
    RH = 39
    PERCENT = 40
    VOLTS = 41
    VOLTS_RMS = 43
    AMP_RMS = 44
    WATTS = 45

    # Additional units are custom for TTPython.
