#!/usr/bin/env python3
"""
Test script for the debug status command functionality.

This script demonstrates how to use the debug status command
and shows the comprehensive system information it provides.
"""

import json
import sys
from datetime import datetime

import pytest


class TestGetLoraStatusSharesSingleton:
    """Regression: get_lora_status() previously imported lora_handler_concurrent
    and lora_runtime_integration bare (no "tools." prefix), which resolves to
    a second, independent copy of each module with its own singleton/lock —
    self-conflicting with the real one over the serial-port flock whenever a
    debug-status request (LoRa channel 50/01) ran alongside the live
    ticktalk_main.py process. Confirmed on UFO010 in production: the debug
    handler's LoRaHandler() construction stole the flock, and every
    subsequent transmit cycle reported "LoRa serial port already owned by
    another process" for the rest of that boot.
    """

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        import tools.lora_handler_concurrent as lhc
        lhc._lora_handler = None
        yield
        lhc._lora_handler = None

    def test_reuses_existing_handler_instead_of_conflicting(self):
        pytest.importorskip("psutil")
        import tools.lora_handler_concurrent as lhc
        from tools.debug_status_command import get_lora_status

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(lhc.LoRaHandler, "refresh_size_limit", lambda self: True)
            mp.setattr(lhc.LoRaHandler, "start_listening", lambda self: None)
            handler = lhc.get_lora_handler()
        assert handler is not None

        status = get_lora_status()

        assert status["status"] == "lora_available"
        assert status["lora_handler_available"] is True
        # The real regression: a second module copy would construct its own
        # LoRaHandler, hit the already-held flock, and report unavailable.
        assert lhc._lora_handler is handler

def test_debug_status_command():
    """Test the debug status command functionality."""
    print("🧪 Testing Debug Status Command")
    print("=" * 50)
    
    try:
        from debug_status_command import handle_debug_status_command, generate_debug_status
        
        # Test 1: Generate debug status
        print("\n📊 Test 1: Generating debug status...")
        debug_status = generate_debug_status()
        
        print(f"✅ Debug status generated successfully")
        print(f"📅 Timestamp: {debug_status['timestamp']}")
        print(f"🖥️  Hostname: {debug_status['system_info'].get('hostname', 'unknown')}")
        print(f"⏰ Uptime: {debug_status['uptime']['uptime_formatted']}")
        print(f"🌡️  CPU Temperature: {debug_status['cpu_temperature']}°C")
        print(f"💻 CPU Usage: {debug_status['cpu_info'].get('cpu_percent', 0)}%")
        print(f"🧠 Memory Usage: {debug_status['memory_info'].get('percent_used', 0)}%")
        print(f"💾 Disk Usage: {debug_status['disk_info'].get('percent_used', 0)}%")
        print(f"📈 Load Average (1min): {debug_status['system_load']['1min']}")
        
        # Test 2: Handle debug status command
        print("\n📊 Test 2: Handling debug status command...")
        result = handle_debug_status_command()
        
        if result['status'] == 'debug_status_generated':
            print(f"✅ Command handled successfully")
            print(f"📦 LoRa formatted size: {result['lora_size_bytes']} bytes")
            print(f"📋 LoRa formatted data: {result['lora_formatted']}")
        else:
            print(f"❌ Command failed: {result.get('error', 'Unknown error')}")
        
        # Test 3: Test LoRa integration
        print("\n📊 Test 3: Testing LoRa integration...")
        from tools.lora_debug_integration import process_debug_command, format_debug_response_for_transmission
        
        # Test debug command processing
        debug_commands = ['50011', '50010', '50012']
        for cmd in debug_commands:
            response = process_debug_command(cmd)
            if response:
                print(f"✅ Command {cmd} processed: {response['status']}")
                
                # Format for transmission
                formatted = format_debug_response_for_transmission(response)
                print(f"📤 Formatted for transmission: {formatted}")
                print(f"📏 Transmission size: {len(formatted)} bytes")
            else:
                print(f"❌ Command {cmd} not processed")
        
        # Test 4: Show detailed system information
        print("\n📊 Test 4: Detailed system information...")
        print_system_details(debug_status)
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Make sure all required modules are available")
        return False
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False

