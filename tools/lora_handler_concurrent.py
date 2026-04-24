#!/usr/bin/env python
import fcntl
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
# 0A 01 wittypi temperature (float, celsius)
# 0A 02 wittypi battery voltage (float, volts)
# 0A 03 wittypi internal voltage (float, volts)

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
# 9999 -> Emergency clear message (Deactivate emergency mode)

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
        
        # Size limit tracking
        self.current_size_limit = 242  # Default LoRaWAN payload size
        
        # Transmission status tracking
        self.last_transmission_status = None
        self.transmission_history = []
        

        
        # Configure the serial port
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=115200,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1
            )
            
            if not self.ser.is_open:
                print(f"❌ Serial port {port} failed to open")
                raise RuntimeError(f"Serial port {port} failed to open")
                
        except serial.SerialException as e:
            print(f"❌ Serial port error on {port}: {e}")
            print(f"   Check if device exists and has proper permissions")
            raise RuntimeError(f"Serial port {port} error: {e}")
        except Exception as e:
            print(f"❌ Unexpected error initializing serial port {port}: {e}")
            raise RuntimeError(f"Serial port {port} initialization failed: {e}")
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        default_config = {
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'emergency_frequency': 30,
            'neighborhood_emergency_frequency': 30,
            'photo_interval': 60,
            'debug_mode': False,
            'max_retransmissions': 3,
            'auto_shutdown_enabled': True,
            'shutdown_iteration_limit': 2,
            'data_retention_days': 30,
            'backup_enabled': True,
            'emergency_mode': False
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        return json.load(f)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
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
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    json.dump(config, f, indent=2)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
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
                        with self.transmit_lock:
                            raw = self.ser.readline()
                        res = raw.decode().strip()
                        print(f"DEBUG: Raw received: '{res}'")
                        # Skip empty messages
                        if not res:
                            continue
                        
                        # Skip AT+SENDB= messages (these are transmission commands, not incoming data)
                        if res.startswith('AT+SENDB='):
                            print(f"DEBUG: Skipping transmission command echo: '{res[:50]}...'")
                            continue
                        
                        # Validate message integrity before processing
                        if not self._is_valid_message(res):
                            print(f"⚠️ Skipping corrupted/invalid message: '{res}'")
                            continue
                        
                        # Check for emergency messages FIRST (highest priority) - ANY message with "EMERGENCY" triggers emergency mode
                        if self._is_emergency_message(res):
                            emergency_status = self._extract_emergency_status(res)
                            if emergency_status:
                                print(f"🚨 EMERGENCY TRIGGERED: '{res}'")
                                # Always activate emergency mode for any EMERGENCY message
                                try:
                                    from tools.lora_runtime_integration import set_parameter
                                    set_parameter('emergency_mode', True)
                                    print("✅ Emergency mode activated via EMERGENCY message")
                                except Exception as e:
                                    print(f"⚠️ Failed to set emergency mode: {e}")
                            continue  # Emergency messages are handled, don't process further
                        
                        # Check for emergency clear messages SECOND (high priority) - only "9999" turns off emergency mode  
                        elif self._is_emergency_clear_message(res):
                            print(f"✅ Emergency clear message received: '{res}'")
                            # Clear emergency mode in runtime integration
                            try:
                                from tools.lora_runtime_integration import set_parameter
                                set_parameter('emergency_mode', False)
                                print("✅ Emergency mode deactivated via '9999' clear message")
                            except Exception as e:
                                print(f"⚠️ Failed to clear emergency mode: {e}")
                            continue  # Emergency clear messages are handled, don't process further
                        
                        # Look for actual LoRa data messages THIRD (priority)
                        if self._is_lora_data(res):
                            try:
                                data = self._extract_lora_data(res)
                                if data:
                                    print(f"📡 LoRa packet received: {data}")
                                    self.decode(data)
                                else:
                                    print(f"DEBUG: Could not extract LoRa data from: '{res}'")
                            except Exception as e:
                                print(f"Error processing LoRa data: {e}")
                                import traceback
                                traceback.print_exc()
                        # Check for +TXS: responses (size limit information) - SECOND priority
                        elif self._is_txs_response(res):
                            size_limit = self._extract_txs_size_limit(res)
                            if size_limit is not None:
                                print(f"📏 mDot size limit received: {size_limit} bytes")
                                # Store the size limit for use in transmission
                                self.current_size_limit = size_limit
                            else:
                                print(f"DEBUG: Could not extract size limit from TXS response: '{res}'")
                        # Check for transmission-related responses (THIRD priority)
                        elif self._is_transmission_response(res):
                            status = self._extract_transmission_status(res)
                            # Store the transmission status
                            self.last_transmission_status = status
                            self.transmission_history.append(status)
                            # Keep only last 10 entries to prevent memory bloat
                            if len(self.transmission_history) > 10:
                                self.transmission_history.pop(0)
                            
                            if status['success']:
                                print(f"✅ Transmission success: {status['details'].get('confirmation', 'OK')}")
                            else:
                                print(f"❌ Transmission error: {status['details'].get('error', 'Unknown error')}")
                            print(f"📡 Transmission response details: {status}")
                            # Could trigger callbacks or update transmission status here
                        # Then check if it's an AT command response (lower priority)
                        elif self._is_at_response(res):
                            print(f"DEBUG: AT response (ignoring): '{res}'")
                        else:
                            print(f"DEBUG: Non-LoRa message (ignoring): '{res}'")
                    except UnicodeDecodeError:
                        print(f"Received binary data (hex): {raw.hex()}")
                else:
                    time.sleep(0.5)  # Small delay to prevent busy waiting
            except Exception as e:
                print(f"Error in listen loop: {e}")
                
                # Handle serial port disconnection specifically
                if "device disconnected" in str(e).lower() or "no data" in str(e).lower():
                    print(f"⚠️ Serial port disconnected or no data available: {e}")
                    # Check connection and attempt reconnection if needed
                    if not self._check_serial_connection():
                        if self._attempt_reconnection():
                            print("✅ Reconnection successful, continuing...")
                            continue
                        else:
                            print("❌ Reconnection failed, stopping listener")
                            break
                    else:
                        print("⚠️ Connection appears fine, waiting before retry...")
                        time.sleep(2)
                        continue
                else:
                    import traceback
                    traceback.print_exc()
                    time.sleep(1)  # Longer delay on error
    
    def transmit(self, content: bytes, max_retries: int = 2) -> bool:
        """Transmit data with thread safety and error recovery"""
        with self.transmit_lock:
            for attempt in range(max_retries + 1):
                try:
                    if attempt > 0:
                        print(f"DEBUG: Retry attempt {attempt}/{max_retries}")
                        # Clear mDot input on retry
                        if not self._clear_mdot_input():
                            print("ERROR: Failed to clear mDot input, aborting retry")
                            return False
                    
                    print(f"DEBUG: Starting transmission of {len(content)} bytes (attempt {attempt + 1})")
                    print(f"DEBUG: Content to send: {content}")
                    
                    # Ensure the serial connection is ready to send
                    self.ser.flush()
                    self.ser.reset_input_buffer()
                    
                    # Send newline to clear any partial commands
                    print("DEBUG: Clearing AT interface with newline...")
                    self.ser.write('\r\n'.encode())
                    time.sleep(0.5)  # Brief pause to let mDot process
                    
                    # Use the stored size limit (updated via listening loop)
                    size_limit = self.current_size_limit
                    print(f"DEBUG: Using stored size limit: {size_limit} bytes")
                    
                    # Optionally refresh the size limit by sending AT+TXS
                    print("DEBUG: Sending AT+TXS command to refresh size limit...")
                    self.ser.write('AT+TXS\r\n'.encode())
                    
                    # Brief wait for mDot to process (size limit will be updated via listening loop)
                    time.sleep(0.5)
                    
                    # Attempt transmission
                    success, error_message = self._attempt_transmission(content, size_limit)
                    
                    if success:
                        return True
                    else:
                        print(f"ERROR: Transmission attempt {attempt + 1} failed: {error_message}")
                        
                        # If this is not the last attempt, continue to retry
                        if attempt < max_retries:
                            print(f"DEBUG: Will retry transmission (attempt {attempt + 2}/{max_retries + 1})")
                            time.sleep(1)  # Brief delay before retry
                            continue
                        else:
                            print(f"ERROR: All {max_retries + 1} transmission attempts failed")
                            return False
                        
                except Exception as e:
                    print(f"Error in transmission attempt {attempt + 1}: {e}")
                    import traceback
                    traceback.print_exc()
                    
                    # If this is not the last attempt, continue to retry
                    if attempt < max_retries:
                        print(f"DEBUG: Will retry transmission after exception (attempt {attempt + 2}/{max_retries + 1})")
                        time.sleep(1)  # Brief delay before retry
                        continue
                    else:
                        print(f"ERROR: All {max_retries + 1} transmission attempts failed due to exceptions")
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
            if data.get('battery_percent') is not None:
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
                add_u8(0x09, 0x29, max(0, min(255, int(data['stage_threshold']))))
            if 'monitoring_frequency' in data: 
                print(f"DEBUG: Processing monitoring_frequency: {data['monitoring_frequency']}")
                add_u16(0x09, 0x39, data['monitoring_frequency'])
            if 'emergency_frequency' in data: 
                print(f"DEBUG: Processing emergency_frequency: {data['emergency_frequency']}")
                add_u16(0x09, 0x49, data['emergency_frequency'])
            if 'neighborhood_emergency_frequency' in data: 
                print(f"DEBUG: Processing neighborhood_emergency_frequency: {data['neighborhood_emergency_frequency']}")
                add_u16(0x09, 0x59, data['neighborhood_emergency_frequency'])
            
            # WittyPi voltage measurements for battery status
            if 'wittypi_temperature' in data: 
                print(f"DEBUG: Processing wittypi_temperature: {data['wittypi_temperature']}")
                add_f32(0x0A, 0x01, data['wittypi_temperature'])
            if 'wittypi_battery_voltage' in data: 
                print(f"DEBUG: Processing wittypi_battery_voltage: {data['wittypi_battery_voltage']}")
                add_f32(0x0A, 0x02, data['wittypi_battery_voltage'])
            if 'wittypi_internal_voltage' in data: 
                print(f"DEBUG: Processing wittypi_internal_voltage: {data['wittypi_internal_voltage']}")
                add_f32(0x0A, 0x03, data['wittypi_internal_voltage'])

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
            
            # First try TLV hex multi-command format: [ch:1B][cmd:1B][len:1B][value:len]
            # Entire payload must be hex characters (no delimiters) and even length
            def _is_hex_string(s: str) -> bool:
                hexdigits = set('0123456789abcdefABCDEF')
                return len(s) % 2 == 0 and all(c in hexdigits for c in s)

            def _parse_tlv_commands(hex_payload: str):
                try:
                    data = bytes.fromhex(hex_payload)
                except Exception:
                    return None
                i = 0
                cmds = []
                while i + 3 <= len(data):
                    ch = data[i]
                    cmd = data[i+1]
                    vlen = data[i+2]
                    i += 3
                    if i + vlen > len(data):
                        print(f"DEBUG: TLV truncated: needed {vlen} bytes, remaining {len(data)-i}")
                        return None
                    value_bytes = data[i:i+vlen]
                    i += vlen
                    cmds.append((ch, cmd, value_bytes))
                if i != len(data):
                    print(f"DEBUG: TLV leftover bytes: {len(data)-i}")
                    return None
                return cmds

            def _to_int_be(b: bytes) -> int:
                if not b:
                    return 0
                return int.from_bytes(b, byteorder='big', signed=False)

            def _apply_command_tlv(ch: int, cmd: int, value_bytes: bytes):
                channel = f"{ch:02d}"
                command = f"{cmd:02d}"
                val_int = _to_int_be(value_bytes)
                # Map TLV channel/command to existing update logic
                if channel == '10' and command == '90':
                    self.update_config('area_threshold', val_int * 10)
                    print(f'Area threshold updated to: {val_int * 10}%')
                elif channel == '11' and command == '91':
                    self.update_config('stage_threshold', float(val_int))
                    print(f'Stage threshold updated to: {float(val_int)} cm')
                elif channel == '12' and command == '92':
                    self.update_config('monitoring_frequency', val_int)
                    print(f'Monitoring frequency updated to: {val_int} minutes')
                elif channel == '13' and command == '93':
                    self.update_config('emergency_frequency', val_int)
                    print(f'Emergency frequency updated to: {val_int} minutes')
                elif channel == '14' and command == '94':
                    self.update_config('photo_interval', val_int)
                    print(f'Photo interval updated to: {val_int} minutes')
                elif channel == '15' and command == '95':
                    self.update_config('neighborhood_emergency_frequency', val_int)
                    print(f'Neighborhood emergency frequency updated to: {val_int} minutes')
                elif channel == '22' and command == '00':
                    self.update_config('debug_mode', bool(val_int))
                    print(f'Debug mode {"enabled" if bool(val_int) else "disabled"}')
                elif channel == '31' and command == '00':
                    cl = max(1, min(10, val_int))
                    self.update_config('compression_level', cl)
                    print(f'Compression level updated to: {cl}')
                elif channel == '32' and command == '00':
                    self.update_config('max_retransmissions', val_int)
                    print(f'Max retransmissions updated to: {val_int}')
                elif channel == '40' and command == '00':
                    self.update_config('auto_shutdown_enabled', bool(val_int))
                    print(f'Auto shutdown {"enabled" if bool(val_int) else "disabled"}')
                elif channel == '41' and command == '00':
                    self.update_config('shutdown_iteration_limit', val_int)
                    print(f'Shutdown iteration limit updated to: {val_int}')
                elif channel == '42' and command == '00':
                    self.update_config('data_retention_days', val_int)
                    print(f'Data retention updated to: {val_int} days')
                elif channel == '43' and command == '00':
                    self.update_config('backup_enabled', bool(val_int))
                    print(f'Backup {"enabled" if bool(val_int) else "disabled"}')
                elif channel == '21' and command == '00':
                    self.update_config('emergency_mode', True)
                    print('🚨 Emergency mode activated!')
                elif channel == '99' and command == '00':
                    self.update_config('emergency_mode', False)
                    print('✅ Emergency mode deactivated')
                else:
                    print(f'Unknown TLV channel/command: {channel}/{command} value={val_int}')

            # Try TLV first if it looks like hex
            if _is_hex_string(payload):
                tlv_cmds = _parse_tlv_commands(payload)
                if tlv_cmds is not None and len(tlv_cmds) > 0:
                    print(f"DEBUG: Parsed {len(tlv_cmds)} TLV command(s)")
                    for (ch, cmd, vbytes) in tlv_cmds:
                        _apply_command_tlv(ch, cmd, vbytes)
                    return

            # Handle new format: [Channel][Command][Value] (single command, digits)
            if len(payload) >= 4:
                # Clean the payload to remove any whitespace or special characters
                clean_payload = payload.strip()
                print(f"DEBUG: Cleaned payload: '{clean_payload}' (length: {len(clean_payload)})")
                
                if len(clean_payload) >= 4:
                    # Parse the format: [Type][Command][Value]
                    # Type: 2 digits (10, 11, 12, etc.)
                    # Command: 2 digits (90, 01, etc.)
                    # Value: remaining digits (0, 1, 10, etc.)
                    channel = clean_payload[:2]
                    command = clean_payload[2:4]  # Two digit command
                    value = clean_payload[4:]     # Remaining digits as value
                    
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
                            
                            
                    elif channel == '22' and command == '0':
                        # Debug mode
                        try:
                            val = bool(int(value))
                            self.update_config('debug_mode', val)
                            print(f'Debug mode {"enabled" if val else "disabled"}')
                        except ValueError:
                            print(f'Invalid debug mode value: {value}')
                            
                            
                            
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
                        
                    elif channel == '50' and command == '01':
                        # Debug status command - request comprehensive system information
                        print('🔍 Debug status requested via LoRa command')
                        try:
                            from tools.lora_debug_integration import handle_debug_status_request
                            debug_response = handle_debug_status_request()
                            
                            if debug_response['status'] == 'success':
                                # Queue the debug response for transmission
                                debug_data = debug_response['data']
                                print(f"📤 Queuing debug response: {len(debug_data)} bytes")
                                
                                # Transmit the debug response
                                self.queue_binary_transmit(debug_data)
                                print('✅ Debug status response queued for transmission')
                            else:
                                print(f"❌ Debug status failed: {debug_response.get('error', 'Unknown error')}")
                                
                        except ImportError as e:
                            print(f"⚠️ Debug status module not available: {e}")
                        except Exception as e:
                            print(f"⚠️ Failed to process debug status request: {e}")
                        
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
    
    def _is_at_response(self, message: str) -> bool:
        """Check if a message is an AT command response that should be ignored"""
        # IMPORTANT: Check for LoRa data patterns FIRST to avoid conflicts
        if self._is_lora_data(message):
            return False
        
        # Check for transmission-related responses that need to be processed
        if self._is_transmission_response(message):
            return False
        
        # Check for TXS responses that need to be processed
        if self._is_txs_response(message):
            return False
        
        # Common AT command response patterns that should be ignored
        at_patterns = [
            '+NJS:',                 # Network join status
            '+DFORMAT:',             # Data format response
            '+MSG:',                 # Message status response
            'AT+',                   # AT command echo
            'AT',                    # AT command echo
        ]
        
        message_upper = message.upper()
        for pattern in at_patterns:
            if pattern in message_upper:
                return True
        
        # Check for numeric responses to AT commands (like size limits)
        # Only consider very short numeric responses as AT responses
        if message.isdigit() and len(message) <= 2:
            return True
            
        # Check for extended AT responses that start with + but are not LoRa data
        # This is a fallback for any other + patterns we haven't explicitly handled
        if message.startswith('+'):
            return True
        
        # If none of the above patterns match, classify as AT response (fallback)
        # This ensures all unclassified messages are treated as AT responses
        return True
    
    def _is_lora_data(self, message: str) -> bool:
        """Check if a message contains actual LoRa data that should be decoded"""
        # Look for LoRa data indicators
        lora_patterns = [
            'DATA=',                 # Explicit data prefix
            'RX:',                   # Receive indicator
            'RX ',                   # Receive indicator (space)
        ]
        
        message_upper = message.upper()
        for pattern in lora_patterns:
            if pattern in message_upper:
                return True
        
        # Check if it's a raw numeric command (4+ digits in [Channel][Command][Value] format)
        # Must be exactly 4 digits for the new format, or longer for legacy
        if message.isdigit():
            if len(message) == 4:  # New format: [Channel][Command][Value]
                return True
            elif len(message) > 4:  # Legacy format or extended commands
                return True
            
        return False
    
    def _extract_lora_data(self, message: str) -> str:
        """Extract LoRa data from various message formats"""
        try:
            # Handle DATA= format
            if "DATA=" in message:
                data = message.split("DATA=", 1)[1]
                return data.strip()
            
            # Handle RX: format
            elif "RX:" in message:
                rx_data = message.split("RX:", 1)[1].strip()
                return rx_data
            
            # Handle RX format (space separated)
            elif message.startswith("RX "):
                rx_data = message[3:].strip()
                return rx_data
            
            # Handle raw numeric commands
            elif message.isdigit() and len(message) >= 4:
                return message.strip()
            
            # Handle other potential formats
            else:
                # Try to extract any numeric data that might be a command
                import re
                numeric_match = re.search(r'(\d{4,})', message)
                if numeric_match:
                    return numeric_match.group(1)
                
                return None
                
        except Exception as e:
            print(f"Error extracting LoRa data from '{message}': {e}")
            return None
    
    def _is_emergency_message(self, message: str) -> bool:
        """Check if a message is an emergency message from the mDot"""
        message_upper = message.upper()
        # Any message containing "EMERGENCY" triggers emergency mode
        return 'EMERGENCY' in message_upper
    
    def _extract_emergency_status(self, message: str) -> dict:
        """Extract emergency status information from an EMERGENCY message"""
        try:
            # Only return emergency status for messages containing "EMERGENCY"
            if 'EMERGENCY' in message.upper():
                return {
                    'emergency_triggered': True,  # Always True for any EMERGENCY message
                    'message': message,
                    'timestamp': time.time()
                }
            else:
                return None
        except Exception as e:
            print(f"Error extracting emergency status from '{message}': {e}")
            return None
    
    def _is_emergency_clear_message(self, message: str) -> bool:
        """Check if a message is an emergency clear message that should turn off emergency mode"""
        # Only the specific message '9999' turns off emergency mode
        return message == '9999'
    
    def _is_txs_response(self, message: str) -> bool:
        """Check if a message is a +TXS: response containing size limit information"""
        # Check for the actual mDot response format: "AT+TXS" (echo) or "242" (size limit)
        return ("AT+TXS" in message or 
                (message.isdigit() and len(message) <= 3))  # Size limits are typically 1-3 digits
    
    def _is_valid_hex_string(self, hex_string: str) -> bool:
        """Validate that a string contains only valid hexadecimal characters"""
        if not isinstance(hex_string, str):
            return False
        
        # Check if string is empty or has odd length
        if not hex_string or len(hex_string) % 2 != 0:
            return False
        
        # Check if all characters are valid hex digits
        hex_digits = set('0123456789abcdefABCDEF')
        return all(c in hex_digits for c in hex_string)
    
    def _clear_mdot_input(self):
        """Clear mDot input buffer and send blank AT command to reset state"""
        try:
            print("DEBUG: Clearing mDot input buffer...")
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            
            # Send blank AT command to clear any partial state
            print("DEBUG: Sending blank AT command to clear mDot state...")
            self.ser.write('AT\r\n'.encode())
            time.sleep(0.5)
            
            # Read any response from the blank AT command
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        print(f"DEBUG: Blank AT response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        print(f"DEBUG: Blank AT binary response: {response_hex}")
            
            print("DEBUG: mDot input cleared successfully")
            return True
        except Exception as e:
            print(f"ERROR: Failed to clear mDot input: {e}")
            return False
    
    def _attempt_transmission(self, content: bytes, size_limit: int) -> tuple[bool, str]:
        """Attempt a single transmission and return (success, error_message)"""
        try:
            # Calculate actual payload size and construct AT command
            if isinstance(content, str):
                # Validate hex string format before sending
                if not self._is_valid_hex_string(content):
                    return False, f"Invalid hex string format: {content}"
                
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
                    error_message = ""
                    
                    while self.ser.in_waiting > 0:
                        res = self.ser.read_until()
                        if res:
                            try:
                                response = res.decode('utf-8').strip()
                                final_responses.append(response)
                                print(f"DEBUG: Final response: '{response}'")
                                
                                # Check for OK response and specific error conditions
                                if 'OK' in response:
                                    success = True
                                    print("DEBUG: mDot confirmed command execution with OK")
                                elif 'INVALID COMMAND' in response.upper():
                                    error_message = f"mDot reported invalid command: {response}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Command was: {content}")
                                elif 'INVALID HEX' in response.upper() or 'INVALID STRING' in response.upper():
                                    error_message = f"mDot reported invalid hex string: {response}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Hex string was: {content}")
                                elif 'ERROR' in response.upper():
                                    error_message = f"mDot reported error: {response}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Command was: {content}")
                            except UnicodeDecodeError:
                                # Handle binary responses that can't be decoded as UTF-8
                                response_hex = res.hex()
                                final_responses.append(f"BINARY:{response_hex}")
                                print(f"DEBUG: Binary response (hex): {response_hex}")
                                
                                # Check if this might be an OK response in binary form
                                if b'OK' in res:
                                    success = True
                                    print("DEBUG: mDot confirmed command execution with OK (binary)")
                    
                    # Check if we already have a successful transmission status from the listening loop
                    if not success and self.last_transmission_status and self.last_transmission_status.get('success', False):
                        # Only use the status if it's recent (within last 5 seconds)
                        status_time = self.last_transmission_status.get('timestamp', 0)
                        if time.time() - status_time < 5:
                            print("DEBUG: Using successful transmission status from listening loop")
                            success = True
                        else:
                            print("DEBUG: Transmission status too old, ignoring")
                    
                    if success:
                        print("Data sent to mDot successfully!")
                        return True, ""
                    else:
                        if not error_message:
                            error_message = f"mDot did not respond with OK - responses: {final_responses}"
                        print(f"ERROR: {error_message}")
                        return False, error_message
                else:
                    error_message = f"Payload size {payload_size} bytes exceeds limit {size_limit} bytes"
                    print(f'DEBUG: {error_message}')
                    print('Contents larger than current lora transmission payload')
                    return False, error_message
            else:
                # For bytes, use actual length
                payload_size = len(content)
                print(f"DEBUG: Bytes length: {payload_size} bytes")
                
                if payload_size <= size_limit:
                    # Convert bytes to hex string and validate
                    hex_content = content.hex()
                    if not self._is_valid_hex_string(hex_content):
                        return False, f"Generated invalid hex string from bytes: {hex_content}"
                    
                    # Write the data to the serial port
                    print(f"DEBUG: Sending data payload...")
                    self.ser.write(f'AT+SENDB={hex_content}\r\n'.encode())
                    print("DEBUG: Data payload sent, waiting for response...")
                    
                    # extended wait for response
                    time.sleep(10)
                    
                    # Read response lines and verify success
                    final_responses = []
                    success = False
                    error_message = ""
                    
                    while self.ser.in_waiting > 0:
                        res = self.ser.read_until()
                        if res:
                            try:
                                response = res.decode('utf-8').strip()
                                final_responses.append(response)
                                print(f"DEBUG: Final response: '{response}'")
                                
                                # Check for OK response and specific error conditions
                                if 'OK' in response:
                                    success = True
                                    print("DEBUG: mDot confirmed command execution with OK")
                                elif 'INVALID COMMAND' in response.upper():
                                    error_message = f"mDot reported invalid command: {response}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Command was: {content}")
                                elif 'INVALID HEX' in response.upper() or 'INVALID STRING' in response.upper():
                                    error_message = f"mDot reported invalid hex string: {response}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Hex string was: {content}")
                                elif 'ERROR' in response.upper():
                                    error_message = f"mDot reported error: {response}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Command was: {content}")
                            except UnicodeDecodeError:
                                # Handle binary responses that can't be decoded as UTF-8
                                response_hex = res.hex()
                                final_responses.append(f"BINARY:{response_hex}")
                                print(f"DEBUG: Binary response (hex): {response_hex}")
                                
                                # Check if this might be an OK response in binary form
                                if b'OK' in res:
                                    success = True
                                    print("DEBUG: mDot confirmed command execution with OK (binary)")
                                # Check for error responses in binary form
                                elif b'ERROR' in res or b'INVALID' in res:
                                    error_message = f"mDot reported error in binary response: {response_hex}"
                                    print(f"ERROR: {error_message}")
                                    print(f"ERROR: Command was: {content}")
                                # For binary data, check if mDot echoed back the same data (indicates success)
                                elif res == content:
                                    success = True
                                    print("DEBUG: mDot echoed back transmitted data - transmission successful")
                    
                    # Check if we already have a successful transmission status from the listening loop
                    if not success and self.last_transmission_status and self.last_transmission_status.get('success', False):
                        # Only use the status if it's recent (within last 5 seconds)
                        status_time = self.last_transmission_status.get('timestamp', 0)
                        if time.time() - status_time < 5:
                            print("DEBUG: Using successful transmission status from listening loop")
                            success = True
                        else:
                            print("DEBUG: Transmission status too old, ignoring")
                    
                    if success:
                        print("Data sent to mDot successfully!")
                        return True, ""
                    else:
                        if not error_message:
                            error_message = f"mDot did not respond with OK - responses: {final_responses}"
                        print(f"ERROR: {error_message}")
                        return False, error_message
                else:
                    error_message = f"Payload size {payload_size} bytes exceeds limit {size_limit} bytes"
                    print(f'DEBUG: {error_message}')
                    print('Contents larger than current lora transmission payload')
                    return False, error_message
                    
        except Exception as e:
            error_message = f"Exception during transmission: {e}"
            print(f"Error sending to mDot: {e}")
            import traceback
            traceback.print_exc()
            return False, error_message
    
    def _is_transmission_response(self, message: str) -> bool:
        """Check if a message is a transmission-related AT response that needs processing"""
        # Look for transmission-related response patterns
        transmission_patterns = [
            'SENDB:',                # AT+SENDB response
            'SEND:',                 # AT+SEND response
            'TX:',                   # Transmission status
            'TRANSMIT:',             # Transmission status
            'PACKET:',               # Packet information
            'PAYLOAD:',              # Payload information
        ]
        
        message_upper = message.upper()
        for pattern in transmission_patterns:
            if pattern in message_upper:
                return True
        
        # Check for transmission confirmation messages
        if any(keyword in message_upper for keyword in ['TRANSMITTED', 'SENT', 'DELIVERED', 'ACK']):
            return True
        
        # Check for standard AT command responses that indicate success/failure
        if any(keyword in message_upper for keyword in ['OK', 'ERROR']):
            return True
            
        return False
    
    def _extract_txs_size_limit(self, message: str) -> int:
        """Extract size limit from +TXS: response"""
        try:
            # Handle the actual mDot response format: "AT+TXS" (echo) or "242" (size limit)
            
            # If it's the command echo, no size limit here
            if "AT+TXS" in message:
                return None
            
            # If it's a numeric message, it might be the size limit
            if message.isdigit() and len(message) <= 3:
                size_limit = int(message)
                # Validate that it's a reasonable size limit (LoRaWAN typically 1-255 bytes)
                if 1 <= size_limit <= 255:
                    return size_limit
            
            # Legacy support for +TXS: format (in case it's used elsewhere)
            if "+TXS:" in message.upper():
                # Extract the numeric value after +TXS:
                size_part = message.split(":", 1)[1].strip()
                # Remove any non-numeric characters and convert to int
                size_str = ''.join(filter(str.isdigit, size_part))
                if size_str:
                    return int(size_str)
            
            return None
        except Exception as e:
            print(f"Error extracting TXS size limit from '{message}': {e}")
            return None
    
    def _extract_transmission_status(self, message: str) -> dict:
        """Extract transmission status information from AT response"""
        try:
            status = {
                'success': False,
                'message': message,
                'details': {},
                'timestamp': time.time()
            }
            
            message_upper = message.upper()
            
            # Check for success indicators
            if 'OK' in message_upper:
                status['success'] = True
                status['details']['confirmation'] = 'OK'
                status['details']['type'] = 'success_response'
            
            # Check for error indicators
            if 'ERROR' in message_upper:
                status['success'] = False
                status['details']['error'] = 'ERROR'
                status['details']['type'] = 'error_response'
            
            # Extract transmission-specific information
            if 'SENDB:' in message_upper:
                # Extract data after SENDB:
                data_part = message.split("SENDB:", 1)[1].strip()
                status['details']['command'] = 'SENDB'
                status['details']['data'] = data_part
                status['details']['type'] = 'command_response'
            
            if 'SEND:' in message_upper:
                # Extract data after SEND:
                data_part = message.split("SEND:", 1)[1].strip()
                status['details']['command'] = 'SEND'
                status['details']['data'] = data_part
                status['details']['type'] = 'command_response'
            
            # Check for transmission confirmation keywords
            for keyword in ['TRANSMITTED', 'SENT', 'DELIVERED', 'ACK']:
                if keyword in message_upper:
                    status['success'] = True
                    status['details']['confirmation'] = keyword
                    status['details']['type'] = 'status_response'
            
            # If no specific type was identified, classify based on content
            if 'type' not in status['details']:
                if 'OK' in message_upper or 'ERROR' in message_upper:
                    status['details']['type'] = 'standard_response'
                else:
                    status['details']['type'] = 'unknown_response'
            
            return status
            
        except Exception as e:
            print(f"Error extracting transmission status from '{message}': {e}")
            return {
                'success': False,
                'message': message,
                'error': str(e),
                'timestamp': time.time()
            }
    
    def get_size_limit(self) -> int:
        """Get the current payload size limit"""
        return self.current_size_limit
    
    def get_last_transmission_status(self) -> dict:
        """Get the last transmission status"""
        return self.last_transmission_status
    
    def get_transmission_history(self) -> list:
        """Get the transmission history"""
        return self.transmission_history.copy()
    

    
    def refresh_size_limit(self) -> bool:
        """Manually refresh the size limit by sending AT+TXS command"""
        try:
            print("DEBUG: Manually refreshing size limit...")
            # Temporarily disable listening to avoid interference
            was_listening = self.listening
            if was_listening:
                self.stop_listening()
                time.sleep(0.5)
            
            # Clear buffers and send AT+TXS command
            self.ser.reset_input_buffer()
            self.ser.write('AT+TXS\r\n'.encode())
            time.sleep(1)
            
            # Read response directly to get immediate size limit
            responses = []
            while self.ser.in_waiting > 0:
                res = self.ser.read_until()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: Manual TXS response: '{response}'")
                        
                        # Check if this is a +TXS: response
                        if "+TXS:" in response.upper():
                            size_limit = self._extract_txs_size_limit(response)
                            if size_limit is not None:
                                self.current_size_limit = size_limit
                                print(f"DEBUG: Size limit refreshed to: {size_limit} bytes")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        print(f"DEBUG: Binary manual TXS response (hex): {response_hex}")
            
            # Re-enable listening if it was active
            if was_listening:
                self.start_listening()
            
            return True
        except Exception as e:
            print(f"Error refreshing size limit: {e}")
            # Re-enable listening on error
            if was_listening:
                self.start_listening()
            return False

    def _is_valid_message(self, message: str) -> bool:
        """Check if a message is valid and complete before processing"""
        try:
            # Skip empty or whitespace-only messages
            if not message or message.isspace():
                return False
            
            # Skip messages that are too short (likely incomplete)
            if len(message) < 2:
                return False
            
            # Skip messages that are too long (likely corrupted)
            # Exception: Allow longer messages for debug status (AT+SENDB=...)
            if len(message) > 100 and not message.startswith('AT+SENDB='):
                return False
            
            # Skip messages with excessive special characters (likely corrupted)
            special_char_count = sum(1 for c in message if not c.isalnum() and not c.isspace() and c not in '=:+-_.')
            if special_char_count > len(message) * 0.3:  # More than 30% special chars
                return False
            
            # Skip messages that look like corrupted data (based on actual logs)
            corrupted_patterns = [
                'irgncmatitw', 'ereca', 'megny_cki', 'gaeay', 
                'nput pin hih nton takn', 'sent t gaeay', 'megny_cki=u=0',
                'yo', 'irgncmatitw:ereca:n0,ot'
            ]
            message_lower = message.lower()
            if any(pattern in message_lower for pattern in corrupted_patterns):
                return False
            
            # Skip messages that are mostly non-printable characters
            printable_ratio = sum(1 for c in message if c.isprintable()) / len(message)
            if printable_ratio < 0.7:  # Less than 70% printable
                return False
            
            # Skip messages that look like partial/corrupted emergency messages
            if 'emergency:' in message_lower and len(message) < 20:
                return False
            
            return True
            
        except Exception as e:
            print(f"Error validating message '{message}': {e}")
            return False
    
    def _check_serial_connection(self) -> bool:
        """Check if the serial port is still connected and functional"""
        try:
            if not self.ser or not self.ser.is_open:
                return False
            
            # Try to get port status
            self.ser.get_settings()
            return True
        except Exception as e:
            print(f"Serial port connection check failed: {e}")
            return False
    
    def _attempt_reconnection(self) -> bool:
        """Attempt to reconnect to the serial port"""
        try:
            print("🔄 Attempting to reconnect to serial port...")
            
            # Close existing connection if any
            if self.ser and self.ser.is_open:
                self.ser.close()
            
            # Wait a moment before reconnecting
            time.sleep(2)
            
            # Try to reconnect
            self.ser = serial.Serial(
                port=self.port,
                baudrate=115200,
                timeout=1,
                write_timeout=1
            )
            
            if self.ser.is_open:
                print("✅ Serial port reconnection successful")
                return True
            else:
                print("❌ Serial port reconnection failed")
                return False
                
        except Exception as e:
            print(f"Serial port reconnection error: {e}")
            return False

