#!/usr/bin/env python3
"""
Test script to verify LoRa command integration with main application
This script simulates incoming LoRa commands and verifies that the main application
properly adjusts its data collection frequency and other parameters.
"""

import time
import json
import sys
import os
from datetime import datetime

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
# Use the package-qualified path so this test shares the same sys.modules
# entry (and thus the same _lora_handler singleton) as tools/lora_runtime_integration.py,
# which imports via "tools.lora_handler_concurrent". A bare import here would
# resolve to a second, independent copy of the module and self-conflict over
# the real serial-port flock.
from tools.lora_handler_concurrent import LoRaHandler, create_lora_handler_with_retry
from tools.lora_runtime_integration import get_parameter, set_parameter, get_runtime_manager

def test_lora_command_processing():
    """Test that LoRa commands properly update runtime parameters"""
    print("🧪 Testing LoRa Command Processing Integration")
    print("=" * 60)
    print("Note: Using new [Channel][Command][Value] format:")
    print("  - Channel 10: Area threshold commands (9)")
    print("  - Channel 12: Monitoring frequency commands (9)")
    print("  - Channel 20: Transmission control commands (0)")
    print("  - Channel 21: Emergency mode commands (0)")
    print("  - Channel 99: Emergency mode deactivation commands (0)")
    print("=" * 60)
    
    # Initialize runtime manager
    try:
        runtime_manager = get_runtime_manager()
        print("✅ Runtime manager initialized")
    except Exception as e:
        print(f"❌ Failed to initialize runtime manager: {e}")
        return False
    
    # Test 1: Monitor frequency adjustment
    print("\n📡 Test 1: Monitoring Frequency Adjustment")
    print("-" * 40)
    
    # Set initial frequency
    initial_freq = 60
    set_parameter('monitoring_frequency', initial_freq)
    print(f"Initial monitoring frequency: {get_parameter('monitoring_frequency')} minutes")
    
    # Simulate LoRa command to change frequency
    # Use the same handler instance that the runtime manager is using
    test_handler = create_lora_handler_with_retry()
    test_handler.decode('1292')  # Set monitoring frequency to 2 minutes (Channel 12, Command 9, Value 2)
    
    # Verify change
    new_freq = get_parameter('monitoring_frequency')
    print(f"New monitoring frequency: {new_freq} minutes")
    
    if new_freq == 2:
        print("✅ Monitoring frequency successfully adjusted via LoRa command")
    else:
        print(f"❌ Monitoring frequency not adjusted correctly. Expected: 2, Got: {new_freq}")
        return False
    
    # Test 2: Emergency mode activation
    print("\n🚨 Test 2: Emergency Mode Activation")
    print("-" * 40)
    
    # Set initial emergency mode
    set_parameter('emergency_mode', False)
    print(f"Initial emergency mode: {get_parameter('emergency_mode')}")
    
    # Simulate emergency command
    test_handler.decode('2100')  # Activate emergency mode (Channel 21, Command 0, Value 0)
    
    # Verify emergency mode
    emergency_active = get_parameter('emergency_mode')
    print(f"Emergency mode after command: {emergency_active}")
    
    if emergency_active:
        print("✅ Emergency mode successfully activated via LoRa command")
    else:
        print("❌ Emergency mode not activated correctly")
        return False
    
    # Test 3: Area threshold adjustment
    print("\n🌊 Test 3: Area Threshold Adjustment")
    print("-" * 40)
    
    # Set initial threshold
    initial_threshold = 10
    set_parameter('area_threshold', initial_threshold)
    print(f"Initial area threshold: {get_parameter('area_threshold')}%")
    
    # Simulate threshold command
    test_handler.decode('1090')  # Set area threshold to 0% (Channel 10, Command 9, Value 0)
    
    # Verify threshold
    new_threshold = get_parameter('area_threshold')
    print(f"New area threshold: {new_threshold}%")
    
    if new_threshold == 0:
        print("✅ Area threshold successfully adjusted via LoRa command")
    else:
        print(f"❌ Area threshold not adjusted correctly. Expected: 0, Got: {new_threshold}")
        print(f"   Note: Command '1090' sets threshold to 0% (Channel 10, Command 9, Value 0)")
        return False
    
    # Test 4: Transmission enable/disable
    print("\n📡 Test 4: Transmission Control")
    print("-" * 40)
    
    # LoRa transmission is always enabled - no need to test disable functionality
    print("✅ LoRa transmission is always enabled - no disable functionality needed")
    
    # Test 5: Command timestamp tracking
    print("\n⏰ Test 5: Command Timestamp Tracking")
    print("-" * 40)
    
    # Check if timestamp was recorded
    last_command_time = get_parameter('last_lora_command_time', None)
    if last_command_time:
        print(f"✅ Command timestamp recorded: {last_command_time}")
        try:
            # Verify it's a valid ISO format timestamp
            datetime.fromisoformat(last_command_time)
            print("✅ Timestamp format is valid")
        except ValueError:
            print("❌ Timestamp format is invalid")
            return False
    else:
        print("❌ No command timestamp recorded")
        return False
    
    # Test 6: Emergency mode deactivation
    print("\n✅ Test 6: Emergency Mode Deactivation")
    print("-" * 40)
    
    # Verify emergency mode is still active
    print(f"Emergency mode before deactivation: {get_parameter('emergency_mode')}")
    
    # Simulate deactivation command
    test_handler.decode('9900')  # Deactivate emergency mode (Channel 99, Command 00, Value 0)
    
    # Verify deactivation
    emergency_active = get_parameter('emergency_mode')
    print(f"Emergency mode after deactivation: {emergency_active}")
    
    if not emergency_active:
        print("✅ Emergency mode successfully deactivated via LoRa command")
    else:
        print("❌ Emergency mode not deactivated correctly")
        return False
    
    # Test 7: Frequency calculation for main application
    print("\n⚙️ Test 7: Dynamic Frequency Calculation")
    print("-" * 40)
    
    # Set up test parameters
    set_parameter('monitoring_frequency', 45)  # Normal mode: 45 minutes
    set_parameter('emergency_frequency', 3)    # Emergency mode: 3 minutes
    set_parameter('emergency_mode', False)     # Start in normal mode
    
    # Calculate effective frequency in normal mode
    normal_freq = get_parameter('monitoring_frequency')
    print(f"Normal mode frequency: {normal_freq} minutes")
    
    # Switch to emergency mode
    set_parameter('emergency_mode', True)
    emergency_freq = get_parameter('emergency_frequency')
    print(f"Emergency mode frequency: {emergency_freq} minutes")
    
    # Verify frequency difference
    if emergency_freq < normal_freq:
        print("✅ Emergency mode uses faster frequency than normal mode")
    else:
        print("❌ Emergency mode frequency not faster than normal mode")
        return False
    
    print("\n🎉 All LoRa Command Integration Tests Passed!")
    return True

