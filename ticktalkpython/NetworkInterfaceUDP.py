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
A UDP network interface for TTPython. Generally, we decided to use UDP datagrams
to avoid the overhead and maintenance of TCP connections. Best-efforts is an
underlying pillar of TickTalk.

When incoming messages arrive to this Ensemble over the port we listen, the
message payloads are provided to a callback function, which typically means
inserting the message into a queue for another process/thread to respond to.

We include the option to use guaranteed delivery via a short handshaking and
acknowledgement protocol, but this is not explicilty necessary. By default, that
option is enabled for control-plane messages (graph setup, joining process,
distributing SQs) whereas data-plane messages (sending tokens between SQs) are
simply best-effort. This stack will automatically split large payloads into
multiple datagrams, which are accounted for in this guaranteed-delivery process.

A single port is used for sending and receiving messages, partially so the
handshaking process can be managed all in one place. Generally, our network
implementation has one UDP interface created for outgoing messages and another
one for incoming messages; these must use different ports.
'''
# The single TX port is probably not the right approach, but we leave that as is
# for now. In reality, each new transmission should probably spawn a new thread
# that uses an unused port to send the message, rather than maintaining all
# transmissions in a single thread, since each exchange is independent of the
# others. This is how network stacks generally operate anyway. The abstraction
# is worth the context-switching overhead.

from enum import Enum
import socket
import re
import logging
import pickle
import threading
from threading import Lock
from datetime import datetime
import math
import time
import selectors

from .NetworkInterface import TTNetworkInterface
from .NetworkInterface import TTNetworkMessage
from . import DebugLogger

logger = DebugLogger.get_logger('UDPNetwork')
logger.setLevel(logging.WARNING)

# Standard TX and RX ports -- 9-key versions of 'TICK' and 'TALK' for TX, RX,
# respectively. Note that the TX port is not necessary to know a priori in the
# general case, but this UDP implementation routes all outgoing data through the
# same port. An alternate strategy would be to use a single port for all
# externally-initiated communications, and spawning a new thread to support any
# outgoing messages, independent of the rest of the stack.

TX_PORT = 8425
RX_PORT = 8225

# When a TTNetworkMessage is sent, it is serialized into multiple packets, each
# of which has header information. These constants are used to ensure that the
# process of receiving and formatting these packets is handled without trimming
# or losing bytes.

DATA_HEADER = 17
ACK_SIZE = 17
REQ_SIZE = 19

# Packet size is capped at the ethernet standard's maxiumum of 1500 bytes per
# packet. 1500 observed to be problematic through tunnel/openvpn interface; 1200
# works better. Issues are not present on localhost or local lan/wlan. Cause
# unknown. 1217 to make the actual payload 1200
MAX_PACKET_SIZE = 1217
PAYLOAD_BYTE_LENGTH = MAX_PACKET_SIZE - DATA_HEADER

# Packets are enqueued in a "window". The window size dictates how many packets
# can be sent/received at a given moment.
WINDOW_SIZE = 1000

# Each packet is accompanied by a checksum calculated using this standard 32-bit
# CRC polynomial
CRC_POLYNOMIAL = 0x04C11DB7


MIN_TO_RECV = max(ACK_SIZE, REQ_SIZE, DATA_HEADER + PAYLOAD_BYTE_LENGTH)
MIN_PAYLOAD = min(DATA_HEADER + PAYLOAD_BYTE_LENGTH, ACK_SIZE, REQ_SIZE)

IPV4_REGEX = r'^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$'
MIN_PORT = 1024
MAX_PORT = 65535
TIMEOUT = 100  # ms

# PACKET              (bytes)
# = [type]                  1
# + [message_id]            8
# + [sequence_num]          4
# + [data]                  n
# + [crc]                   4

# REQ_TO_SEND         (bytes)
# = [type]                  1
# + [message_id]            8
# + [message_type]          2
# + [num_packets]           4
# + [crc]                   4

# ACK                 (bytes)
# = [type]                  1
# + [message_id]            8
# + [sequence_num]          4
# + [crc]                   4

def get_public_ip():
    import requests
    ip_addr = requests.get('https://checkip.amazonaws.com').text.strip()
    return ip_addr

def separate_ip_and_port(str_in):
    # string in format "ip:port", e.g. 10.200.0.1:8425

    ip_addr = str_in.split(':')[0].strip()
    port = str_in.split(':')[1].strip()
    return ip_addr, port

class TTPacketType(Enum):
    '''
    Packets can either be a request to send (REQ), a data packet containing a
    portion of a message (DATA), or an acknowledgement that a particular data
    packet has been received (ACK). These are encoded as a single byte at the
    front of every packet.
    '''
    REQ = 0
    DATA = 1
    ACK = 2

class TTPacket():
    '''
    A wrapper object representing a payload containing a REQ, ACK, or DATA
    packet. When the send() method is called, the time in milliseconds is
    recorded for use in calculating if this packet has timed out.

    :param packet_type: The type of TTPacket

    :type packet_type: TTPacketType

    :param message_id: the unique ID representing the TTNetworkMessage object
        contained within.

    :type message_id: int
    '''

    def __init__(self, packet_type, message_id):
        self.packet_type = packet_type
        self.message_id = message_id
        self.time_sent = 0

    def send(self, server_socket, address):
        '''
        :param server_socket: The given TTNetwork instance's open server socket.

        :type server_socket: Socket

        :param address: The recipient's IP and port.

        :type address: Address
        '''
        # TODO: ensure that the epoch for datetime.now() matches the constraints
        # of the program.
        self.time_sent = datetime.now().microsecond
        payload = convert_to_payload(self)
        server_socket.sendto(payload, address)

class TTDataPacket(TTPacket):
    '''
    A wrapper object representing a DATA packet.

    :param sequence_num: The sequence number for the packet.

    :type sequence_num: int

    :param payload: The data and header bytes to be sent in this packet.

    :type payload: bytearray

    :param ACKed: Indicates whether this message has been ACKed.

    :type ACKed: bool

    :param message_id: the unique ID representing the TTNetworkMessage object
        contained within.

    :type message_id: int
    '''

    def __init__(self, message_id=0, sequence_num=0, payload=None, ACKed=False):
        super().__init__(TTPacketType.DATA, message_id)
        self.sequence_num = sequence_num
        self.payload = [] if payload is None else payload
        self.ACKed = ACKed

    def send(self, server_socket, address):
        '''
        :param server_socket: The given TTNetwork instance's open server socket.

        :type server_socket: Socket

        :param address: The recipient's IP and port.

        :type address: Address
        '''
        if __debug__:
            logger.debug('DATA_SEND to %s:%s; #=%s, ID=%s, len=%s' % (
                address[0], address[1], self.sequence_num, self.message_id, len(self.payload)))
        super().send(server_socket, address)

class TTAckPacket(TTPacket):
    '''
    A wrapper object representing an ACK packet.

    :param sequence_num: The sequence number for the packet.

    :type sequence_num: int

    :param message_id: the unique ID representing the TTNetworkMessage object
        contained within.

    :type message_id: int
    '''

    def __init__(self, message_id, sequence_num):
        super().__init__(TTPacketType.ACK, message_id)
        self.sequence_num = sequence_num

    def send(self, server_socket, address):
        '''
        :param server_socket: The given TTNetwork instance's open server socket.

        :type server_socket: Socket

        :param address: The recipient's IP and port.

        :type address: Address
        '''
        if __debug__:
            logger.debug('ACK_SEND to %s; #=%s, ID=%s' % (
                str(address[0]) + ":" + str(address[1]), self.sequence_num, self.message_id))
        super().send(server_socket, address)

class TTRequestPacket(TTPacket):
    '''
    A wrapper object representing a REQ packet.

    :param message_id: the unique ID representing the TTNetworkMessage object
        contained within.

    :type message_id: int

    :param num_packets: the number of packets for the receiver to expect for the
        given message.

    :type num_packets: int
    '''

    def __init__(self, message_id, num_packets):
        super().__init__(TTPacketType.REQ, message_id)
        self.num_packets = num_packets

class TTNetworkMessageUDP(TTNetworkMessage):
    '''
    An encapsulation of a message to be sent into the network, consisting of a
    recipient, and a payload of arbitrary form. There are no restrictions on
    size of the payload; the Message is simply an encapsulation mechanism.

    :param recipient_ip: The ip of an ensemble to send a message to.

    :type recipient_ip: str

    :param recipient_port: The port that the recipient ensemble is listening on.

    :type recipient_port: int

    :param payload_byte_array: The actual data to send within the message. The
        format/type within the payload is arbitrary, depending ony on the
        message_type

    :type payload_byte_array: bytearray
    '''

    def __init__(self,
                 recipient_ip="127.0.0.1",
                 recipient_port=8080,
                 payload_byte_array=None):
        # the message_id is guaranteed to be unique throughout the entirety of
        # the sending process, as the duration of sending is also the lifetime
        # of this TTNetworkMessage object.
        self.message_id = id(self)

        self.recipient_ip = recipient_ip
        self.recipient_port = recipient_port
        # self.payload = [] if payload_byte_array is None else payload_byte_array
        super().__init__(
            [] if payload_byte_array is None else payload_byte_array,
            str(recipient_ip) + ":" + str(recipient_port))

        # for use later on in encoding a REQ packet for this message
        self.num_packets = math.ceil(len(self.payload)/PAYLOAD_BYTE_LENGTH)

    def get_request(self):
        '''
        Generates a TTRequestPacket instance for this message.
        '''
        return TTRequestPacket(self.message_id, self.num_packets)

    def generate_packets(self, window_size):
        '''
        Splits the message's payload_byte_array into a series of DATA packets
        and returns them as a list. The window_size parameter is used to enforce
        the constraint that the maximum sequence number of a packet must be more
        than twice the size of the window to ensure that collisions cannot
        occur.

        :param window_size: The size of the TTSlidingWindow in which this
            message will be sent.

        :type window_size: int
        '''
        packets = []
        sequence_num = 0
        while len(self.payload) >= PAYLOAD_BYTE_LENGTH:
            chunk = self.payload[:PAYLOAD_BYTE_LENGTH]
            packet = TTDataPacket(self.message_id, sequence_num, chunk)
            packets.append(packet)
            sequence_num = (sequence_num + 1) % ((2 * window_size) + 1)
            del self.payload[:PAYLOAD_BYTE_LENGTH]
        if len(self.payload) > 0:
            packet = TTDataPacket(self.message_id, sequence_num, self.payload)
            packets.append(packet)
        return packets

    def get_address(self):
        '''
        Return the destination address as an Address tuple object for use with
        Python's UDP interface.
        '''
        return (self.recipient_ip, self.recipient_port)


def message(obj, recipient_ip="127.0.0.1", recipient_port=8080):
    '''
    Provides a shorthand form for the TTNetworkMessage constructor, and handles
    serialization of the message's contents.
    '''
    payload = bytearray(pickle.dumps(obj))
    return TTNetworkMessageUDP(recipient_ip, recipient_port, payload)

def extract(bytes_in, start, end):
    return int.from_bytes(bytes_in[start:end], 'little')

def convert_int(val):
    return val.to_bytes(4, 'little')

def convert_long(val):
    return val.to_bytes(8, 'little')

def convert_short(val):
    return val.to_bytes(2, 'little')

def convert_byte(val):
    return val.to_bytes(1, 'little')

def crc(payload):
    # Given a bytearray, calculates the 32-bit CRC checksum.
    # credit: http://www.sunshine2k.de/articles/coding/crc/understanding_crc.html

    crc_value = 0
    for index in range(0, len(payload)):
        crc_value = crc_value ^ (payload[index] << 24)
        for _ in range(0, 8):
            if crc_value & 0x80000000 != 0:
                crc_value = (crc_value << 1) ^ CRC_POLYNOMIAL
            else:
                crc_value = crc_value << 1
    return crc_value & 0xFFFFFFFF

def convert_to_packet(payload):
    '''
    Given a bytearray, produces a TTPacket instance for use by the receiver.
    Exceptions will be thrown if the given array doesn't match the minimum
    lengths and formats required for a message. The value 'None' will be
    returned if the CRC check fails, in which case the TTNetwork should ignore
    the message, triggering the timeout on the sender's side.
    '''
    if len(payload) < MIN_PAYLOAD:
        raise Exception(
            "A packet must be at least " + str(DATA_HEADER) +
            " bytes long to account for its header information; only " +
            str(len(payload)) + " bytes are present.")

    crc_test = crc(payload)
    if __debug__:
        logger.debug("Receiving CRC: " + hex(crc_test))
    if crc_test != 0:
        if __debug__:
            logger.debug('CRC failed; bit errors detected')
        return None
    else:
        if __debug__:
            logger.debug('CRC Passed')

    packet_type = TTPacketType(payload[0])
    message_id = extract(payload, 1, 9)
    if packet_type == TTPacketType.DATA:
        sequence_num = extract(payload, 9, 13)
        data_section = payload[13:len(payload) - 4]
        return TTDataPacket(message_id, sequence_num, data_section)
    elif packet_type == TTPacketType.ACK:
        sequence_num = extract(payload, 9, 13)
        return TTAckPacket(message_id, sequence_num)
    else:
        # message_type = extract(payload, 9, 11)
        num_packets = extract(payload, 11, 15)
        return TTRequestPacket(message_id, num_packets)

def convert_to_payload(packet):
    '''
    Given a TTPacket instance, converts into a bytearray for sending over
    Python's UDP interface.
    '''
    packet_type_byte = convert_byte(packet.packet_type.value)
    message_id_bytes = convert_long(packet.message_id)
    payload = bytearray()
    if packet.packet_type == TTPacketType.DATA:
        sequence_num_bytes = convert_int(packet.sequence_num)
        payload = packet_type_byte + message_id_bytes + \
            sequence_num_bytes + packet.payload
    elif packet.packet_type == TTPacketType.ACK:
        sequence_num_bytes = convert_int(packet.sequence_num)
        payload = packet_type_byte + message_id_bytes + sequence_num_bytes
    else:
        # message_type_bytes = convert_short(packet.message_type.value)
        # removing message type from direct packet encoding; it has no
        # relevance at the network transport layer
        message_type_bytes = convert_short(0)
        num_packets_bytes = convert_int(packet.num_packets)
        payload = packet_type_byte + message_id_bytes + \
            message_type_bytes + num_packets_bytes
    crc_check = crc(payload)
    if __debug__:
        logger.debug("Sending CRC: " + hex(crc_check))
    return payload + crc_check.to_bytes(4, 'big', signed=False)

class TTSlidingWindow():
    '''
    TTSlidingWindow is an implementation of the Selective Repeat sliding window
    protocol, and is used to send AND receive every type of packet used in
    TTNetwork. Though it is contained and used by the TTNetwork's primary
    message-receiving thread, it has its own timeout thread for the message that
    it represents. A receiving window is distinguished from a sending window
    based on the type of the initializer object; a receiving window is
    initialized by a TTRequestPacket, while a sending window is initialized by a
    TTNetworkMessage object.

    :param window_size: The number of packets that can be sent at once through
        this sliding window.

    :type window_size: int

    :param timeout_ms: The number of milliseconds that the timeout thread waits
        for before checking for timed-out packets

    :type timeout_ms: int

    :param completion_fn: A lambda to be called upon completion of the sending
        or receiving operation

    :type completion_fn: lambda TTNetworkMessage

    :param initializer: A TTRequestPacket (receiving) or TTNetworkMessage
        (sending) used to determine if the window will be used for sending or
        receiving.

    :type initializer: TTRequestPacket | TTNetworkMessage
    '''
    def __init__(self,
                 window_size=WINDOW_SIZE,
                 timeout_ms=TIMEOUT,
                 completion_fn=(lambda msg: logger.debug(msg)),
                 initializer=None):
        self.timeout_ms = timeout_ms
        # the minimum sequence number being sent
        self.left_end = 0

        # the maximum sequence number being sent
        self.right_end = window_size-1

        self.window_size = window_size

        # indicates whether the timeout thread should terminate
        self.active = False

        # protects the window from data race conditions
        self.mutex = Lock()

        # indicates whether the full message has been received based on the
        # number of packets to expect from its REQ
        self.num_received = 0

        self.initializer = initializer
        self.completion_fn = completion_fn

        if isinstance(initializer, TTRequestPacket):
            # indicates whether the window will be sending or receiving.
            self.is_sending = False

            # a receiving window needs a slot allocated for every packet that
            # could come in
            self.window = [None] * window_size

            # indicates the number of packets to expect from the sender and the
            # message's type (REQ, ACK, DATA)
            self.packet_count = initializer.num_packets

            # the non-header contents of the message received thus far
            self.received_object_bytes = bytearray()

        elif isinstance(initializer, TTNetworkMessageUDP):
            self.is_sending = True

            # a sending window will be full of packets to send, but only those with
            # sequence numbers in the left_end and right_end range will be sent at a
            # given moment
            self.window = initializer.generate_packets(self.window_size)
            self.packet_count = len(self.window)
        else:
            raise Exception("A TTSlidingWindow must have an initializer "
                            "of type TTNetworkMessage or TTRequestPacket")

    def slide(self, server_socket, address):
        '''
        Remove all ACK-ed packets/Received packets from the window and allow an
        equal number to be sent/received at the end of the window.

        :param server_socket: the parent TTNetwork's open server socket

        :type server_socket: Socket

        :param address: the address used to send packets that enter the window
            after sliding (if sender)

        :type address: Address

        '''
        while (len(self.window) > 0 and self.window[0] is not None
               and (not self.is_sending or self.window[0].ACKed)):
            popped = self.window.pop(0)
            if not self.is_sending:
                # if receiving, collect the next packet's payload
                self.received_object_bytes = self.received_object_bytes + popped.payload
                # ensure that an empty slot is in place for next message at the
                # right_end of the window
                self.window.append(None)
            elif len(self.window) >= self.window_size:
                # a sending window will send the next packet that enters the
                # window, if there's one that has yet to be sent.
                self.window[self.window_size - 1].send(server_socket, address)
            # increase the right end and left end by one, accounting for
            # relationship between a sequence number and sliding window
            # (max(sequence_number) = 2 * window_size + 1)
            self.left_end = (self.left_end + 1) % ((2*self.window_size)+1)
            self.right_end = (self.right_end + 1) % ((2*self.window_size)+1)

        # if(self.is_sending and len(self.window) >= self.window_size):
        #    self.window[self.window_size - 1].send(server_socket, address)

    def receive(self, data_packet, server_socket, address):
        '''
        Given a received TTDataPacket object, add it to the window and send an
        ACK for it on the given server_socket to the given address. ACKs are
        sent for any packet, regardless if it is currently in the window or not.
        Restrictions on window sizes (sending=receiving) and sliding ensure that
        any packet that is not currently in the window has already been received
        and dealt with. This method triggers completion of the receiving process
        once the correct number of packets have been received, calling the
        completion_fn with the assembled message.

        :param data_packet: a packet of any sequence number that is part of the
            message to be received

        :type data_packet: TTDataPacket

        :param server_socket: the parent TTNetwork object's open server socket

        :type server_socket: Socket

        :param address: the address to which an ACK will be sent for the given
            packet

        :type address: Address
        '''
        self.mutex.acquire()
        ack = TTAckPacket(data_packet.message_id, data_packet.sequence_num)
        ack.send(server_socket, address)

        if self.is_in_window(data_packet.sequence_num):
            index = self.get_packet_index(data_packet.sequence_num)
            if self.window[index] is None:
                self.num_received += 1
            self.window[index] = data_packet
            self.slide(server_socket, address)

            if self.num_received == self.packet_count:
                received_message = TTNetworkMessageUDP(
                    address[0], address[1], self.received_object_bytes)
                received_message.message_id = data_packet.message_id
                self.completion_fn(received_message)

        self.mutex.release()

    def ack(self, ack_packet, server_socket, address):
        '''
        The sender's version of receiver(); this method takes a TTAckPacket
        object and marks the corresponding packet in the window as ACK-ed.

        :param ack_packet: an ACK for a given TTDataPacket

        :type ack_packet: TTAckPacket

        :param server_socket: the parent TTNetwork object's open server socket

        :type server_socket: Socket

        :param address: the address from which the ACK was received

        :type address: Address
        '''
        self.mutex.acquire()
        if self.is_in_window(ack_packet.sequence_num):
            index = self.get_packet_index(ack_packet.sequence_num)
            self.window[index].ACKed = True
        self.slide(server_socket, address)
        if len(self.window) == 0:
            self.active = False
            self.completion_fn(self.initializer)
        self.mutex.release()

    def start(self, server_socket, address, timeout):
        '''
        This method is called to active a sending window. It sends the first n
        packets specified by the window_size, and starts an independent thread
        to handle timeouts.

        :param server_socket: the parent TTNetwork's open server socket

        :type server_socket: Socket

        :param address: the address that the window will send to

        :type Address: Address

        :param timeout: the number of milliseconds to wait before resending
            packets.

        :type timeout: int
        '''
        self.mutex.acquire()
        self.active = True
        for i in range(0, min(self.window_size, len(self.window))):
            self.window[i].send(server_socket, address)
        start_thread = threading.Thread(
            target=self._start, args=(server_socket, address, timeout))
        start_thread.start()
        self.mutex.release()

    def _start(self, server_socket, address, timeout):
        '''
        Ensures that the _handle_timeouts method runs independently of the
        calling thread.

        :param server_socket: the parent TTNetwork's open server socket

        :type server_socket: Socket

        :param address: the address to which this message will be sent

        :type address: Address

        :param timeout: the number of milliseconds to wait before resending
        packets.

        :type timeout: int
        '''
        timeouts_thread = threading.Thread(
            target=self._handle_timeouts, args=(server_socket, address, timeout))
        timeouts_thread.start()
        timeouts_thread.join()

    def print_window(self):
        '''
        For debugging purposes only; prints the range of sequence numbers that
        are being sent/received at a given time. For accuracy and to avoid
        errors, this must be called AFTER securing the mutex. However, code to
        secure the mutex has not been added here because this method is most
        useful when called within the context of a method like ack() or
        receive(); both of which secure the mutex.
        '''
        output = "[" + str(self.left_end)

        if self.right_end >= self.left_end:
            for i in range(self.left_end + 1, self.right_end + 1):
                output += ", " + str(i)
        else:
            for i in range(self.left_end + 1, ((2 * self.window_size) + 1)):
                output += ", " + str(i)
            for i in range(0, self.right_end):
                output += ", " + str(i)
        output += "]"
        if __debug__:
            logger.debug('Window for ID=%s: %s' %
                         (self.initializer.message_id, output))

    def get_packet_index(self, sequence_number):
        '''
        Given a sequence number, obtain the index in the current window where
        the packet with that sequence number should be/is stored. This assumes
        that the given sequence_number has been confirmed to be in the window
        with a call to is_in_window()

        :param sequence_number: the sequence number of a given packet

        :type sequence_number: int

        :return: the index of the packet in the window corresponding to the
            given sequence number

        :rtype: int
        '''
        if self.right_end >= self.left_end:
            return (sequence_number - self.left_end) % self.window_size
        else:
            right_slots = (self.right_end + 1)
            left_slots = (self.window_size - right_slots)
            if sequence_number <= self.right_end:
                return sequence_number + left_slots
            else:
                return sequence_number - self.left_end

    def _handle_timeouts(self, server_socket, address, timeout):
        '''
        While the window is sending, this iterates from the start of the window
        and resends every packet that has waited for the specified number of
        milliseconds after being sent. Upon reaching a packet that has not timed
        out, iteration can terminate.

        :param server_socket: the parent TTNetwork object's open server socket

        :type server_socket: Socket

        :param address: the address to which this message is being sent

        :type address: Address
        '''
        while self.active:
            self.mutex.acquire()
            max_range = min(self.window_size, len(self.window))
            for i in range(0, max_range):
                current_time = datetime.now().microsecond
                if self.window[i].time_sent is not None and self.window[i].time_sent - current_time >= timeout:
                    self.window[i].send(server_socket, address)
                else:
                    i = max_range
            self.mutex.release()
            time.sleep(timeout / 1000)

    def is_in_window(self, sequence_number):
        '''
        Indicates whether a given sequence number is valid for the current state
        of the sliding window; e.g. whether a packet with that number is being
        sent or can be received.

        :param sequence_number: the sequence number to be checked

        :type sequence_number: int

        :rtype: bool
        '''
        if self.right_end >= self.left_end:
            return sequence_number >= self.left_end and sequence_number <= self.right_end
        else:
            return sequence_number >= self.left_end or sequence_number <= self.right_end


class WindowException(Exception):
    pass

class TTNetworkInterfaceUDP(TTNetworkInterface):
    '''
    In simulation, this represents the network that interconnects all ensembles
    in the system, providing a medium to exchange ``TTMessage`` objects. In
    practice, every ensemble will have an active instance of TTNetwork for use
    in sending and receiving messages. The TTNetwork constructor is
    non-blocking, as well as its send and receive operations. It represents a
    single port for the given ensemble for use in both sending and receiving
    messages.

    :param ensembles: The set of TTEnsemble objects involved in a TTPython
        simulation.

    :type ensembles: TTEnsemble

    :param ip: The IP public IP address of the current ensemble. Localhost can
        be used for simulation/testing, but the public IP must be set in order
        to receive external messages.

    :type ip: str

    :param port: The port for the network to send and receive on

    :type port: int

    :param timeout_ms: the number of milliseconds to wait before resending
        packets

    :type timeout_ms: int

    :param receiver_function: the function to be called once a TTNetworkMessage
        has been received.

    :type receiver_function: lambda TTNetworkMessage
    '''
    # TODO: use a different port for sending messages than receiving. This will
    # require splitting listen() into two separate implementations. a current
    # workaround to less-than-ideal conditions caused by using a single port is
    # to create RX and TX TTNetwork objects.

    def __init__(
            self,
            ensembles=None,
            ip_addr="127.0.0.1",
            port=RX_PORT,
            timeout_ms=TIMEOUT,
            receiver_function=(lambda msg: logger.debug(msg))):
        super().__init__(receiver_function=receiver_function)

        self.ip_addr = ip_addr
        self.port = port
        self.timeout_ms = timeout_ms
        self.stop_threads = False
        self.receiver_function = receiver_function
        self.server_socket = None
        self.receiving_thread = None
        self.num_handshaking = 0
        self.ensembles = ensembles

        # a map between message IDs and termination flags for threads waiting
        # for an initial handshake to occur for that message ID
        self.handshaking_threads = {}

        # a map from message IDs and recipient addresses to TTSlidingWindows
        self.sending_windows = {}

        # a map from message IDs and receipient addresses to TTSlidingWindows
        self.receiving_windows = {}

        if re.search(IPV4_REGEX, ip_addr) is None:
            raise Exception(ip_addr + "is not a valid IPv4 address.")
        if port not in range(MIN_PORT, MAX_PORT):
            raise Exception(str(port) + " is not a valid port.")

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.server_socket.bind((self.ip_addr, self.port))
        self.server_socket.setblocking(False)

        self.handshake_lock = Lock()
        self.handshake_thread = threading.Thread(target=self.handshake)
        self.handshake_thread.start()
        self.receiving_thread = threading.Thread(target=self.listen)
        self.receiving_thread.start()

    def __del__(self):
        '''
        Once this object is garbage collected/deleted, both the socket and
        receiving thread must be terminated.
        '''
        self.cleanup()

    def cleanup(self):
        # TODO: ensure that any active handshaking threads are terminated here
        self.stop_threads = True
        if self.handshake_thread:
            self.handshake_thread.join()
        if self.receiving_thread:
            self.receiving_thread.join()
        if self.server_socket:
            self.server_socket.close()

    def ensure_handshake(self, req_message, address):
        '''
        Spawns a thread to ensure that a REQ to send a given TTNetworkMessage is
        ACKed by the recipient. The thread will wait on a boolean contained in
        the handshaking_threads dictionary, uniquely identified by the message's
        ID and address.

        :param req_message: the TTReqPacket for the given message that is to be
            resent if not ACKed for

        :type req_message: TTReqPacket

        :param address: the address to which the request is being sent

        :type address: Address

        '''
        if address not in self.handshaking_threads:
            self.handshaking_threads[address] = {}
        if req_message.message_id in self.handshaking_threads[address]:
            raise Exception("A handshake for this message ID is ongoing.")
        else:
            self.handshaking_threads[address][req_message.message_id] = {
                "req_packet": req_message,
                "address": address
            }
        self.num_handshaking += 1

    def get_num_waiting(self):
        return self.num_handshaking

    def handshake(self):
        '''
        Wait for the packet timeout, and then resend the REQ for all messages
        that have not yet been handshaken for.
        '''
        while not self.stop_threads:
            self.handshake_lock.acquire()
            current_time = datetime.now().microsecond
            for address in list(self.handshaking_threads):
                for message_id in list(self.handshaking_threads[address]):
                    req_collection = self.handshaking_threads[address][message_id]
                    req_packet = req_collection["req_packet"]
                    req_address = req_collection["address"]
                    if current_time - req_packet.time_sent >= self.timeout_ms:
                        req_packet.send(self.server_socket, req_address)
            self.handshake_lock.release()
            time.sleep(self.timeout_ms / 1000)

    def terminate_handshake(self, message_id, address):
        '''
        Upon completion of a handshake for a given message ID and address,
        terminate the thread.

        :param message_id: The message ID that is being handshaken for

        :type message_id: int

        :param address: the Address to which the message will be sent

        :type address Address
        '''
        self.handshake_lock.acquire()
        if address in self.handshaking_threads:
            if message_id in self.handshaking_threads[address]:
                del self.handshaking_threads[address][message_id]
        self.handshake_lock.release()
        self.num_handshaking -= 1

    def wrap_completion(self, msg, message_dict, message_id, callback):
        '''
        When a message has been sent or received correctly, before calling the
        corresponding signalling function (after or receiver_function), ensure
        that the entry for that message's sending/receiving window is deleted to
        avoid collision of message IDs.

        :param msg: The TTNetworkMessage object passed to the callback function
            on completion of sending/receiving.

        :type msg: TTNetworkMessage

        :param message_dict: the dictionary containing the given message's ID

        :type message_dict: dict

        :param message_id: the ID of the message that has completed
            sending/receiving

        :type message_id: int

        :param callback: the callback function to be executed

        :type callback: lambda
        '''
        logger.debug(f'Wrap completion for message id: {message_id}')
        del message_dict[message_id]
        callback(msg)

    def get_receiving_window(self, address, message_id):
        '''
        Get the TTSlidingWindow object for a message that is currently being
        received. Will return 'None' if no such message exists.

        :param address: The address that the message is being received from

        :type address: Address

        :param message_id: the ID of the message being received

        :type message_id: int
        '''
        if address not in self.receiving_windows:
            return None
        if message_id not in self.receiving_windows[address]:
            return None
        return self.receiving_windows[address][message_id]

    def create_receiving_window(self, address, request_packet):
        '''
        Create a TTSlidingWindow for a REQ. Will throw an error if a message ID
        is duplicated (which should be guaranteed not to occur based on the
        lifetime of the sender's TTNetworkMessage object).

        :param address: The address from which the message will be received

        :type address: Address

        :param request_packet: The TTReqPacket object received.

        :type request_packet: TTReqPacket
        '''
        if address not in self.receiving_windows:
            self.receiving_windows[address] = {}
        if request_packet.message_id not in self.receiving_windows[address]:
            def completion_wrap(msg):
                return self.wrap_completion(
                msg, self.receiving_windows[address], request_packet.message_id, self.receiver_function)
            self.receiving_windows[address][request_packet.message_id] = TTSlidingWindow(
                completion_fn=completion_wrap, initializer=request_packet)
        else:
            raise WindowException(
                "Duplicate message detected when creating receiving window! "
                f"ID={request_packet.message_id}")

    def get_sending_window(self, dest_address, message_id):
        '''
        Get the TTSlidingWindow object for a message that is currently being
        sent. Will return 'None' if no such message exists.

        :param dest_address: The address that the requested window is sending
            to.

        :type dest_address: Address

        :param message_id: The ID of the message that is currently being sent

        :type message_id: int
        '''
        if dest_address not in self.sending_windows:
            return None
        if message_id not in self.sending_windows[dest_address]:
            return None
        return self.sending_windows[dest_address][message_id]

    def create_sending_window(self, message, after):
        '''
        Used to create a TTSlidingWindow after receiving an ACK_START from the
        recipient. Will raise an Exception if a duplicate message ID is being
        sent, which will occur if the same TTNetworkMessage is being sent
        simultaneously to a single address.

        :param message: the TTNetworkMessage object to be sent.

        :type message: TTNetworkMessage

        :param after: the function to be called once the message has been sent
            successfully in its entirety.

        :type after: lambda
        '''
        address = message.get_address()
        if address not in self.sending_windows:
            self.sending_windows[address] = {}
        if message.message_id not in self.sending_windows[address]:
            def after_wrap(msg):
                return self.wrap_completion(
                msg, self.sending_windows[address], message.message_id, after)
            self.sending_windows[address][message.message_id] = TTSlidingWindow(
                initializer=message, completion_fn=after_wrap)
        else:
            raise WindowException(
                "Duplicate message detected when creating sending window! "
                f"ID={message.message_id}")

    def listen(self):
        '''
        Receive packets at the specified port and public IP. Redirect them to
        the corresponding sending and receiving windows, creating new ones when
        necessary. This passes logic directly to the TTSlidingWindow objects
        concerned with each message.

        On the receivers side, it is possible that message will come in that
        doesn't match an active receiving window, and isn't a REQ to create a
        new receiving window. These cases will occur quite often due to the
        nature of UDP; packets can be duplicated, and ACKs can be lost easily,
        so it's possible for a receiver to finish receiving a message in full
        before the sender is aware. The sender might then have a timeout trigger
        for a packet before its ACK arrives, leading to duplicates, or a
        duplicate will simply occur without cause. These errors can be safely
        ignored, as seen below in the try-except blocks for WindowException.
        '''
        # super().__init__()
        if __debug__:
            logger.debug('Listening for messages sent to %s:%s' %
                         (self.ip_addr, self.port))

        sel = selectors.DefaultSelector()
        sel.register(self.server_socket, selectors.EVENT_READ)

        while not self.stop_threads:
            try:
                events = sel.select(timeout=TIMEOUT/1000)

                # empty list indicates nothing to read
                if events:
                    (payload, address) = self.server_socket.recvfrom(MIN_TO_RECV)
                    packet = convert_to_packet(payload)
                    if packet is not None:
                        if isinstance(packet, TTRequestPacket):
                            if __debug__:
                                logger.debug('REQ from %s for ID=%s' % (
                                    str(address[0]) + ":" + str(address[1]), packet.message_id))
                            try:
                                self.create_receiving_window(address, packet)
                                ack = TTAckPacket(packet.message_id, 0)
                                ack.send(self.server_socket, address)
                            except WindowException as winex:
                                logger.warning('Window ex!! %s', winex)
                                pass
                            finally:
                                pass
                        elif isinstance(packet, TTAckPacket):
                            sending_window = self.get_sending_window(
                                address, packet.message_id)
                            if sending_window is not None:
                                if sending_window.active:
                                    if __debug__:
                                        logger.debug('ACK from %s for ID=%s' % (
                                            str(address[0]) + ":" + str(address[1]), packet.message_id))
                                    sending_window.ack(
                                        packet, self.server_socket, address)
                                else:
                                    if __debug__:
                                        logger.debug('ACK_START from %s for ID=%s' % (
                                            str(address[0]) + ":" + str(address[1]), packet.message_id))
                                    self.terminate_handshake(
                                        packet.message_id, address)
                                    sending_window.start(
                                        self.server_socket, address, self.timeout_ms)
                            else:
                                if __debug__:
                                    logger.debug(
                                        'ERROR: No TX window exists for message ID=%s' % (packet.message_id,))

                        else:
                            assert isinstance(
                                packet, TTDataPacket), 'Received a non-data packet?'
                            # is the 'else' condition a DataPacket?
                            if __debug__:
                                logger.debug('PACKET from %s; #=%s, ID=%s, SIZE=%s' % (str(
                                    address[0]) + ":" + str(address[1]), packet.sequence_num, packet.message_id, len(packet.payload)))
                            receiving_window = self.get_receiving_window(
                                address, packet.message_id)
                            logger.debug('RX win: %s' % receiving_window)
                            if receiving_window:
                                receiving_window.receive(
                                    packet, self.server_socket, address)
                            else:
                                if __debug__:
                                    logger.debug(
                                        'ERROR: No RX window exists for message ID=%s' % (packet.message_id,))
            except ConnectionResetError:
                pass

    def send(
            self,
            message,
            after=(lambda msg: logger.debug("Sent successfully! ID=%s" % (msg.message_id,))),
            ensure=False):
        '''
        Create a sending window for a message, send a REQ for it to the sender,
        and then start a thread to ensure that the handshake completes
        successfully in case of packet loss. Returns immediately without
        blocking.
        '''
        super().__init__(message)

        self.create_sending_window(message, after)
        request_packet = message.get_request()
        request_packet.send(self.server_socket, message.get_address())
        if ensure:
            self.ensure_handshake(request_packet, message.get_address())