def print_system_details(debug_status):
    """Print detailed system information."""
    print("\n🔍 Detailed System Information:")
    print("-" * 40)
    
    # System info
    sys_info = debug_status['system_info']
    print(f"🖥️  System: {sys_info.get('system', 'unknown')} {sys_info.get('release', 'unknown')}")
    print(f"🏗️  Architecture: {sys_info.get('machine', 'unknown')}")
    print(f"🐍 Python: {sys_info.get('python_version', 'unknown')}")
    
    # CPU info
    cpu_info = debug_status['cpu_info']
    print(f"💻 CPU: {cpu_info.get('cpu_count', 0)} cores")
    if cpu_info.get('cpu_freq_mhz'):
        print(f"⚡ CPU Frequency: {cpu_info['cpu_freq_mhz']} MHz")
    
    # Memory info
    mem_info = debug_status['memory_info']
    print(f"🧠 Memory: {mem_info.get('used_mb', 0):.1f}MB / {mem_info.get('total_mb', 0):.1f}MB ({mem_info.get('percent_used', 0):.1f}%)")
    
    # Disk info
    disk_info = debug_status['disk_info']
    print(f"💾 Disk: {disk_info.get('used_gb', 0):.1f}GB / {disk_info.get('total_gb', 0):.1f}GB ({disk_info.get('percent_used', 0):.1f}%)")
    
    # Network info
    net_info = debug_status['network_info']
    print(f"🌐 Network: {net_info.get('bytes_sent', 0)} bytes sent, {net_info.get('bytes_recv', 0)} bytes received")
    
    # Process info
    proc_info = debug_status['process_info']
    print(f"🔄 Processes: {proc_info.get('total_processes', 0)} total")
    
    # LoRa status
    lora_status = debug_status['lora_status']
    print(f"📡 LoRa: {'Available' if lora_status.get('lora_handler_available') else 'Unavailable'}")
    
    # WittyPi status
    wittypi_status = debug_status['wittypi_status']
    if wittypi_status.get('available'):
        print(f"🔋 WittyPi: Available (Temp: {wittypi_status.get('temperature_c', 0)}°C, Battery: {wittypi_status.get('battery_voltage_v', 0)}V)")
    else:
        print(f"🔋 WittyPi: Unavailable")
    
    # Sensor status
    sensor_status = debug_status['sensor_status']
    print(f"📊 Sensors:")
    for sensor, status in sensor_status.items():
        available = status.get('available', False)
        print(f"   {sensor}: {'Available' if available else 'Unavailable'}")

def test_lora_command_format():
    """Test the LoRa command format for debug status."""
    print("\n📡 Testing LoRa Command Format")
    print("=" * 50)
    
    # Test command format
    debug_commands = [
        ('50011', 'Debug status request (any value)'),
        ('50010', 'Debug status request (zero value)'),
        ('50012', 'Debug status request (different value)')
    ]
    
    print("🔧 Debug Status Commands:")
    for cmd, description in debug_commands:
        print(f"   {cmd} - {description}")
    
    print("\n📋 Command Format: [Type][Command][Value]")
    print("   Type: 50 (Debug and Status Commands)")
    print("   Command: 01 (Request comprehensive debug status)")
    print("   Value: Any value triggers debug status")
    
    print("\n💡 Usage Examples:")
    print("   echo '50011' | send_to_lora")
    print("   python3 test_chirpstack_parameter_update.py <DEV_EUI> output.log")
    print("   # Then send: {\"type\": \"debug_status\", \"enabled\": true}")

def main():
    """Main test function."""
    print("🔍 Debug Status Command Test Suite")
    print("=" * 60)
    print(f"⏰ Test started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Test debug status command
    success = test_debug_status_command()
    
    # Test LoRa command format
    test_lora_command_format()
    
    print(f"\n{'='*60}")
    if success:
        print("🎉 All tests completed successfully!")
        print("✅ Debug status command is ready for use")
        print("\n💡 Next steps:")
        print("   1. Integrate with LoRa handler")
        print("   2. Test via ChirpStack downlink")
        print("   3. Use command '50011' to request debug status")
    else:
        print("❌ Some tests failed")
        print("   Check the error messages above")
    
    print(f"⏰ Test completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