# Global instance for easy access from other modules
_lora_handler = None

def get_lora_handler() -> LoRaHandler:
    """Get the global LoRa handler instance"""
    global _lora_handler
    if _lora_handler is None:
        try:
            print(f"🔧 Creating new LoRaHandler instance...")
            _lora_handler = LoRaHandler()
            print(f"🔧 LoRaHandler created: {type(_lora_handler)}")
            print(f"🔧 LoRaHandler class: {_lora_handler.__class__}")
            print(f"🔧 LoRaHandler module: {_lora_handler.__class__.__module__}")
            print(f"🔧 LoRaHandler methods: {[method for method in dir(_lora_handler) if not method.startswith('_')]}")
            print(f"🔧 LoRaHandler has set_runtime_callback: {hasattr(_lora_handler, 'set_runtime_callback')}")
            print(f"🔧 LoRaHandler has current_size_limit: {hasattr(_lora_handler, 'current_size_limit')}")
            print(f"🔧 LoRaHandler has _is_emergency_message: {hasattr(_lora_handler, '_is_emergency_message')}")
            _lora_handler.start_listening()
            print("✅ LoRa handler initialized successfully")
        except Exception as e:
            print(f"❌ Failed to initialize LoRa handler: {e}")
            print("⚠️ LoRa functionality will not be available")
            # Don't create a mock handler - let the error propagate
            raise RuntimeError(f"LoRa handler initialization failed: {e}")
    else:
        print(f"🔧 Returning existing LoRaHandler: {type(_lora_handler)}")
        print(f"🔧 LoRaHandler methods: {[method for method in dir(_lora_handler) if not method.startswith('_')]}")
        print(f"🔧 LoRaHandler has set_runtime_callback: {hasattr(_lora_handler, 'set_runtime_callback')}")
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