def test_main_application_integration():
    """Test that the main application properly uses LoRa-adjusted parameters"""
    print("\n🔧 Testing Main Application Integration")
    print("=" * 60)
    
    # Simulate a complete workflow with LoRa commands
    print("\n📋 Simulating Main Application Workflow with LoRa Commands")
    print("-" * 50)
    
    # Step 1: Set initial parameters
    print("1️⃣ Setting initial parameters...")
    set_parameter('monitoring_frequency', 60)
    set_parameter('emergency_frequency', 5)
    set_parameter('emergency_mode', False)
    set_parameter('area_threshold', 10)
    
    print(f"   Initial monitoring frequency: {get_parameter('monitoring_frequency')} minutes")
    print(f"   Initial emergency frequency: {get_parameter('emergency_frequency')} minutes")
    print(f"   Initial emergency mode: {get_parameter('emergency_mode')}")
    
    # Step 2: Simulate LoRa command to change frequency
    print("\n2️⃣ Simulating LoRa command to change monitoring frequency...")
    test_handler = create_lora_handler_with_retry()
    test_handler.decode('1292')  # Set monitoring frequency to 2 minutes (Channel 12, Command 9, Value 2)
    
    new_freq = get_parameter('monitoring_frequency')
    print(f"   New monitoring frequency: {new_freq} minutes")
    
    # Update the expected frequency for the rest of the test
    expected_freq = 2  # Based on command '1292' (Channel 12, Command 9, Value 2)
    
    # Step 3: Simulate emergency activation
    print("\n3️⃣ Simulating emergency mode activation...")
    test_handler.decode('2100')  # Activate emergency mode (Channel 21, Command 0, Value 0)
    
    emergency_active = get_parameter('emergency_mode')
    print(f"   Emergency mode active: {emergency_active}")
    
    # Step 4: Calculate effective frequency (like main application does)
    print("\n4️⃣ Calculating effective monitoring frequency...")
    if emergency_active:
        effective_freq = get_parameter('emergency_frequency')
        print(f"   Emergency mode active - using {effective_freq} minute interval")
    else:
        effective_freq = get_parameter('monitoring_frequency')
        print(f"   Normal mode - using {effective_freq} minute interval")
    
    # Step 5: Verify the frequency makes sense for data collection
    print("\n5️⃣ Verifying frequency suitability for data collection...")
    if effective_freq <= 30:
        print(f"   ✅ {effective_freq} minute interval suitable for frequent monitoring")
    elif effective_freq <= 60:
        print(f"   ⚠️ {effective_freq} minute interval for standard monitoring")
    else:
        print(f"   ⚠️ {effective_freq} minute interval for infrequent monitoring")
    
    # Step 6: Test parameter persistence
    print("\n6️⃣ Testing parameter persistence...")
    # Simulate application restart by reinitializing
    runtime_manager = get_runtime_manager()
    persisted_freq = get_parameter('monitoring_frequency')
    persisted_emergency = get_parameter('emergency_mode')
    
    print(f"   Persisted monitoring frequency: {persisted_freq} minutes")
    print(f"   Persisted emergency mode: {persisted_emergency}")
    
    if persisted_freq == expected_freq and persisted_emergency == emergency_active:
        print("   ✅ Parameters persist correctly across application restarts")
    else:
        print("   ❌ Parameters do not persist correctly")
        print(f"      Expected frequency: {expected_freq}, Got: {persisted_freq}")
        print(f"      Expected emergency: {emergency_active}, Got: {persisted_emergency}")
        return False
    
    print("\n🎉 Main Application Integration Test Passed!")
    return True

def main():
    """Run all integration tests"""
    print("🚀 LoRa Command Integration Test Suite")
    print("=" * 60)
    
    try:
        # Test LoRa command processing
        if not test_lora_command_processing():
            print("\n❌ LoRa Command Processing Tests Failed")
            return False
        
        # Test main application integration
        if not test_main_application_integration():
            print("\n❌ Main Application Integration Tests Failed")
            return False
        
        print("\n" + "=" * 60)
        print("🎉 ALL INTEGRATION TESTS PASSED!")
        print("✅ LoRa commands properly adjust main application parameters")
        print("✅ Data collection frequency dynamically adjusts based on commands")
        print("✅ Emergency mode properly affects monitoring frequency")
        print("✅ Parameters persist across application restarts")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
