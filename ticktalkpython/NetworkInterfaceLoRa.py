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

import ctypes
from bitstring import Bits, BitArray
from collections import OrderedDict, namedtuple

from .NetworkInterface import TTNetworkInterface
from .NetworkInterface import TTNetworkMessage
from .TTToken import TTToken
from .Tag import TTTag
from .Time import TTTimeSpec
from .Clock import TTClock, TTClockSpec


class TTLoRa(TTNetworkInterface):
    '''
    '''

    def __init__(self,
                 lib_path,
                 func_name,
                 arg_types,
                 arg_wrappers,
                 receiver_function=None):
        # children should call super()__init__(receiver_function) before anything
        # else
        self.lib_path = ctypes.CDLL(lib_path)
        self.func_name = func_name
        self.arg_types = arg_types
        self.arg_wrappers = arg_wrappers
        self.receiver_function = receiver_function

    def send(self, message):
        '''
        Send a TTNetworkMessage through the network interface

        :param message: A message to send through the network interface

        :type message: TTNetworkMessage
        '''
        args = [message]
        self.lib_path[self.func_name](
            [f(x) for f, x in zip(self.arg_wrappers, args)])
        

        
        payload_hex = message.hex()
        transmit(f"AT+SENDB={payload_hex}\r\n".encode())

        return

    def listen(self):
        '''
        Equivalent to receiving; sits in an infinite loop, waiting to receive
        messages over the network interface, and calls the receiver_function on
        the payload of the message, once arrived (and defragmented/stripped of
        headers, where applicable)
        '''
        # In most cases, this should be running as a separate thread. It is IO
        # locked, as it runs in accordance with external mechanisms like a
        # native UDP/IP stack or a WiFi module over SPI
        pass


u8_len = 8
u16_len = 16
u32_len = 32

# encoding is the following
# u8: sq_id, u8: port, u16: context,
# u16: origin_sq_id u16: dev_id/recipient,
# u32: start_time
# u32: end_time
HeaderEntry = namedtuple('HeaderEntry', 'name length')
sq_id_entry = HeaderEntry('sq_id', u16_len)
port_entry = HeaderEntry('port', u8_len)
context_entry = HeaderEntry('context', u8_len)
dev_id_entry = HeaderEntry('dev_id', u16_len)
start_tick_entry = HeaderEntry('start_time', u32_len)
stop_tick_entry = HeaderEntry('stop_time', u32_len)

header_entries = OrderedDict([
    sq_id_entry, port_entry, context_entry,  dev_id_entry,
    start_tick_entry, stop_tick_entry
])


class TTLoRaMessage(TTNetworkMessage):
    '''
    '''

    def check_and_create_bits(self, value, header_entry: HeaderEntry):
        if header_entry.length < len(bin(value)):
            raise TypeError(
                f"{header_entry.name}('{value}') is longer than "
                f'{header_entry.length} bits (has {len(bin(value))} bits)')

        return Bits(uint=value, length=header_entry.length)

    def generate_header_values(self):
        bit_vals = {}

        token = self.payload
        tag = token.tag

        bit_vals[sq_id_entry.name] = self.check_and_create_bits(
            tag.sq, sq_id_entry)
        bit_vals[port_entry.name] = Bits(uint=tag.p, length=port_entry.length)
        bit_vals[context_entry.name] = self.check_and_create_bits(
            tag.u, context_entry)

        bit_vals[dev_id_entry.name] = self.check_and_create_bits(
            self.recipient, dev_id_entry)

        # timing info
        bit_vals[start_tick_entry.name] = Bits(uint=token.time.start_tick,
                                               length=start_tick_entry.length)
        bit_vals[stop_tick_entry.name] = Bits(uint=token.time.stop_tick,
                                              length=stop_tick_entry.length)

        return bit_vals

    def encode_token(self) -> bytes:
        if not isinstance(self.payload, TTToken):
            raise TypeError(
                'LoRa encoding only supported for TTToken payloads.')

        token = self.payload

        # actual data. Might need to encode it first.
        data = token.value

        header_data = self.generate_header_values()

        # do some error checking to ensure we have all the headers
        for entry_name in header_entries:
            if entry_name not in header_data:
                raise TypeError(f'Header construction missing {entry_name}')

        byte_payload = BitArray()

        # ordered dict ensures this is always the same
        for header_entry_name in header_entries:
            byte_payload += header_data[header_entry_name]

        return (byte_payload + data).tobytes()

    @staticmethod
    def decode_bytes(payload: bytes) -> TTToken:
        s = Bits(payload)
        ordered_header_names = list(header_entries.keys())
        ordered_header_lengths = header_entries.values()
        unpacked_data = s.unpack(ordered_header_lengths)
        header_data = dict(zip(ordered_header_names, unpacked_data))

        sq_id = header_data[sq_id_entry.name].int
        port = header_data[port_entry.name].int
        context = header_data[context_entry.name].int
        recipient_device = header_data[dev_id_entry.name].int
        start_tick = header_data[start_tick_entry.name].int
        stop_tick = header_data[stop_tick_entry.name].int
        data = unpacked_data[-1].bytes

        clockspec = TTClockSpec.from_clock(TTClock.root())
        timespec = TTTimeSpec(clockspec, start_tick, stop_tick)

        tag = TTTag(context, sq_id, port, recipient_device)

        return TTToken(data, timespec, tag=tag)
