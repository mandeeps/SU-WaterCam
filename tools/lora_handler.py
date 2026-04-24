#!/usr/bin/env python
import atexit
from time import sleep
from sys import getsizeof
import struct
import serial

# Transmission Data Format
# Channel 00 Type 01 Time stamp UNIX
# Channel 01 Type 04 emergency status 0/1 where 0 is monitoring & 1 is emergency
# 01 05 health 0/1 where 0 is normal operation
# 01 06 coordinate move threshold 0/1 where 0 is insignificant movement
# 02 01 battery percent
# 03 01 tilt/roll/yaw
# 04 01 lat/lon/z
# 05 01 temp four digit float as ints, celsius
# 06 01 rel humidity percent
# 07 17 camera flood detect status 0/1
# 07 27 new local max (flood growing) 0/1
# 08 18 flood bitmap compressed binary
# 09 19 status area threshold %
# 09 29 stage threshold %
# 09 39 monitoring freq
# 09 49 emergency freq
# 09 59 neighborhood emergency status freq

# Reception Data Format
# Channel 10 Type 90 Area Threshold (at) - 10% increments
# 11 91 Stage Threshold (st) - continuous cm value 
# 12 92 Monitoring Frequency (mf) - how long to stay awake for - minute value
# Emergency status: !, hex 21 - system enters emergency mode and stops scheduled shutdowns

def open_port() -> serial.Serial:
    """Open the mDot serial port and register a close handler on exit."""
    port = serial.Serial(
        port='/dev/ttyAMA5',
        baudrate=115200,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        bytesize=serial.EIGHTBITS,
        timeout=1
    )
    if not port.is_open:
        raise RuntimeError("Serial port /dev/ttyAMA5 failed to open")
    atexit.register(port.close)
    return port

_ser: serial.Serial = None

def _get_ser() -> serial.Serial:
    """Return the open serial port, opening it on first call."""
    global _ser
    if _ser is None:
        _ser = open_port()
    return _ser

def transmit(content):
    ser = _get_ser()
    # Send the formatted data over UART/Serial
    try:
        # Ensure the serial connection is ready to send
        ser.flush()
        ser.write('AT+TXS\r\n'.encode()) # check byte limit of next transmission
        res = ser.read_until() # get 'AT+TXS' echo back from mDot
        res = ser.read_until().decode() # this should be the actual response
        print(f'Size limit of next transmission payload: {res}')

        if getsizeof(content) <= int(res):
            # Write the data to the serial port
            ser.write(content)
            print("Data sent to mDot successfully!")
            line = ser.read_until() # newline response from mDot
            #print(line)
            line = ser.read_until().decode() # should be 'OK' response from mDot
            print(f'Response from mDot: {line}')
        else:
            print('Contents larger than current lora transmission payload')

    except Exception as e:
        print(f"Error sending to mDot: {e}")

# Handle encoding and compression of data from sensors for transmission over LoRa
def compressed_encoding(data):
    packet = bytearray()
    def add_u8(ch, t, v): packet.extend([ch, t, v])
    def add_u16(ch, t, v): packet.extend([ch, t]); packet.extend(struct.pack(">H", v))
    def add_u32(ch, t, v): packet.extend([ch, t]); packet.extend(struct.pack(">I", v))
    def add_f32(ch, t, v): packet.extend([ch, t]); packet.extend(struct.pack(">f", v))
    def add_f32_3(ch, t, v): packet.extend([ch, t]); [packet.extend(struct.pack(">f", x)) for x in v]
    def add_blob(ch, t, b): packet.extend([ch, t]); packet.extend(struct.pack(">H", len(b))); packet.extend(b)

    if 'timestamp' in data: add_u32(0x00, 0x01, data['timestamp'])
    if 'emergency_status' in data: add_u8(0x01, 0x04, data['emergency_status'])
    if 'health_status' in data: add_u8(0x01, 0x05, data['health_status'])
    if 'movement_threshold' in data: add_u8(0x01, 0x06, data['movement_threshold'])
    if 'battery_percent' in data: add_u8(0x02, 0x01, data['battery_percent'])
    if 'tilt_roll_yaw' in data: add_f32_3(0x03, 0x01, data['tilt_roll_yaw'])
    if 'lat_lon_z' in data: add_f32_3(0x04, 0x01, data['lat_lon_z'])
    if 'temperature_celsius' in data: add_f32(0x05, 0x01, data['temperature_celsius'])
    if 'relative_humidity' in data: add_u8(0x06, 0x01, data['relative_humidity'])
    if 'camera_flood_detected' in data: add_u8(0x07, 0x17, data['camera_flood_detected'])
    if 'camera_flood_growing' in data: add_u8(0x07, 0x27, data['camera_flood_growing'])
    if 'flood_bitmap_compressed' in data: add_blob(0x08, 0x18, data['flood_bitmap_compressed'])
    if 'status_area_threshold' in data: add_u8(0x09, 0x19, data['status_area_threshold'])
    if 'stage_threshold' in data: add_u8(0x09, 0x29, data['stage_threshold'])
    if 'monitoring_frequency' in data: add_u16(0x09, 0x39, data['monitoring_frequency'])
    if 'emergency_frequency' in data: add_u16(0x09, 0x49, data['emergency_frequency'])
    if 'neighborhood_emergency_frequency' in data: add_u16(0x09, 0x59, data['neighborhood_emergency_frequency'])

    return bytes(packet)

# basic packet for testing transmission
example_packet = compressed_encoding({
    "timestamp": 1748892908,
    "emergency_status": 1,
    "health_status": 1,
    "battery_percent": 5,
    "temperature_celsius": 23.5,
    "tilt_roll_yaw": [0.1, 0.2, 0.3],
    "lat_lon_z": [40.7128, -74.0060, 12.5],
    "relative_humidity": 55
})

# Example sending AT commands directly, so the mDot does not need to be placed into 
# serial data mode. This will allow us to use it as a Class C device
# lora_transmit.transmit("AT+SEND=test\r\n".encode())


# Inbound Packet Handling
# Get LoRa messages, interpret, and return commands or values for other functions
# Write some values to text for persistance

# Command Handler Functions
def area_threshold(val):
    print(f'Area threshold: {val}')

def stage_threshold(val):
    print(f'Stage Threshold {val}')

def monitoring_freq(val):
    print(f'Monitoring Frequency: {val}')

# dictionary of commands and associated functions
commands = {'at':area_threshold, 'st':stage_threshold, 'mf':monitoring_freq}

def decode(payload):
    command = payload[:2]
    param = payload[2:]
    commands[command](param)

# Loop that listens for incoming commands. Should be listening when not transmitting
def listen(listening = True):
    ser = _get_ser()
    print('Listening for incoming packets')
    while(listening):
        if (ser.in_waiting > 0):
            res = ser.readline().decode()
            print(res)
            data = res.split("DATA=",1)[1]
            payload = bytes.fromhex(data).decode()
            print(payload)
            decode(payload)

if __name__ == "__main__":
    from sys import argv
 
    # send a packet when run without an argument
    if len(argv) == 1:
        transmit(f"AT+SENDB={example_packet.hex()}\r\n".encode())
    elif argv[1] == 'listen':
        listen()
    # else transmit the file param
    else:
        with open(argv[1], 'rb') as file:
            contents = file.read().hex()
            transmit(f"AT+SENDB={contents}\r\n".encode())
