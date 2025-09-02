#!/usr/bin/env python3
"""
Debug Status Command for LoRa Remote Debugging

This module provides comprehensive system status information that can be
requested via LoRa downlink commands for remote debugging and monitoring.
"""

import os
import sys
import time
import psutil
import subprocess
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

def get_cpu_temperature() -> Optional[float]:
    """Get CPU temperature in Celsius."""
    try:
        # Try different temperature sources
        temp_sources = [
            '/sys/class/thermal/thermal_zone0/temp',
            '/sys/class/thermal/thermal_zone1/temp',
            '/sys/class/hwmon/hwmon0/temp1_input',
            '/sys/class/hwmon/hwmon1/temp1_input'
        ]
        
        for source in temp_sources:
            if os.path.exists(source):
                with open(source, 'r') as f:
                    temp_millicelsius = int(f.read().strip())
                    return temp_millicelsius / 1000.0
        
        # Try vcgencmd if available (Raspberry Pi)
        try:
            result = subprocess.run(['vcgencmd', 'measure_temp'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                temp_str = result.stdout.strip()
                # Extract temperature from "temp=45.6'C"
                temp_value = float(temp_str.split('=')[1].split("'")[0])
                return temp_value
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, IndexError):
            pass
            
        return None
    except Exception as e:
        print(f"⚠️ Failed to get CPU temperature: {e}")
        return None

def get_system_load() -> Dict[str, float]:
    """Get system load averages."""
    try:
        load_avg = os.getloadavg()
        return {
            '1min': load_avg[0],
            '5min': load_avg[1],
            '15min': load_avg[2]
        }
    except Exception as e:
        print(f"⚠️ Failed to get system load: {e}")
        return {'1min': 0.0, '5min': 0.0, '15min': 0.0}

def get_uptime() -> Dict[str, Any]:
    """Get system uptime information."""
    try:
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_delta = timedelta(seconds=uptime_seconds)
        
        return {
            'uptime_seconds': int(uptime_seconds),
            'uptime_formatted': str(uptime_delta),
            'boot_time': datetime.fromtimestamp(psutil.boot_time()).isoformat(),
            'current_time': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Failed to get uptime: {e}")
        return {
            'uptime_seconds': 0,
            'uptime_formatted': 'unknown',
            'boot_time': 'unknown',
            'current_time': datetime.now().isoformat()
        }

def get_memory_info() -> Dict[str, Any]:
    """Get memory usage information."""
    try:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        
        return {
            'total_mb': round(memory.total / (1024 * 1024), 2),
            'available_mb': round(memory.available / (1024 * 1024), 2),
            'used_mb': round(memory.used / (1024 * 1024), 2),
            'free_mb': round(memory.free / (1024 * 1024), 2),
            'percent_used': memory.percent,
            'swap_total_mb': round(swap.total / (1024 * 1024), 2),
            'swap_used_mb': round(swap.used / (1024 * 1024), 2),
            'swap_percent_used': swap.percent
        }
    except Exception as e:
        print(f"⚠️ Failed to get memory info: {e}")
        return {'error': str(e)}

def get_disk_info() -> Dict[str, Any]:
    """Get disk usage information."""
    try:
        disk_usage = psutil.disk_usage('/')
        return {
            'total_gb': round(disk_usage.total / (1024 * 1024 * 1024), 2),
            'used_gb': round(disk_usage.used / (1024 * 1024 * 1024), 2),
            'free_gb': round(disk_usage.free / (1024 * 1024 * 1024), 2),
            'percent_used': round((disk_usage.used / disk_usage.total) * 100, 2)
        }
    except Exception as e:
        print(f"⚠️ Failed to get disk info: {e}")
        return {'error': str(e)}

def get_cpu_info() -> Dict[str, Any]:
    """Get CPU information and usage."""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count()
        cpu_freq = psutil.cpu_freq()
        
        return {
            'cpu_percent': cpu_percent,
            'cpu_count': cpu_count,
            'cpu_freq_mhz': round(cpu_freq.current, 2) if cpu_freq else None,
            'cpu_freq_max_mhz': round(cpu_freq.max, 2) if cpu_freq else None,
            'cpu_freq_min_mhz': round(cpu_freq.min, 2) if cpu_freq else None
        }
    except Exception as e:
        print(f"⚠️ Failed to get CPU info: {e}")
        return {'error': str(e)}

def get_network_info() -> Dict[str, Any]:
    """Get network interface information."""
    try:
        net_io = psutil.net_io_counters()
        net_connections = len(psutil.net_connections())
        
        return {
            'bytes_sent': net_io.bytes_sent,
            'bytes_recv': net_io.bytes_recv,
            'packets_sent': net_io.packets_sent,
            'packets_recv': net_io.packets_recv,
            'active_connections': net_connections
        }
    except Exception as e:
        print(f"⚠️ Failed to get network info: {e}")
        return {'error': str(e)}

def get_process_info() -> Dict[str, Any]:
    """Get information about running processes."""
    try:
        processes = list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']))
        
        # Get top 5 processes by CPU usage
        top_cpu = sorted(processes, key=lambda x: x.info['cpu_percent'] or 0, reverse=True)[:5]
        top_memory = sorted(processes, key=lambda x: x.info['memory_percent'] or 0, reverse=True)[:5]
        
        return {
            'total_processes': len(processes),
            'top_cpu_processes': [
                {
                    'pid': p.info['pid'],
                    'name': p.info['name'],
                    'cpu_percent': p.info['cpu_percent']
                } for p in top_cpu
            ],
            'top_memory_processes': [
                {
                    'pid': p.info['pid'],
                    'name': p.info['name'],
                    'memory_percent': p.info['memory_percent']
                } for p in top_memory
            ]
        }
    except Exception as e:
        print(f"⚠️ Failed to get process info: {e}")
        return {'error': str(e)}

def get_system_info() -> Dict[str, Any]:
    """Get basic system information."""
    try:
        return {
            'hostname': os.uname().nodename,
            'system': os.uname().sysname,
            'release': os.uname().release,
            'version': os.uname().version,
            'machine': os.uname().machine,
            'python_version': sys.version.split()[0],
            'platform': sys.platform
        }
    except Exception as e:
        print(f"⚠️ Failed to get system info: {e}")
        return {'error': str(e)}

def get_lora_status() -> Dict[str, Any]:
    """Get LoRa and application specific status."""
    try:
        # Try to get LoRa handler status
        from lora_handler_concurrent import get_lora_handler
        handler = get_lora_handler()
        
        # Try to get runtime parameters
        from lora_runtime_integration import get_parameter
        emergency_mode = get_parameter('emergency_mode', False)
        transmission_enabled = get_parameter('transmission_enabled', True)
        
        # In emergency mode, transmission should ALWAYS be enabled
        if emergency_mode:
            transmission_enabled = True
        
        runtime_params = {
            'emergency_mode': emergency_mode,
            'transmission_enabled': transmission_enabled,
            'debug_mode': get_parameter('debug_mode', False),
            'monitoring_frequency': get_parameter('monitoring_frequency', 60),
            'area_threshold': get_parameter('area_threshold', 10)
        }
        
        return {
            'lora_handler_available': handler is not None,
            'runtime_parameters': runtime_params,
            'status': 'lora_available'
        }
    except Exception as e:
        return {
            'lora_handler_available': False,
            'runtime_parameters': {},
            'status': 'lora_unavailable',
            'error': str(e)
        }

def get_wittypi_status() -> Dict[str, Any]:
    """Get WittyPi specific status."""
    try:
        # First check if WittyPi hardware is present via I2C
        wittypi_present = check_wittypi_i2c_presence()
        
        if not wittypi_present:
            return {
                'available': False,
                'status': 'wittypi_hardware_not_detected',
                'error': 'WittyPi not detected on I2C address 0x08'
            }
        
        # If hardware is present, try to get data
        from wittypi_control import get_data
        temperature, battery_voltage, internal_voltage = get_data()
        
        return {
            'available': True,
            'temperature_c': temperature,
            'battery_voltage_v': battery_voltage,
            'internal_voltage_v': internal_voltage,
            'status': 'wittypi_available',
            'i2c_address': '0x08'
        }
    except Exception as e:
        return {
            'available': False,
            'status': 'wittypi_unavailable',
            'error': str(e)
        }

def check_wittypi_i2c_presence() -> bool:
    """Check if WittyPi is present on I2C bus at address 0x08."""
    try:
        # Method 1: Check if i2c-tools is available and use i2cdetect
        try:
            result = subprocess.run(['i2cdetect', '-y', '1'], 
                                  capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                # Look for device at address 08 (hex) in the output
                # The output format is: "00: 08 -- -- ..." if device is present
                lines = result.stdout.split('\n')
                for line in lines:
                    if line.startswith('00:'):
                        # Check if 08 appears in the first line (addresses 00-0f)
                        if '08' in line:
                            return True
                        break
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        
        # Method 2: Try to read from I2C device directly using smbus
        try:
            import smbus
            bus = smbus.SMBus(1)  # I2C bus 1 (Raspberry Pi)
            # Try to read a byte from address 0x08
            # This will raise an exception if device is not present
            bus.read_byte(0x08)
            return True
        except (ImportError, OSError, IOError):
            pass
        
        # Method 3: Try to use i2cget to read from address 0x08
        try:
            result = subprocess.run(['i2cget', '-y', '1', '0x08'], 
                                  capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
            
        return False
        
    except Exception as e:
        print(f"⚠️ Failed to check WittyPi I2C presence: {e}")
        return False

def get_sensor_status() -> Dict[str, Any]:
    """Get status of various sensors."""
    sensor_status = {}
    
    # Check IMU sensor
    try:
        from bno055_imu import get_orientation
        imu_data = get_orientation()
        sensor_status['imu'] = {
            'available': True,
            'status': 'imu_available',
            'data_keys': list(imu_data.keys()) if imu_data else []
        }
    except Exception as e:
        sensor_status['imu'] = {
            'available': False,
            'status': 'imu_unavailable',
            'error': str(e)
        }
    
    # Check temperature sensor
    try:
        from aht20_temperature import get_aht20
        temp_data = get_aht20()
        sensor_status['temperature'] = {
            'available': True,
            'status': 'temperature_available',
            'data_keys': list(temp_data.keys()) if temp_data else []
        }
    except Exception as e:
        sensor_status['temperature'] = {
            'available': False,
            'status': 'temperature_unavailable',
            'error': str(e)
        }
    
    # Check GPS
    try:
        from get_gps import get_lat_lon_alt
        gps_data = get_lat_lon_alt()
        sensor_status['gps'] = {
            'available': gps_data is not None,
            'status': 'gps_available' if gps_data else 'gps_no_fix',
            'data_keys': list(gps_data.keys()) if gps_data else []
        }
    except Exception as e:
        sensor_status['gps'] = {
            'available': False,
            'status': 'gps_unavailable',
            'error': str(e)
        }
    
    return sensor_status

def generate_debug_status() -> Dict[str, Any]:
    """Generate comprehensive debug status information."""
    print("🔍 Generating comprehensive debug status...")
    
    debug_status = {
        'timestamp': datetime.now().isoformat(),
        'system_info': get_system_info(),
        'uptime': get_uptime(),
        'cpu_info': get_cpu_info(),
        'cpu_temperature': get_cpu_temperature(),
        'system_load': get_system_load(),
        'memory_info': get_memory_info(),
        'disk_info': get_disk_info(),
        'network_info': get_network_info(),
        'process_info': get_process_info(),
        'lora_status': get_lora_status(),
        'wittypi_status': get_wittypi_status(),
        'sensor_status': get_sensor_status()
    }
    
    print("✅ Debug status generated successfully")
    return debug_status

def format_debug_status_for_lora(debug_status: Dict[str, Any]) -> str:
    """Format debug status for LoRa transmission (compact format)."""
    try:
        # Create a very compact summary for LoRa transmission (target: <200 bytes)
        summary = {
            'ts': debug_status['timestamp'][:19],  # Remove microseconds: "2025-09-02T14:08:10"
            'host': debug_status['system_info'].get('hostname', 'unknown')[:8],  # Limit hostname length
            'up': round(debug_status['uptime']['uptime_seconds'] / 3600, 1),  # Uptime in hours
            'cpu_t': round(debug_status['cpu_temperature'] or 0, 1),  # CPU temp, 1 decimal
            'cpu_p': round(debug_status['cpu_info'].get('cpu_percent', 0), 1),  # CPU percent, 1 decimal
            'mem_p': round(debug_status['memory_info'].get('percent_used', 0), 1),  # Memory percent, 1 decimal
            'disk_p': round(debug_status['disk_info'].get('percent_used', 0), 1),  # Disk percent, 1 decimal
            'load': round(debug_status['system_load']['1min'], 2),  # Load average, 2 decimals
            'em': debug_status['lora_status']['runtime_parameters'].get('emergency_mode', False),  # Emergency mode
            'tx': debug_status['lora_status']['runtime_parameters'].get('transmission_enabled', True),  # Transmission enabled
            'lora': debug_status['lora_status']['lora_handler_available'],  # LoRa available
            'wp': debug_status['wittypi_status']['available']  # WittyPi available
        }
        
        return json.dumps(summary, separators=(',', ':'))
    except Exception as e:
        print(f"⚠️ Failed to format debug status: {e}")
        return json.dumps({'error': 'Failed to format debug status', 'timestamp': datetime.now().isoformat()})

def handle_debug_status_command() -> Dict[str, Any]:
    """Handle debug status command and return formatted response."""
    try:
        # Generate comprehensive debug status
        debug_status = generate_debug_status()
        
        # Format for LoRa transmission
        lora_formatted = format_debug_status_for_lora(debug_status)
        
        return {
            'status': 'debug_status_generated',
            'timestamp': datetime.now().isoformat(),
            'full_status': debug_status,
            'lora_formatted': lora_formatted,
            'lora_size_bytes': len(lora_formatted.encode('utf-8'))
        }
    except Exception as e:
        print(f"⚠️ Failed to handle debug status command: {e}")
        return {
            'status': 'debug_status_failed',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }

def transmit_debug_status_via_lora():
    """Transmit debug status via LoRa when run standalone."""
    try:
        # Import the LoRa handler
        import sys
        import os
        
        # Add the parent directory to the path to import lora_handler_concurrent
        parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, parent_dir)
        
        from tools.lora_handler_concurrent import get_lora_handler
        
        print("🔍 Generating debug status...")
        result = handle_debug_status_command()
        
        if result['status'] != 'debug_status_generated':
            print(f"❌ Failed to generate debug status: {result.get('error', 'Unknown error')}")
            return False
        
        print(f"📊 Debug Status Generated:")
        print(f"Status: {result['status']}")
        print(f"Timestamp: {result['timestamp']}")
        print(f"LoRa Formatted Size: {result['lora_size_bytes']} bytes")
        
        # Get the LoRa handler
        handler = get_lora_handler()
        if not handler:
            print("❌ LoRa handler not available - cannot transmit")
            return False
        
        # Format the debug data with proper LoRa channel/type headers
        debug_data = result['lora_formatted']
        debug_bytes = debug_data.encode('utf-8')
        
        # Create the proper LoRa packet format: Channel 0B (11), Type 01, Length (2 bytes), Data
        length = len(debug_bytes)
        lora_packet = bytes([0x0B, 0x01]) + length.to_bytes(2, 'big') + debug_bytes
        lora_hex = lora_packet.hex()
        
        print(f"📤 Transmitting debug status via LoRa...")
        print(f"Data: {debug_data}")
        print(f"Length: {length} bytes")
        print(f"LoRa Packet: {lora_packet.hex()}")
        print(f"Total Size: {len(lora_packet)} bytes")
        
        # Transmit the debug status with proper formatting
        handler.queue_binary_transmit(lora_hex)
        handler.process_transmit_queue()
        
        print("✅ Debug status transmitted successfully via LoRa")
        
        # Print some key information
        full_status = result['full_status']
        print(f"\n🔍 Key System Information:")
        print(f"Hostname: {full_status['system_info'].get('hostname', 'unknown')}")
        print(f"Uptime: {full_status['uptime']['uptime_formatted']}")
        print(f"CPU Temperature: {full_status['cpu_temperature']}°C")
        print(f"CPU Usage: {full_status['cpu_info'].get('cpu_percent', 0)}%")
        print(f"Memory Usage: {full_status['memory_info'].get('percent_used', 0)}%")
        print(f"Disk Usage: {full_status['disk_info'].get('percent_used', 0)}%")
        print(f"Load Average (1min): {full_status['system_load']['1min']}")
        print(f"Emergency Mode: {full_status['lora_status']['runtime_parameters'].get('emergency_mode', False)}")
        print(f"LoRa Available: {full_status['lora_status']['lora_handler_available']}")
        print(f"WittyPi Available: {full_status['wittypi_status']['available']}")
        
        return True
        
    except ImportError as e:
        print(f"❌ Failed to import LoRa handler: {e}")
        print("💡 Make sure you're running this from the correct directory and the LoRa handler is available")
        return False
    except Exception as e:
        print(f"❌ Failed to transmit debug status: {e}")
        return False

if __name__ == "__main__":
    print("🚀 Debug Status Command - Standalone Mode")
    print("=" * 50)
    
    # Check for test mode flag
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        # Test mode - just generate and display status without transmitting
        print("🧪 Testing debug status command (test mode)...")
        print("💡 Use without --test flag to transmit via LoRa")
        print()
        
        result = handle_debug_status_command()
        
        print(f"📊 Debug Status Result:")
        print(f"Status: {result['status']}")
        print(f"Timestamp: {result['timestamp']}")
        
        if result['status'] == 'debug_status_generated':
            print(f"LoRa Formatted Size: {result['lora_size_bytes']} bytes")
            print(f"LoRa Formatted: {result['lora_formatted']}")
            
            # Print some key information
            full_status = result['full_status']
            print(f"\n🔍 Key System Information:")
            print(f"Hostname: {full_status['system_info'].get('hostname', 'unknown')}")
            print(f"Uptime: {full_status['uptime']['uptime_formatted']}")
            print(f"CPU Temperature: {full_status['cpu_temperature']}°C")
            print(f"CPU Usage: {full_status['cpu_info'].get('cpu_percent', 0)}%")
            print(f"Memory Usage: {full_status['memory_info'].get('percent_used', 0)}%")
            print(f"Disk Usage: {full_status['disk_info'].get('percent_used', 0)}%")
            print(f"Load Average (1min): {full_status['system_load']['1min']}")
            print(f"Emergency Mode: {full_status['lora_status']['runtime_parameters'].get('emergency_mode', False)}")
            print(f"LoRa Available: {full_status['lora_status']['lora_handler_available']}")
            print(f"WittyPi Available: {full_status['wittypi_status']['available']}")
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
    else:
        # Default mode - transmit debug status via LoRa
        print("📡 Transmitting debug status via LoRa...")
        success = transmit_debug_status_via_lora()
        if success:
            print("\n✅ Debug status command completed successfully")
        else:
            print("\n❌ Debug status command failed")
            sys.exit(1)
