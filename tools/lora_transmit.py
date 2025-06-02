#!/usr/bin/env python

import struct
import serial
import ast
import json

# Format
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


# Configure the serial port
ser = serial.Serial(
    port = '/dev/ttyAMA1',  # Replace with correct UART device
    baudrate = 115200,
    parity = serial.PARITY_NONE,
    stopbits = serial.STOPBITS_ONE,
    bytesize = serial.EIGHTBITS,
    timeout = 1
)

if not ser.is_open:
    print("Serial port is not open. Check the connection.")


def load_file(filename):
    with open(filename, 'r') as f:
        data = f.read()

    data = ast.literal_eval(data)
    return data

def format_dictionary(data):
    if not data:
        print("No data received")
        return

    # Format each key-value pair into bytes (efficient format)
    formatted_data = b""
    for k, v in data.items():
        if isinstance(v, int):
            # For integers, pack into 4-byte unsigned integer
            formatted_data += struct.pack(">I", v)
        elif isinstance(v, str):
            # For strings, encode as UTF-8 and add length prefix
            encoded_str = v.encode("utf-8")
            len_byte = struct.pack(">B", len(encoded_str))
            formatted_data += len_byte + encoded_str
        else:
            print(f"Unsupported value type: {type(v)} for key '{k}'")


def transmit(content):
    # Send the formatted data over UART/Serial
    try:
        # Ensure the serial connection is ready to send
        ser.flush()
        # Write the data to the serial port
        ser.write(content)
        print("Data transmitted successfully!")
    except Exception as e:
        print(f"Transmission error: {e}")

    finally:
        if 'ser' in locals():
            ser.close()

# formatting and default payload
sensor_data = {
    (0x00, 0x01): 0,         # UNIX timestamp
    (0x01, 0x04): 0,                  # Emergency status (0 or 1)
    (0x01, 0x05): 0,                  # Health status
    (0x01, 0x06): 0,                  # Movement threshold crossed
    (0x02, 0x01): 0,                 # Battery percent
    (0x03, 0x01): (0.0, 0.0, 0.0),    # Tilt/roll/yaw
    (0x04, 0x01): (0.0, 0.0, 0.0),  # Lat/lon/z
    (0x05, 0x01): 0,              # Temp in Celsius
    (0x06, 0x01): 0,                 # Relative humidity
    (0x07, 0x17): 0,                  # Camera flood detected
    (0x07, 0x27): 0,                  # New local max
    (0x08, 0x18): b'\x00\x01\xFE\xFF', # Compressed bitmap (placeholder binary)
    (0x09, 0x19): 0,                 # Status area threshold %
    (0x09, 0x29): 0,                 # Stage threshold %
    (0x09, 0x39): 0,                 # Monitoring freq
    (0x09, 0x49): 0,                 # Emergency freq
    (0x09, 0x59): 0                 # Neighborhood emergency status freq
}

def pack_sensor_data(data_dict):
    packed = b""

    for (channel, type_), value in data_dict.items():
        packed += struct.pack("BB", channel, type_)

        if isinstance(value, int):
            packed += struct.pack(">I", value)  # 4-byte big-endian unsigned int
        elif isinstance(value, float):
            packed += struct.pack(">f", value)  # 4-byte float
        elif isinstance(value, tuple) and all(isinstance(x, float) for x in value):
            packed += struct.pack(">" + "f" * len(value), *value)  # Multiple floats
        elif isinstance(value, bytes):
            length = len(value)
            packed += struct.pack(">H", length) + value  # 2-byte length prefix + binary
        else:
            raise ValueError(f"Unsupported data type for ({channel}, {type_}): {value}")
    return packed


def transmit_from_watercam(data):
    print("handle data from watercam sensors")
    print(data)

if __name__ == "__main__":
    import sys
    file = sys.argv[1]
    bits = load_file(file)

    bits = pack_sensor_data(bits)
    transmit(bits)
