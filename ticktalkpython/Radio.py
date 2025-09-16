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
import re

from .Component import TTComponent
from .Error import TTComponentError


class TTRadio(TTComponent):
    def __init__(self, name, custom_fields=None):
        super().__init__(name, {} if custom_fields is None else custom_fields)

    def send(self):
        pass

    def receive(self):
        pass


# matches addresses separated by colons or dashes, exclusively
MAC_ADDR_REGEX = re.compile(r"^([A-Za-z0-9]{2}[:\-]){5}[A-Za-z0-9]{2}$")


class TTWIFI(TTRadio):
    def __init__(self, name, mac_address, protocol, custom_fields=None):
        super().__init__(name, {} if custom_fields is None else custom_fields)
        if not isinstance(protocol, WLANProtocol):
            raise TTComponentError("A TTWIFI's 'protocol' must be of type 'Radio.WLANProtocol'.")
        self.protocol = protocol
        if MAC_ADDR_REGEX.fullmatch(mac_address) is None:
            raise TTComponentError("The provided MAC address is incorrectly formatted: " + mac_address)
        self.mac_address = mac_address


@unique
class WLANProtocol(Enum):
    LEGACY = auto()
    YR2007 = auto()
    YR2012 = auto()
    YR2016 = auto()
    A = auto()
    B = auto()
    G = auto()
    N = auto()
    AC = auto()
    AD = auto()
    AF = auto()
    AH = auto()
    AI = auto()
    AJ = auto()
    AQ = auto()
    AX = auto()
    AY = auto()
    BA = auto()
    BE = auto()