def get_size_limit() -> int:
    """Convenience function to get the current payload size limit"""
    handler = get_lora_handler()
    return handler.get_size_limit()

def refresh_size_limit() -> bool:
    """Convenience function to manually refresh the size limit"""
    handler = get_lora_handler()
    return handler.refresh_size_limit()

def get_last_transmission_status() -> dict:
    """Convenience function to get the last transmission status"""
    handler = get_lora_handler()
    return handler.get_last_transmission_status()

def get_transmission_history() -> list:
    """Convenience function to get the transmission history"""
    handler = get_lora_handler()
    return handler.get_transmission_history()



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
    "relative_humidity": 55,
    "wittypi_temperature": 25.3,
    "wittypi_battery_voltage": 3.8,
    "wittypi_internal_voltage": 5.1
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

def test_error_handling():
    """Test error handling with invalid commands and hex strings"""
    print("Testing error handling...")
    
    # Test invalid hex strings
    invalid_hex_strings = [
        "GG",           # Invalid hex characters
        "123G",         # Mixed valid/invalid
        "123",          # Odd length
        "",             # Empty string
        "12 34",        # Contains spaces
        "12-34",        # Contains hyphens
    ]
    
    for invalid_hex in invalid_hex_strings:
        print(f"Testing invalid hex string: '{invalid_hex}'")
        if not LoRaHandler._is_valid_hex_string(None, invalid_hex):
            print(f"  ✓ Correctly rejected: '{invalid_hex}'")
        else:
            print(f"  ERROR: Should have been rejected: '{invalid_hex}'")
    
    # Test valid hex strings
    valid_hex_strings = [
        "1234",
        "ABCD",
        "abcd",
        "1234ABCD",
        "00FF",
    ]
    
    for valid_hex in valid_hex_strings:
        print(f"Testing valid hex string: '{valid_hex}'")
        if LoRaHandler._is_valid_hex_string(None, valid_hex):
            print(f"  ✓ Correctly accepted: '{valid_hex}'")
        else:
            print(f"  ERROR: Should have been accepted: '{valid_hex}'")
    
    print("Error handling test completed.")

