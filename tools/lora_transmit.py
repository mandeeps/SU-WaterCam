#!/usr/bin/env python

import struct
import ast
import serial

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


def transmit_serial(content):
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
# without conversion
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

# formatting
#sensor_packet = {
#    'timestamp' : val,
#    'emergency_status' : val,
#    'health_status' : val,
#    'movement_threshold' : val,
#    'battery_percent' : val,
#    'tilt_roll_yaw' : val,
#    'lat_lon_z' : val,
#    'temperature_celsius' : val,
#    'relative_humidity' : val,
#    'camera_flood_detected' : val,
#    'camera_flood_growing' : val,
#    'flood_bitmap_compressed' : val,
#    'status_area_threshold' : val,
#    'stage_threshold' : val,
#    'monitoring_frequency' : val,
#    'emergency_frequency' : val,
#    'neighborhood_emergency_frequency' : val
#}

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

encoded = compressed_encoding({
    "timestamp": 1748892908,
    "emergency_status": 1,
    "health_status": 1,
    "battery_percent": 5,
    "temperature_celsius": 23.5,
    "tilt_roll_yaw": [0.1, 0.2, 0.3],
    "lat_lon_z": [40.7128, -74.0060, 12.5],
    "relative_humidity": 55
})
#print(encoded.hex())

# example sending AT commands directly, so the mDot does not need to be placed into 
# serial data mode. This will allow us to use it as a Class C device

# lora_transmit.transmit("AT+SEND=test\r\n".encode())

def transmit_from_watercam(data_dict):
    print("handle data from watercam sensors")
    print(data_dict)

    packet = compressed_encoding(data_dict)
    data = packet.hex()
    transmit(f"AT+SENDB={data}\r\n".encode())


if __name__ == "__main__":
    #    import sys
#    file = sys.argv[1]
#    bits = load_file(file)

#    bits = pack_sensor_data(bits)
#    transmit(bits)
    transmit(encoded)
