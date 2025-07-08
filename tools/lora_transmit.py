#!/usr/bin/env python
import struct
import serial

# Data Format
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
    port = '/dev/ttyAMA5',  # Replace with correct UART device
    # should be AMA5 if using alternate UART on Pi 4 as in README 
    baudrate = 115200,
    parity = serial.PARITY_NONE,
    stopbits = serial.STOPBITS_ONE,
    bytesize = serial.EIGHTBITS,
    timeout = 1
)

if not ser.is_open:
    print("Serial port is not open. Check the connection.")

def transmit(content):
    # Send the formatted data over UART/Serial
    try:
        # Ensure the serial connection is ready to send
        ser.flush()
        # Write the data to the serial port
        ser.write(content)
        print("Data sent to mDot successfully!")
    except Exception as e:
        print(f"Error sending to mDot: {e}")

    finally:
        if 'ser' in locals():
            ser.close()

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

# example sending AT commands directly, so the mDot does not need to be placed into 
# serial data mode. This will allow us to use it as a Class C device
# lora_transmit.transmit("AT+SEND=test\r\n".encode())

def transmit_from_watercam(data_dict):
    # example for direct use
    print("handle data from watercam sensors")
    print(data_dict)

    packet = compressed_encoding(data_dict)
    data = packet.hex()
    transmit(f"AT+SENDB={data}\r\n".encode())

if __name__ == "__main__":
    from sys import argv
    if len(argv) == 1:
        transmit(example_packet)
    else:
        with open(argv[1], 'rb') as file:
            contents = file.read().hex()
            transmit(f"AT+SENDB={contents}\r\n".encode())
