#!/usr/bin/env python3
"""
Test utilities for LoRa testing scripts
Provides consistent serial device detection and mocking across all test scripts
"""

import sys
import os

def is_serial_device_available():
    """Check if the LoRa serial device is available"""
    # Primary LoRa device path (preferred)
    primary_path = '/dev/ttyAMA5'
    
    if os.path.exists(primary_path):
        print(f"🔌 Found primary LoRa device: {primary_path}")
        return True
    
    # Fallback device paths
    fallback_paths = [
        '/dev/ttyAMA0',  # Alternative Raspberry Pi serial
        '/dev/ttyAMA1',  # Alternative Raspberry Pi serial
        '/dev/ttyAMA2',  # Alternative Raspberry Pi serial
        '/dev/ttyAMA3',  # Alternative Raspberry Pi serial
        '/dev/ttyAMA4',  # Alternative Raspberry Pi serial
        '/dev/ttyUSB0',  # USB-to-serial adapter
        '/dev/ttyACM0',  # Arduino-style device
        '/dev/ttyS0',    # Serial port
    ]
    
    for path in fallback_paths:
        if os.path.exists(path):
            print(f"🔌 Found fallback serial device: {path}")
            print(f"⚠️  Note: This is not the primary LoRa device (/dev/ttyAMA5)")
            return True
    
    print("⚠️  No suitable serial device found")
    return False

def setup_serial_for_testing():
    """Setup serial module for testing - prefer real device, mock only if necessary"""
    if is_serial_device_available():
        print("🔌 Using real serial device for testing")
        return True
    else:
        print("🔧 No serial device available, using mock mode for testing")
        # Mock the serial import to avoid serial port errors during testing
        class MockSerial:
            def __init__(self, **kwargs):
                pass
            
            def is_open(self):
                return True
            
            def close(self):
                pass
            
            def in_waiting(self):
                return 0
            
            def readline(self):
                return b''
            
            def write(self, data):
                pass
            
            def flush(self):
                pass
        
        # Mock the serial module
        sys.modules['serial'] = type('MockSerialModule', (), {
            'Serial': MockSerial,
            'PARITY_NONE': 'N',
            'STOPBITS_ONE': 1,
            'EIGHTBITS': 8
        })
        return False

def get_test_mode_info():
    """Get information about the current test mode"""
    if 'serial' in sys.modules and hasattr(sys.modules['serial'], 'Serial'):
        if 'MockSerialModule' in str(type(sys.modules['serial'])):
            return "Mock Mode - No real hardware"
        else:
            return "Real Hardware Mode - Using actual serial device"
    else:
        # Check if we have a real device available
        if is_serial_device_available():
            return "Real Hardware Mode - Serial module will be loaded when needed"
        else:
            return "Unknown Mode - Serial module not loaded"

def get_preferred_device_path():
    """Get the preferred device path for LoRa communication"""
    primary_path = '/dev/ttyAMA5'
    if os.path.exists(primary_path):
        return primary_path
    
    # Check fallback paths
    fallback_paths = [
        '/dev/ttyAMA0', '/dev/ttyAMA1', '/dev/ttyAMA2', 
        '/dev/ttyAMA3', '/dev/ttyAMA4', '/dev/ttyUSB0', 
        '/dev/ttyACM0', '/dev/ttyS0'
    ]
    
    for path in fallback_paths:
        if os.path.exists(path):
            return path
    
    return None

def force_mock_serial():
    """Force mocking of the serial module regardless of device availability"""
    print("🔧 Forcing mock serial mode")
    
    # Mock the serial import to avoid serial port errors during testing
    class MockSerial:
        def __init__(self, **kwargs):
            pass
        
        def is_open(self):
            return True
        
        def close(self):
            pass
        
        @property
        def in_waiting(self):
            return 0
        
        def readline(self):
            return b''
        
        def write(self, data):
            pass
        
        def flush(self):
            pass
    
    # Mock the serial module
    sys.modules['serial'] = type('MockSerialModule', (), {
        'Serial': MockSerial,
        'PARITY_NONE': 'N',
        'STOPBITS_ONE': 1,
        'EIGHTBITS': 8
    })
    print("✅ Serial module mocked successfully")

def is_serial_mocked():
    """Check if the serial module is currently mocked"""
    if 'serial' in sys.modules and hasattr(sys.modules['serial'], 'Serial'):
        return 'MockSerialModule' in str(type(sys.modules['serial']))
    return False

def get_detailed_test_mode_info():
    """Get detailed information about the current test mode and device status"""
    device_available = is_serial_device_available()
    serial_mocked = is_serial_mocked()
    preferred_device = get_preferred_device_path()
    
    info = {
        'device_available': device_available,
        'serial_mocked': serial_mocked,
        'preferred_device': preferred_device,
        'mode': 'Unknown'
    }
    
    if serial_mocked:
        info['mode'] = 'Mock Mode - Serial module mocked'
    elif device_available:
        if preferred_device == '/dev/ttyAMA5':
            info['mode'] = 'Real Hardware Mode - Primary LoRa device available'
        else:
            info['mode'] = 'Real Hardware Mode - Fallback device available'
    else:
        info['mode'] = 'No Device Mode - No serial devices found'
    
    return info

if __name__ == "__main__":
    # Test the utility functions
    print("🧪 Testing Serial Device Detection")
    print("=" * 40)
    
    available = is_serial_device_available()
    print(f"Device available: {available}")
    
    preferred_path = get_preferred_device_path()
    if preferred_path:
        print(f"Preferred device: {preferred_path}")
        if preferred_path == '/dev/ttyAMA5':
            print("✅ Primary LoRa device available")
        else:
            print("⚠️  Using fallback device")
    else:
        print("❌ No suitable device found")
    
    print("\n📊 Detailed Test Mode Information:")
    print("-" * 30)
    detailed_info = get_detailed_test_mode_info()
    for key, value in detailed_info.items():
        print(f"  {key}: {value}")
    
    print(f"\n🔧 Serial Module Status:")
    print("-" * 25)
    print(f"  Mocked: {is_serial_mocked()}")
    print(f"  In sys.modules: {'serial' in sys.modules}")
    if 'serial' in sys.modules:
        print(f"  Has Serial class: {hasattr(sys.modules['serial'], 'Serial')}")
        if hasattr(sys.modules['serial'], 'Serial'):
            print(f"  Serial class type: {type(sys.modules['serial'].Serial)}")
    
    print(f"\n📋 Test Mode Summary:")
    print("-" * 20)
    setup_serial_for_testing()
    print(f"  Final mode: {get_test_mode_info()}")
