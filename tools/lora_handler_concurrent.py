#!/usr/bin/env python
import time
from sys import getsizeof
import struct
import serial
import threading
import queue
import json
import os
from typing import Dict, Any, Optional

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
# First two digits represent the command, remaining digits are the values to set
# 10 Area Threshold - two digits representing 10% increments, example: we receive 1010 and decode as 'set aread threshold to 100%' 
# 11 Stage Threshold - four digits continuous cm value
# 12 Monitoring Frequency - how long to stay awake for - minute value
# 21 Emergency status: system enters emergency mode and stops scheduled shutdowns

class LoRaHandler:
    def __init__(self, port='/dev/ttyAMA5', config_file='lora_config.json'):
        self.port = port
        self.config_file = config_file
        self.config = self.load_config()
        
        # Threading components
        self.transmit_queue = queue.Queue()
        self.listening = False
        self.listener_thread = None
        self.transmit_lock = threading.Lock()
        
        # Configure the serial port
        self.ser = serial.Serial(
            port=port,
            baudrate=115200,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            bytesize=serial.EIGHTBITS,
            timeout=1
        )
        
        if not self.ser.is_open:
            print("Serial port is not open. Check the connection.")
            raise RuntimeError("Serial port failed to open")
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        default_config = {
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'emergency_frequency': 5,
            'neighborhood_emergency_frequency': 30
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}, using defaults")
                return default_config
        else:
            # Save default config
            self.save_config(default_config)
            return default_config
    
    def save_config(self, config: Dict[str, Any]):
        """Save configuration to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def update_config(self, key: str, value: Any):
        """Update a configuration value and save to file"""
        self.config[key] = value
        self.save_config(self.config)
        print(f"Updated {key} to {value}")
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a configuration value"""
        return self.config.get(key, default)
    
    def start_listening(self):
        """Start the listening thread"""
        if not self.listening:
            self.listening = True
            self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.listener_thread.start()
            print("LoRa listener started")
    
    def stop_listening(self):
        """Stop the listening thread"""
        self.listening = False
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2)
            print("LoRa listener stopped")
    
    def _listen_loop(self):
        """Main listening loop - runs in separate thread"""
        print('Listening for incoming packets')
        while self.listening:
            try:
                if self.ser.in_waiting > 0:
                    try:
                        res = self.ser.readline().decode().strip()
                        if res and "DATA=" in res:
                            try:
                                data = res.split("DATA=", 1)[1]
                                print(f"Received: {payload}")
                                self.decode(payload)
                            except Exception as e:
                                print(f"Error processing received data: {e}")
                    except UnicodeDecodeError:
                        # Handle binary data that can't be decoded as UTF-8
                        res_raw = self.ser.readline()
                        print(f"Received binary data (hex): {res_raw.hex()}")
                else:
                    time.sleep(0.5)  # Small delay to prevent busy waiting
            except Exception as e:
                print(f"Error in listen loop: {e}")
                time.sleep(1)  # Longer delay on error
    
    def transmit(self, content: bytes) -> bool:
        """Transmit data with thread safety"""
        with self.transmit_lock:
            try:
                print(f"DEBUG: Starting transmission of {len(content)} bytes")
                print(f"DEBUG: Content to send: {content}")
                
                # Ensure the serial connection is ready to send
                self.ser.flush()
                self.ser.reset_input_buffer()
                
                # Send newline to clear any partial commands
                print("DEBUG: Clearing AT interface with newline...")
                self.ser.write('\r\n'.encode())
                time.sleep(0.5)  # Brief pause to let mDot process
                
                # Send AT+TXS command to check size
                print("DEBUG: Sending AT+TXS command...")
                self.ser.write('AT+TXS\r\n'.encode())
                
                # Wait a bit for mDot to process
                time.sleep(1)
                
                # Read all available responses
                responses = []
                while self.ser.in_waiting > 0:
                    res = self.ser.read_until()
                    if res:
                        try:
                            response = res.decode('utf-8').strip()
                            responses.append(response)
                            print(f"DEBUG: Response: '{response}'")
                        except UnicodeDecodeError:
                            response_hex = res.hex()
                            responses.append(f"BINARY:{response_hex}")
                            print(f"DEBUG: Binary response (hex): {response_hex}")
                
                # Look for the size limit in responses
                size_limit = 242  # Default size based on mDot response (LoRaWAN payload size limit)
                for response in responses:
                    if response.isdigit():
                        size_limit = int(response)
                        print(f"DEBUG: Found size limit: {size_limit} bytes")
                        break
                
                print(f"DEBUG: Using size limit: {size_limit} bytes")
                
                # Calculate actual payload size and construct AT command
                if isinstance(content, str):
                    # For hex strings, each pair of characters = 1 byte
                    payload_size = len(content) // 2
                    print(f"DEBUG: Hex string length: {len(content)} chars, payload size: {payload_size} bytes")
                    
                    if payload_size <= size_limit:
                        # Construct AT command for hex string
                        at_command = f"AT+SENDB={content}\r\n".encode()
                        print(f"DEBUG: Sending AT command: {at_command}")
                        self.ser.write(at_command)
                        print("DEBUG: AT command sent, waiting for response...")
                        
                        # Wait for response
                        time.sleep(1)
                        
                        # Read response lines and verify success
                        final_responses = []
                        success = False
                        while self.ser.in_waiting > 0:
                            res = self.ser.read_until()
                            if res:
                                try:
                                    response = res.decode('utf-8').strip()
                                    final_responses.append(response)
                                    print(f"DEBUG: Final response: '{response}'")
                                    
                                    # Check for OK response
                                    if 'OK' in response:
                                        success = True
                                        print("DEBUG: mDot confirmed command execution with OK")
                                except UnicodeDecodeError:
                                    # Handle binary responses that can't be decoded as UTF-8
                                    response_hex = res.hex()
                                    final_responses.append(f"BINARY:{response_hex}")
                                    print(f"DEBUG: Binary response (hex): {response_hex}")
                                    
                                    # Check if this might be an OK response in binary form
                                    if b'OK' in res:
                                        success = True
                                        print("DEBUG: mDot confirmed command execution with OK (binary)")
                        
                        if success:
                            print("Data sent to mDot successfully!")
                            return True
                        else:
                            print("ERROR: mDot did not respond with OK - command may have failed")
                            print(f"Responses received: {final_responses}")
                            return False
                    else:
                        print(f'DEBUG: Payload size {payload_size} bytes exceeds limit {size_limit} bytes')
                        print('Contents larger than current lora transmission payload')
                        return False
                else:
                    # For bytes, use actual length
                    payload_size = len(content)
                    print(f"DEBUG: Bytes length: {payload_size} bytes")
                    
                    if payload_size <= size_limit:
                        # Write the data to the serial port
                        print(f"DEBUG: Sending data payload...")
                        self.ser.write(f'AT+SENDB={content.hex()}\r\n'.encode())
                        print("DEBUG: Data payload sent, waiting for response...")
                        
                        # extended wait for response
                        time.sleep(10)
                        
                        # Read response lines and verify success
                        final_responses = []
                        success = False
                        while self.ser.in_waiting > 0:
                            res = self.ser.read_until()
                            if res:
                                try:
                                    response = res.decode('utf-8').strip()
                                    final_responses.append(response)
                                    print(f"DEBUG: Final response: '{response}'")
                                    
                                    # Check for OK response
                                    if 'OK' in response:
                                        success = True
                                        print("DEBUG: mDot confirmed command execution with OK")
                                except UnicodeDecodeError:
                                    # Handle binary responses that can't be decoded as UTF-8
                                    response_hex = res.hex()
                                    final_responses.append(f"BINARY:{response_hex}")
                                    print(f"DEBUG: Binary response (hex): {response_hex}")
                                    
                                    # Check if this might be an OK response in binary form
                                    if b'OK' in res:
                                        success = True
                                        print("DEBUG: mDot confirmed command execution with OK (binary)")
                                    # For binary data, check if mDot echoed back the same data (indicates success)
                                    elif res == content:
                                        success = True
                                        print("DEBUG: mDot echoed back transmitted data - transmission successful")
                        
                        if success:
                            print("Data sent to mDot successfully!")
                            return True
                        else:
                            print("ERROR: mDot did not respond with OK - command may have failed")
                            print(f"Responses received: {final_responses}")
                            return False
                    else:
                        print(f'DEBUG: Payload size {payload_size} bytes exceeds limit {size_limit} bytes')
                        print('Contents larger than current lora transmission payload')
                        return False
                    
            except Exception as e:
                print(f"Error sending to mDot: {e}")
                import traceback
                traceback.print_exc()
                return False
    
    def queue_transmit(self, data: Dict[str, Any]) -> bool:
        """Queue sensor data for transmission (thread-safe)"""
        try:
            packet = self.compressed_encoding(data)
            self.transmit_queue.put(('sensor', packet))
            return True
        except Exception as e:
            print(f"Error queuing transmission: {e}")
            return False
    
    def queue_file_transmit(self, file_data: bytes) -> bool:
        """Queue file data for transmission (thread-safe)"""
        try:
            self.transmit_queue.put(('file', file_data))
            return True
        except Exception as e:
            print(f"Error queuing file transmission: {e}")
            return False
    
    def queue_binary_transmit(self, binary_data) -> bool:
        """Queue arbitrary binary data for transmission (thread-safe)"""
        try:
            # If it's a string, assume it's hex data and send directly
            if isinstance(binary_data, str):
                print(f"DEBUG: Detected hex string, queuing as hex data: {binary_data[:50]}...")
                self.transmit_queue.put(('hex_string', binary_data))
            else:
                # For bytes/bytearray, queue as binary
                print(f"DEBUG: Detected binary data, queuing as binary: {len(binary_data)} bytes")
                self.transmit_queue.put(('binary', binary_data))
            return True
        except Exception as e:
            print(f"Error queuing binary transmission: {e}")
            return False
    
    def queue_auto(self, data) -> bool:
        """Automatically detect data type and queue appropriately"""
        try:
            if isinstance(data, dict):
                print("DEBUG: Auto-detected dictionary, using queue_transmit")
                return self.queue_transmit(data)
            elif isinstance(data, (bytes, bytearray)):
                print("DEBUG: Auto-detected bytes, using queue_binary_transmit")
                return self.queue_binary_transmit(data)
            elif isinstance(data, str):
                print("DEBUG: Auto-detected string, assuming hex data and using queue_binary_transmit")
                return self.queue_binary_transmit(data)  # Will be handled as hex_string
            else:
                print(f"DEBUG: Auto-detected {type(data)}, converting to string then using queue_binary_transmit")
                return self.queue_binary_transmit(str(data))  # Will be handled as hex_string
        except Exception as e:
            print(f"Error in auto-queue: {e}")
            return False
    
    def process_transmit_queue(self):
        """Process queued transmissions"""
        while not self.transmit_queue.empty():
            try:
                item = self.transmit_queue.get_nowait()
                print(f"DEBUG: Processing queue item: {item}, type: {type(item)}")
                
                if isinstance(item, tuple) and len(item) == 2:
                    data_type, packet = item
                    print(f"DEBUG: Processing {data_type} packet, type: {type(packet)}")
                    
                    if data_type == 'hex_string':
                        # Hex string data - send directly without conversion
                        print(f"DEBUG: Sending hex string: {len(packet)} chars")
                        self.transmit(packet)
                    else:
                        # For other data types, ensure packet is bytes
                        if isinstance(packet, str):
                            print(f"WARNING: Converting string packet to bytes: {packet[:50]}...")
                            packet = packet.encode('utf-8')
                        elif not isinstance(packet, (bytes, bytearray)):
                            print(f"WARNING: Converting {type(packet)} packet to bytes: {packet}")
                            packet = str(packet).encode('utf-8')
                        
                        if data_type == 'sensor':
                            # Sensor data from compressed_encoding() - already bytes
                            print(f"DEBUG: Sending sensor data: {len(packet)} bytes")
                            self.transmit(packet)
                        elif data_type == 'file':
                            # File data - already bytes
                            print(f"DEBUG: Sending file data: {len(packet)} bytes")
                            self.transmit(packet)
                        elif data_type == 'binary':
                            # Arbitrary binary data - already bytes
                            print(f"DEBUG: Sending binary data: {len(packet)} bytes")
                            self.transmit(packet)
                        else:
                            print(f"Unknown data type: {data_type}")
                            continue
                else:
                    # Handle legacy format (backward compatibility)
                    packet = item
                    print(f"DEBUG: Processing legacy packet, type: {type(packet)}")
                    
                    try:
                        # Check if this looks like a hex string
                        if isinstance(packet, str) and all(c in '0123456789abcdefABCDEF' for c in packet):
                            print(f"DEBUG: Legacy packet appears to be hex string, treating as hex")
                            # Treat as hex string instead of trying UTF-8 conversion
                            self.transmit(packet)
                        else:
                            # Ensure packet is bytes for other types
                            if isinstance(packet, str):
                                print(f"WARNING: Converting legacy string packet to bytes: {packet[:50]}...")
                                packet = packet.encode('utf-8')
                            elif not isinstance(packet, (bytes, bytearray)):
                                print(f"WARNING: Converting legacy {type(packet)} packet to bytes: {packet}")
                                packet = str(packet).encode('utf-8')
                            
                            print(f"DEBUG: Legacy packet converted to bytes: {type(packet)}, length: {len(packet)}")
                            # Use transmit method which will verify OK response
                            result = self.transmit(packet)
                            if not result:
                                print("WARNING: Legacy packet transmission failed - no OK response")
                    except Exception as e:
                        print(f"ERROR: Failed to process legacy packet: {e}")
                        print(f"  Packet: {packet}")
                        print(f"  Packet type: {type(packet)}")
                        import traceback
                        traceback.print_exc()
                        continue
                    
            except queue.Empty:
                break
            except Exception as e:
                print(f"Error processing transmit queue: {e}")
                print(f"Item that caused error: {item}")
                print(f"Item type: {type(item)}")
                print(f"Error location: {e.__traceback__.tb_lineno if hasattr(e, '__traceback__') else 'unknown'}")
                import traceback
                traceback.print_exc()
    
    def compressed_encoding(self, data: Dict[str, Any]) -> bytes:
        """Handle encoding and compression of data from sensors for transmission over LoRa"""
        print(f"DEBUG: Starting compressed_encoding with data: {data}")
        print(f"DEBUG: Data types: {[(k, type(v)) for k, v in data.items()]}")
        packet = bytearray()
        
        def add_u8(ch, t, v): 
            try:
                print(f"DEBUG: add_u8 called with ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                packet.extend(bytes([ch, t, int(v)]))
            except Exception as e:
                print(f"Error in add_u8: ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                raise
        
        def add_u16(ch, t, v): 
            try:
                print(f"DEBUG: add_u16 called with ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                packet.extend(bytes([ch, t]))
                packet.extend(struct.pack(">H", int(v)))
            except Exception as e:
                print(f"Error in add_u16: ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                raise
        
        def add_u32(ch, t, v): 
            try:
                print(f"DEBUG: add_u32 called with ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                packet.extend(bytes([ch, t]))
                packet.extend(struct.pack(">I", int(v)))
            except Exception as e:
                print(f"Error in add_u32: ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                raise
        
        def add_f32(ch, t, v): 
            try:
                print(f"DEBUG: add_f32 called with ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                packet.extend(bytes([ch, t]))
                packet.extend(struct.pack(">f", float(v)))
            except Exception as e:
                print(f"Error in add_f32: ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                raise
        
        def add_f32_3(ch, t, v): 
            try:
                print(f"DEBUG: add_f32_3 called with ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                packet.extend(bytes([ch, t]))
                if not isinstance(v, (list, tuple)):
                    raise ValueError(f"Expected list/tuple for f32_3, got {type(v)}: {v}")
                for x in v:
                    packet.extend(struct.pack(">f", float(x)))
            except Exception as e:
                print(f"Error in add_f32_3: ch={ch}, t={t}, v={v}, type(v)={type(v)}")
                raise
        
        def add_blob(ch, t, b): 
            try:
                print(f"DEBUG: add_blob called with ch={ch}, t={t}, b={b}, type(b)={type(b)}")
                packet.extend(bytes([ch, t]))
                # Ensure b is bytes
                if isinstance(b, str):
                    print(f"DEBUG: Converting string to bytes: '{b}'")
                    b = b.encode('utf-8')
                elif not isinstance(b, (bytes, bytearray)):
                    print(f"DEBUG: Converting {type(b)} to bytes: {b}")
                    b = str(b).encode('utf-8')
                
                print(f"DEBUG: Final blob data type: {type(b)}, length: {len(b)}")
                packet.extend(struct.pack(">H", len(b)))
                packet.extend(b)
            except Exception as e:
                print(f"Error in add_blob: ch={ch}, t={t}, b={b}, type(b)={type(b)}")
                print(f"Error details: {e}")
                import traceback
                traceback.print_exc()
                raise

        try:
            print("DEBUG: Processing data fields...")
            if 'timestamp' in data: 
                print(f"DEBUG: Processing timestamp: {data['timestamp']}")
                add_u32(0x00, 0x01, data['timestamp'])
            if 'emergency_status' in data: 
                print(f"DEBUG: Processing emergency_status: {data['emergency_status']}")
                add_u8(0x01, 0x04, data['emergency_status'])
            if 'health_status' in data: 
                print(f"DEBUG: Processing health_status: {data['health_status']}")
                add_u8(0x01, 0x05, data['health_status'])
            if 'movement_threshold' in data: 
                print(f"DEBUG: Processing movement_threshold: {data['movement_threshold']}")
                add_u8(0x01, 0x06, data['movement_threshold'])
            if 'battery_percent' in data: 
                print(f"DEBUG: Processing battery_percent: {data['battery_percent']}")
                add_u8(0x02, 0x01, data['battery_percent'])
            if 'tilt_roll_yaw' in data: 
                print(f"DEBUG: Processing tilt_roll_yaw: {data['tilt_roll_yaw']}")
                add_f32_3(0x03, 0x01, data['tilt_roll_yaw'])
            if 'lat_lon_z' in data: 
                print(f"DEBUG: Processing lat_lon_z: {data['lat_lon_z']}")
                add_f32_3(0x04, 0x01, data['lat_lon_z'])
            if 'temperature_celsius' in data: 
                print(f"DEBUG: Processing temperature_celsius: {data['temperature_celsius']}")
                add_f32(0x05, 0x01, data['temperature_celsius'])
            if 'relative_humidity' in data: 
                print(f"DEBUG: Processing relative_humidity: {data['relative_humidity']}")
                add_u8(0x06, 0x01, data['relative_humidity'])
            if 'camera_flood_detected' in data: 
                print(f"DEBUG: Processing camera_flood_detected: {data['camera_flood_detected']}")
                add_u8(0x07, 0x17, data['camera_flood_detected'])
            if 'camera_flood_growing' in data: 
                print(f"DEBUG: Processing camera_flood_growing: {data['camera_flood_growing']}")
                add_u8(0x07, 0x27, data['camera_flood_growing'])
            if 'flood_bitmap_compressed' in data: 
                print(f"DEBUG: Processing flood_bitmap_compressed: {data['flood_bitmap_compressed']}")
                add_blob(0x08, 0x18, data['flood_bitmap_compressed'])
            if 'status_area_threshold' in data: 
                print(f"DEBUG: Processing status_area_threshold: {data['status_area_threshold']}")
                add_u8(0x09, 0x19, data['status_area_threshold'])
            if 'stage_threshold' in data: 
                print(f"DEBUG: Processing stage_threshold: {data['stage_threshold']}")
                add_u8(0x09, 0x29, data['stage_threshold'])
            if 'monitoring_frequency' in data: 
                print(f"DEBUG: Processing monitoring_frequency: {data['monitoring_frequency']}")
                add_u16(0x09, 0x39, data['monitoring_frequency'])
            if 'emergency_frequency' in data: 
                print(f"DEBUG: Processing emergency_frequency: {data['emergency_frequency']}")
                add_u16(0x09, 0x49, data['emergency_frequency'])
            if 'neighborhood_emergency_frequency' in data: 
                print(f"DEBUG: Processing neighborhood_emergency_frequency: {data['neighborhood_emergency_frequency']}")
                add_u16(0x09, 0x59, data['neighborhood_emergency_frequency'])

            print(f"DEBUG: Final packet size: {len(packet)} bytes")
            return bytes(packet)
        except Exception as e:
            print(f"Error in compressed_encoding: {e}")
            print(f"Data: {data}")
            raise
    
    def decode(self, payload: str):
        """Decode incoming payload and update configuration"""
        try:
            command = payload[:2]
            param = payload[2:]
            
            if command == '10':
                # Area threshold - 10% increments
                val = int(param) * 10
                self.update_config('area_threshold', val)
                print(f'Area threshold updated to: {val}%')
                
            elif command == '11':
                # Stage threshold - continuous cm value
                val = float(param)
                self.update_config('stage_threshold', val)
                print(f'Stage threshold updated to: {val} cm')
                
            elif command == '12':
                # Monitoring frequency - minute value
                val = int(param)
                self.update_config('monitoring_frequency', val)
                print(f'Monitoring frequency updated to: {val} minutes')
                
            elif payload == '21':
                # Emergency status
                self.update_config('emergency_mode', True)
                print('Emergency mode activated!')
                
            else:
                print(f'Unknown command: {command} with param: {param}')
                
        except Exception as e:
            print(f"Error decoding payload '{payload}': {e}")
    
    def check_mdot_status(self):
        """Check mDot status and mode"""
        try:
            print("DEBUG: Checking mDot status...")
            
            # Clear buffers first
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            # Send newline to clear any partial commands first
            self.ser.write('\r\n'.encode())
            time.sleep(0.1)
            
            # Check if mDot responds to basic AT command
            print("DEBUG: Sending AT command...")
            self.ser.write('AT\r\n'.encode())
            time.sleep(1)
            
            responses = []
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: AT response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary AT response (hex): {response_hex}")
           
           
            # Check join status
            print("DEBUG: Sending AT+NJS? command...")
            self.ser.flush()
            # Send newline to clear any partial commands
            self.ser.write('\r\n'.encode())
            time.sleep(0.5)
            self.ser.write('AT+NJS?\r\n'.encode())
            time.sleep(0.5)
            
            responses = []
            njs_success = False
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: Join status response: '{response}'")
                        if 'OK' in response:
                            njs_success = True
                            print("DEBUG: AT+NJS? command successful")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary join status response (hex): {response_hex}")
                        if b'OK' in res:
                            njs_success = True
                            print("DEBUG: AT+NJS? command successful (binary)")
            
            if not njs_success:
                print("WARNING: AT+NJS? command did not receive OK response")
            
            return ver_success and njs_success
        except Exception as e:
            print(f"DEBUG: Error checking mDot status: {e}")
            import traceback
            traceback.print_exc()
            return False
    
   
    def close(self):
        """Clean up resources"""
        self.stop_listening()
        if self.ser.is_open:
            self.ser.close()
        print("LoRa handler closed")

# Global instance for easy access from other modules
_lora_handler = None

def get_lora_handler() -> LoRaHandler:
    """Get the global LoRa handler instance"""
    global _lora_handler
    if _lora_handler is None:
        _lora_handler = LoRaHandler()
        _lora_handler.start_listening()
    return _lora_handler

def transmit_data(data: Dict[str, Any]) -> bool:
    """Convenience function to transmit sensor data"""
    handler = get_lora_handler()
    return handler.queue_transmit(data)

def transmit_file(file_data: bytes) -> bool:
    """Convenience function to transmit file data"""
    handler = get_lora_handler()
    return handler.queue_file_transmit(file_data)

def transmit_binary(binary_data) -> bool:
    """Convenience function to transmit arbitrary binary data (bytes or hex string)"""
    handler = get_lora_handler()
    return handler.queue_binary_transmit(binary_data)

def transmit_auto(data) -> bool:
    """Convenience function to automatically detect data type and transmit"""
    handler = get_lora_handler()
    return handler.queue_auto(data)

def get_config_value(key: str, default: Any = None) -> Any:
    """Convenience function to get configuration values"""
    handler = get_lora_handler()
    return handler.get_config(key, default)

# Legacy functions for backward compatibility
def transmit(content):
    """Legacy transmit function"""
    handler = get_lora_handler()
    return handler.transmit(content)

def compressed_encoding(data):
    """Legacy encoding function"""
    handler = get_lora_handler()
    return handler.compressed_encoding(data)

# Example packet for testing
example_packet = {
    "timestamp": 1748892908,
    "emergency_status": 1,
    "health_status": 1,
    "battery_percent": 5,
    "temperature_celsius": 23.5,
    "tilt_roll_yaw": [0.1, 0.2, 0.3],
    "lat_lon_z": [40.7128, -74.0060, 12.5],
    "relative_humidity": 55
}

if __name__ == "__main__":
    from sys import argv
    
    handler = LoRaHandler()
    
    try:
        # send a packet when run without an argument
        if len(argv) == 1:
            handler.queue_transmit(example_packet)
            handler.process_transmit_queue()
        elif argv[1] == 'listen':
            # Start listening and keep running
            handler.start_listening()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping...")
        # else transmit the file param
        elif argv[1] == 'binary':
            # Transmit arbitrary binary data
            binary_data = b'\x01\x02\x03\x04\x05\x06\x07\x08'
            handler.queue_binary_transmit(binary_data)
            handler.process_transmit_queue()
        else:
            with open(argv[1], 'rb') as file:
                file_data = file.read()
                handler.queue_file_transmit(file_data)
                handler.process_transmit_queue()
    finally:
        handler.close()