def test_error_recovery():
    """Test error recovery mechanism with retry logic"""
    print("Testing error recovery mechanism...")
    
    # Create a handler instance for testing
    handler = LoRaHandler()
    
    try:
        # Test with valid data (should succeed on first attempt)
        print("\n1. Testing with valid data:")
        valid_data = b'\x01\x02\x03\x04'
        result = handler.transmit(valid_data, max_retries=2)
        print(f"   Result: {'SUCCESS' if result else 'FAILED'}")
        
        # Test with invalid hex string (should fail validation before transmission)
        print("\n2. Testing with invalid hex string:")
        invalid_hex = "GG"  # Invalid hex characters
        result = handler.transmit(invalid_hex, max_retries=2)
        print(f"   Result: {'SUCCESS' if result else 'FAILED'}")
        
        # Test retry mechanism (simulate by using a handler that might fail)
        print("\n3. Testing retry mechanism:")
        print("   (This would normally test actual mDot communication)")
        print("   In a real scenario, errors would trigger retries with input clearing")
        
    finally:
        handler.close()
    
    print("Error recovery test completed.")

def test_transmission_status_integration():
    """Test that transmission status from listening loop is properly used"""
    print("Testing transmission status integration...")
    
    # Create a handler instance for testing
    handler = LoRaHandler()
    
    try:
        # Simulate a successful transmission status from the listening loop
        handler.last_transmission_status = {
            'success': True,
            'message': 'OK',
            'details': {'confirmation': 'OK', 'type': 'success_response'},
            'timestamp': time.time()
        }
        
        print("1. Testing with simulated successful transmission status:")
        print(f"   Last transmission status: {handler.last_transmission_status}")
        
        # Test that the status is used correctly
        if handler.last_transmission_status and handler.last_transmission_status.get('success', False):
            status_time = handler.last_transmission_status.get('timestamp', 0)
            if time.time() - status_time < 5:
                print("   ✓ Recent successful status detected")
            else:
                print("   ✗ Status too old")
        else:
            print("   ✗ No successful status found")
        
        # Test with old status
        print("\n2. Testing with old transmission status:")
        handler.last_transmission_status['timestamp'] = time.time() - 10  # 10 seconds ago
        print(f"   Last transmission status: {handler.last_transmission_status}")
        
        if handler.last_transmission_status and handler.last_transmission_status.get('success', False):
            status_time = handler.last_transmission_status.get('timestamp', 0)
            if time.time() - status_time < 5:
                print("   ✓ Recent successful status detected")
            else:
                print("   ✓ Old status correctly ignored")
        else:
            print("   ✗ No successful status found")
        
    finally:
        handler.close()
    
    print("Transmission status integration test completed.")

if __name__ == "__main__":
    import sys
    from sys import argv
    
    # Run error handling tests
    test_error_handling()
    print("\n" + "="*50 + "\n")
    
    test_error_recovery()
    print("\n" + "="*50 + "\n")
    
    test_transmission_status_integration()
    print("\n" + "="*50 + "\n")
    
    # Run main handler
    handler = LoRaHandler()
    try:
        if len(argv) == 1:
            # Test with sample binary data
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

