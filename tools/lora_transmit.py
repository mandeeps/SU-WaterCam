#!/usr/bin/env python

import struct
import sys
import serial

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
    with open(filename, 'rb') as f:
        image = f.read()
    return image

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

if __name__ == "__main__":
    file = sys.argv[1]
    bits = load_file(file)
    transmit(bits)
