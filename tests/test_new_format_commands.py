import pytest
#!/usr/bin/env python3
"""
Test script to verify the new LoRa command format [Channel][Command][Value]
"""

import sys
import os

# Import test utilities for serial device detection
from test_utils import get_preferred_device_path, force_mock_serial

# Setup serial for testing BEFORE importing any LoRa modules
# Note: We prefer to use /dev/ttyAMA5 for LoRa, so we'll use mock mode
# unless the primary device is actually available
preferred_device = get_preferred_device_path()
if preferred_device == '/dev/ttyAMA5':
    print("🔌 Primary LoRa device (/dev/ttyAMA5) available - using real hardware")
    # Don't mock - let the real serial module be used
else:
    print("🔧 Primary LoRa device not available - using mock mode for testing")
    # Force mocking since we don't have the preferred device
    force_mock_serial()
    print("✅ Mock serial applied - now importing LoRa modules")

# Now import LoRa modules after serial setup
from lora_handler_concurrent import LoRaHandler

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_new_format_commands():
    """Test the new command format"""
    
    print("🧪 Testing New LoRa Command Format")
    print("=" * 50)
    print("Format: [Channel][Command][Value]")
    print("=" * 50)
    
    # Create handler instance
    handler = LoRaHandler()
    
    # Test cases with expected results
    # Format: [Channel][Command][Value] where:
    # - Channel: 2 digits (10, 11, 12, etc.)
    # - Command: 1 digit (9, 0, etc.)
    # - Value: 1+ digits (0, 1, 10, etc.)
    test_cases = [
        ('1090', 'Area threshold 0%', 'area_threshold', 0),      # Channel 10, Command 9, Value 0
        ('10910', 'Area threshold 100%', 'area_threshold', 100),  # Channel 10, Command 9, Value 10
        ('1191', 'Stage threshold 1 cm', 'stage_threshold', 1),   # Channel 11, Command 9, Value 1
        ('1292', 'Monitoring frequency 2 min', 'monitoring_frequency', 2),  # Channel 12, Command 9, Value 2
        ('1393', 'Emergency frequency 3 min', 'emergency_frequency', 3),    # Channel 13, Command 9, Value 3
        ('2100', 'Emergency mode on', 'emergency_mode', True),              # Channel 21, Command 0, Value 0
        ('9900', 'Emergency mode off', 'emergency_mode', False),            # Channel 99, Command 0, Value 0
    ]
    
    for command, description, param_name, expected_value in test_cases:
        print(f"\n📡 Testing: {command} - {description}")
        print("-" * 40)
        
        # Process the command
        handler.decode(command)
        
        # Get the updated parameter value
        actual_value = handler.config.get(param_name)
        print(f"Parameter: {param_name}")
        print(f"Expected: {expected_value}")
        print(f"Actual: {actual_value}")
        
        if actual_value == expected_value:
            print("✅ PASS")
        else:
            print("❌ FAIL")
            print(f"   Expected {expected_value}, got {actual_value}")
    
    # Show final configuration
    print(f"\n📋 Final Configuration:")
    print("=" * 30)
    for key, value in handler.config.items():
        print(f"{key}: {value}")
    
    handler.close()

if __name__ == "__main__":
    test_new_format_commands()
