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
# Format: [Channel][Command][Value]
# Channel: Two digits (00-99) representing the data channel
# Command: Two digits representing the command type
# Value: Remaining digits representing the value to set
# 
# Examples:
# 1090 -> Channel 10, Command 90, Value 0 (Area Threshold 0%)
# 1010 -> Channel 10, Command 90, Value 10 (Area Threshold 100%)
# 1191 -> Channel 11, Command 91, Value 1 (Stage Threshold 1 cm)
# 1192 -> Channel 11, Command 91, Value 92 (Stage Threshold 92 cm)
# 1292 -> Channel 12, Command 92, Value 2 (Monitoring Frequency 2 minutes)
# 1393 -> Channel 13, Command 93, Value 3 (Emergency Frequency 3 minutes)
# 2100 -> Channel 21, Command 00, Value 0 (Emergency status: system enters emergency mode)
# 9900 -> Channel 99, Command 00, Value 0 (Deactivate emergency mode)

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
        
        # Callback for runtime integration
        self.runtime_callback = None
        
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
            'neighborhood_emergency_frequency': 30,
            'photo_interval': 60,
            'transmission_enabled': True,
            'debug_mode': False,
            'gps_enabled': True,
            'battery_threshold': 20,
            'compression_level': 5,
            'max_retransmissions': 3,
            'auto_shutdown_enabled': True,
            'shutdown_iteration_limit': 10,
            'data_retention_days': 30,
            'backup_enabled': True,
            'emergency_mode': False
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
        
        # Call runtime callback if registered
        print(f"DEBUG: Runtime callback registered: {self.runtime_callback is not None}")
        if self.runtime_callback:
            try:
                print(f"DEBUG: Calling runtime callback with key='{key}', value={value}")
                self.runtime_callback(key, value)
                print(f"DEBUG: Runtime callback completed successfully")
            except Exception as e:
                print(f"Error in runtime callback for {key}: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"DEBUG: No runtime callback registered")
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a configuration value"""
        return self.config.get(key, default)
    
    def set_runtime_callback(self, callback):
        """Set callback for runtime integration updates"""
        self.runtime_callback = callback
        print("Runtime callback registered with LoRa handler")
    
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
                        print(f"DEBUG: Raw received: '{res}'")
                        if res and "DATA=" in res:
                            try:
                                print(f"DEBUG: Found DATA= in message: '{res}'")
                                data = res.split("DATA=", 1)[1]
                                print(f"DEBUG: Extracted data: '{data}'")
                                print(f"DEBUG: Data type: {type(data)}")
                                print(f"DEBUG: Data length: {len(data)}")
                                print(f"DEBUG: Data hex: {data.encode().hex()}")
                                print(f"DEBUG: Data repr: {repr(data)}")
                                print(f"📡 LoRa packet received: {data}")
                                self.decode(data)
                            except Exception as e:
                                print(f"Error processing received data: {e}")
                                import traceback
                                traceback.print_exc()
                        elif res and res.strip().isdigit() and len(res.strip()) >= 2:
                            # Handle case where mDot sends data directly without DATA= prefix
                            try:
                                print(f"DEBUG: Found direct numeric message: '{res}'")
                                data = res.strip()
                                print(f"DEBUG: Processing direct data: '{data}'")
                                print(f"📡 LoRa packet received (direct): {data}")
                                self.decode(data)
                            except Exception as e:
                                print(f"Error processing direct data: {e}")
                                import traceback
                                traceback.print_exc()
                        elif res:
                            print(f"DEBUG: Non-DATA message: '{res}'")
                            print(f"DEBUG: Non-DATA message hex: {res.encode().hex()}")
                            print(f"DEBUG: Non-DATA message repr: {repr(res)}")
                            
                            # Check if this might be a different format of incoming data
                            if res.startswith("RX:") or res.startswith("RX ") or "RX" in res:
                                print(f"DEBUG: Possible RX message detected: {res}")
                                # Extract data after RX
                                try:
                                    if ":" in res:
                                        rx_data = res.split(":", 1)[1].strip()
                                        print(f"DEBUG: Extracted RX data: '{rx_data}'")
                                        if rx_data.isdigit():
                                            print(f"DEBUG: Attempting to decode RX data as command: {rx_data}")
                                            self.decode(rx_data)
                                    else:
                                        rx_data = res.replace("RX", "").strip()
                                        print(f"DEBUG: Extracted RX data: '{rx_data}'")
                                        if rx_data.isdigit():
                                            print(f"DEBUG: Attempting to decode RX data as command: {rx_data}")
                                            self.decode(rx_data)
                                except Exception as e:
                                    print(f"DEBUG: Failed to decode RX data: {e}")
                            elif res.startswith("+") and ":" in res:
                                print(f"DEBUG: Possible AT response message: {res}")
                            elif res.isdigit() or res.replace('.', '').replace('-', '').isdigit():
                                print(f"DEBUG: Possible numeric data message: {res}")
                                # Try to decode it directly as a command
                                try:
                                    print(f"DEBUG: Attempting to decode numeric message as command: {res}")
                                    self.decode(res)
                                except Exception as e:
                                    print(f"DEBUG: Failed to decode numeric message: {e}")
                            elif res.strip() and len(res.strip()) >= 2:
                                # Check if this might be a command without any prefix
                                stripped = res.strip()
                                print(f"DEBUG: Possible raw command message: '{stripped}'")
                                if stripped.isdigit() and len(stripped) >= 2:
                                    print(f"DEBUG: Attempting to decode raw command: {stripped}")
                                    self.decode(stripped)
                    except UnicodeDecodeError:
                        # Handle binary data that can't be decoded as UTF-8
                        res_raw = self.ser.readline()
                        print(f"Received binary data (hex): {res_raw.hex()}")
                else:
                    time.sleep(0.5)  # Small delay to prevent busy waiting
            except Exception as e:
                print(f"Error in listen loop: {e}")
                import traceback
                traceback.print_exc()
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
            print(f"DEBUG: Decoding payload: '{payload}'")
            print(f"DEBUG: Payload type: {type(payload)}")
            print(f"DEBUG: Payload length: {len(payload)}")
            print(f"DEBUG: Payload hex: {payload.encode().hex()}")
            print(f"DEBUG: Payload repr: {repr(payload)}")
            
            # Track command timestamp for frequency adjustment
            from datetime import datetime
            self.update_config('last_lora_command_time', datetime.now().isoformat())
            print(f"📡 LoRa command received at {datetime.now().strftime('%H:%M:%S')}")
            
            # Handle emergency mode command (no parameters) - legacy format
            if payload == '21':
                self.update_config('emergency_mode', True)
                print('🚨 Emergency mode activated!')
                return
            
            # Handle new format: [Channel][Command][Value]
            if len(payload) >= 4:
                # Clean the payload to remove any whitespace or special characters
                clean_payload = payload.strip()
                print(f"DEBUG: Cleaned payload: '{clean_payload}' (length: {len(clean_payload)})")
                
                if len(clean_payload) >= 4:
                    # Parse the format: [Channel][Command][Value]
                    # Channel: 2 digits (10, 11, 12, etc.)
                    # Command: 1 digit (9, 0, etc.)
                    # Value: remaining digits (0, 1, 10, etc.)
                    channel = clean_payload[:2]
                    command = clean_payload[2:3]  # Single digit command
                    value = clean_payload[3:]     # Remaining digits as value
                    
                    print(f"DEBUG: Channel: '{channel}', Command: '{command}', Value: '{value}'")
                    print(f"DEBUG: Channel length: {len(channel)}, Command length: {len(command)}, Value length: {len(value)}")
                    print(f"DEBUG: Channel hex: {channel.encode().hex()}, Command hex: {command.encode().hex()}, Value hex: {value.encode().hex()}")
                    print(f"DEBUG: Channel repr: {repr(channel)}, Command repr: {repr(command)}, Value repr: {repr(value)}")
                    
                    # Validate that channel and command are not empty or just whitespace
                    if not channel or channel.isspace() or not command or command.isspace():
                        print(f"ERROR: Invalid channel or command - empty or whitespace: channel='{channel}', command='{command}'")
                        return
                    
                    # Process commands based on channel and command combination
                    if channel == '10' and command == '9':
                        # Area threshold - value represents 10% increments
                        try:
                            val = int(value) * 10
                            print(f"DEBUG: Calculated area threshold: {value} * 10 = {val}%")
                            self.update_config('area_threshold', val)
                            print(f'Area threshold updated to: {val}%')
                        except ValueError as e:
                            print(f'Invalid area threshold value: {value}, error: {e}')
                            print(f'DEBUG: Value type: {type(value)}, Value repr: {repr(value)}')
                            
                    elif channel == '11' and command == '9':
                        # Stage threshold - continuous cm value
                        try:
                            val = float(value)
                            self.update_config('stage_threshold', val)
                            print(f'Stage threshold updated to: {val} cm')
                        except ValueError:
                            print(f'Invalid stage threshold value: {value}')
                            
                    elif channel == '12' and command == '9':
                        # Monitoring frequency - minute value
                        try:
                            val = int(value)
                            self.update_config('monitoring_frequency', val)
                            print(f'Monitoring frequency updated to: {val} minutes')
                        except ValueError:
                            print(f'Invalid monitoring frequency value: {value}')
                            
                    elif channel == '13' and command == '9':
                        # Emergency frequency - minute value
                        try:
                            val = int(value)
                            self.update_config('emergency_frequency', val)
                            print(f'Emergency frequency updated to: {val} minutes')
                        except ValueError:
                            print(f'Invalid emergency frequency value: {value}')
                            
                    elif channel == '14' and command == '9':
                        # Photo interval - minute value
                        try:
                            val = int(value)
                            self.update_config('photo_interval', val)
                            print(f'Photo interval updated to: {val} minutes')
                        except ValueError:
                            print(f'Invalid photo interval value: {value}')
                            
                    elif channel == '15' and command == '9':
                        # Neighborhood emergency frequency - minute value
                        try:
                            val = int(value)
                            self.update_config('neighborhood_emergency_frequency', val)
                            print(f'Neighborhood emergency frequency updated to: {val} minutes')
                        except ValueError:
                            print(f'Invalid neighborhood emergency frequency value: {value}')
                            
                    elif channel == '20' and command == '0':
                        # Transmission enabled/disabled
                        try:
                            val = bool(int(value))
                            self.update_config('transmission_enabled', val)
                            print(f'Transmission {"enabled" if val else "disabled"}')
                        except ValueError:
                            print(f'Invalid transmission enabled value: {value}')
                            
                    elif channel == '22' and command == '0':
                        # Debug mode
                        try:
                            val = bool(int(value))
                            self.update_config('debug_mode', val)
                            print(f'Debug mode {"enabled" if val else "disabled"}')
                        except ValueError:
                            print(f'Invalid debug mode value: {value}')
                            
                    elif channel == '23' and command == '0':
                        # GPS enabled/disabled
                        try:
                            val = bool(int(value))
                            self.update_config('gps_enabled', val)
                            print(f'GPS {"enabled" if val else "disabled"}')
                        except ValueError:
                            print(f'Invalid GPS enabled value: {value}')
                            
                    elif channel == '30' and command == '0':
                        # Battery threshold
                        try:
                            val = int(value)
                            self.update_config('battery_threshold', val)
                            print(f'Battery threshold updated to: {val}%')
                        except ValueError:
                            print(f'Invalid battery threshold value: {value}')
                            
                    elif channel == '31' and command == '0':
                        # Compression level
                        try:
                            val = int(value)
                            val = max(1, min(10, val))  # Clamp between 1-10
                            self.update_config('compression_level', val)
                            print(f'Compression level updated to: {val}')
                        except ValueError:
                            print(f'Invalid compression level value: {value}')
                            
                    elif channel == '32' and command == '0':
                        # Max retransmissions
                        try:
                            val = int(value)
                            self.update_config('max_retransmissions', val)
                            print(f'Max retransmissions updated to: {val}')
                        except ValueError:
                            print(f'Invalid max retransmissions value: {value}')
                            
                    elif channel == '40' and command == '0':
                        # Auto shutdown enabled/disabled
                        try:
                            val = bool(int(value))
                            self.update_config('auto_shutdown_enabled', val)
                            print(f'Auto shutdown {"enabled" if val else "disabled"}')
                        except ValueError:
                            print(f'Invalid auto shutdown value: {value}')
                            
                    elif channel == '41' and command == '0':
                        # Shutdown iteration limit
                        try:
                            val = int(value)
                            self.update_config('shutdown_iteration_limit', val)
                            print(f'Shutdown iteration limit updated to: {val}')
                        except ValueError:
                            print(f'Invalid shutdown iteration limit value: {value}')
                            
                    elif channel == '42' and command == '0':
                        # Data retention days
                        try:
                            val = int(value)
                            self.update_config('data_retention_days', val)
                            print(f'Data retention updated to: {val} days')
                        except ValueError:
                            print(f'Invalid data retention value: {value}')
                            
                    elif channel == '43' and command == '0':
                        # Backup enabled/disabled
                        try:
                            val = bool(int(value))
                            self.update_config('backup_enabled', val)
                            print(f'Backup {"enabled" if val else "disabled"}')
                        except ValueError:
                            print(f'Invalid backup value: {value}')
                            
                    elif channel == '21' and command == '0':
                        # Emergency status: system enters emergency mode and stops scheduled shutdowns
                        self.update_config('emergency_mode', True)
                        print('🚨 Emergency mode activated!')
                        
                    elif channel == '99' and command == '0':
                        # Deactivate emergency mode
                        self.update_config('emergency_mode', False)
                        print('✅ Emergency mode deactivated')
                        
                    else:
                        print(f'Unknown channel/command combination: Channel {channel}, Command {command} with value: {value}')
                else:
                    print(f"ERROR: Cleaned payload too short for new format: '{clean_payload}' (length: {len(clean_payload)})")
                    return
            else:
                print(f'Invalid payload format: {payload} (minimum 4 characters required for [Channel][Command][Value] format)')
                
        except Exception as e:
            print(f"Error decoding payload '{payload}': {e}")
            import traceback
            traceback.print_exc()
    
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
            ver_success = False
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: AT response: '{response}'")
                        if 'OK' in response:
                            ver_success = True
                            print("DEBUG: AT command successful")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary AT response (hex): {response_hex}")
                        if b'OK' in res:
                            ver_success = True
                            print("DEBUG: AT command successful (binary)")
           
           
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
    
    def check_reception_status(self):
        """Check if the LoRa handler is properly receiving data"""
        try:
            print("DEBUG: Checking LoRa reception status...")
            
            # Check if listener thread is running
            if not self.listening:
                print("WARNING: LoRa listener is not running")
                return False
            
            if not self.listener_thread or not self.listener_thread.is_alive():
                print("WARNING: LoRa listener thread is not alive")
                return False
            
            # Check serial port status
            if not self.ser.is_open:
                print("WARNING: Serial port is not open")
                return False
            
            # Check if there's data waiting
            if self.ser.in_waiting > 0:
                print(f"DEBUG: {self.ser.in_waiting} bytes waiting in serial buffer")
                
                # Read and display waiting data
                while self.ser.in_waiting > 0:
                    res = self.ser.read_until()
                    if res:
                        try:
                            response = res.decode('utf-8').strip()
                            print(f"DEBUG: Waiting data: '{response}'")
                        except UnicodeDecodeError:
                            response_hex = res.hex()
                            print(f"DEBUG: Waiting binary data (hex): {response_hex}")
            else:
                print("DEBUG: No data waiting in serial buffer")
            
            print("DEBUG: LoRa reception status check completed")
            return True
            
        except Exception as e:
            print(f"DEBUG: Error checking reception status: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def check_mdot_data_config(self):
        """Check mDot data reception configuration"""
        try:
            print("DEBUG: Checking mDot data reception configuration...")
            
            # Clear buffers first
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            # Send newline to clear any partial commands
            self.ser.write('\r\n'.encode())
            time.sleep(0.1)
            
            # Check current data format configuration
            print("DEBUG: Checking data format configuration...")
            self.ser.write('AT+DFORMAT?\r\n'.encode())
            time.sleep(1)
            
            responses = []
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: DFORMAT response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary DFORMAT response (hex): {response_hex}")
            
            # Check if there are any pending messages
            print("DEBUG: Checking for pending messages...")
            self.ser.write('AT+MSG?\r\n'.encode())
            time.sleep(1)
            
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        print(f"DEBUG: MSG response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        print(f"DEBUG: Binary MSG response (hex): {response_hex}")
            
            print("DEBUG: mDot data configuration check completed")
            return True
            
        except Exception as e:
            print(f"DEBUG: Error checking mDot data config: {e}")
            import traceback
            traceback.print_exc()
            return False
    
   
    def close(self):
        """Clean up resources"""
        self.stop_listening()
        if self.ser.is_open:
            self.ser.close()
        print("LoRa handler closed")
    
    def test_reception_format(self, test_payload: str):
        """Test the new reception format with a sample payload"""
        print(f"🧪 Testing reception format with payload: {test_payload}")
        print(f"Expected format: [Channel][Command][Value]")
        
        if len(test_payload) >= 4:
            channel = test_payload[:2]
            command = test_payload[2:4]
            value = test_payload[4:]
            print(f"Parsed: Channel={channel}, Command={command}, Value={value}")
            
            # Test decoding
            self.decode(test_payload)
        else:
            print(f"Invalid test payload: {test_payload} (minimum 4 characters required)")

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

def check_lora_reception():
    """Convenience function to check LoRa reception status"""
    handler = get_lora_handler()
    return handler.check_reception_status()

def check_mdot_data_config():
    """Convenience function to check mDot data reception configuration"""
    handler = get_lora_handler()
    return handler.check_mdot_data_config()

def test_reception_format(test_payload: str):
    """Convenience function to test the new reception format"""
    handler = get_lora_handler()
    return handler.test_reception_format(test_payload)

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
        elif argv[1] == 'test':
            # Test the new reception format
            if len(argv) >= 3:
                test_payload = argv[2]
                handler.test_reception_format(test_payload)
            else:
                print("Usage: python lora_handler_concurrent.py test <payload>")
                print("Example: python lora_handler_concurrent.py test 1090")
                print("Example: python lora_handler_concurrent.py test 1191")
                print("Example: python lora_handler_concurrent.py test 2100")
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

