#!/usr/bin/env python
"""
Test script for the new LoRa reception format
Demonstrates the [Channel][Command][Value] format
"""

import pytest
from lora_handler_concurrent import LoRaHandler

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_reception_format():
    """Test various reception format examples"""
    
    # Create handler instance
    handler = LoRaHandler()
    
    # Test cases for the new format
    test_cases = [
        "1090",    # Channel 10, Command 90, Value 0 (Area Threshold 0%)
        "1010",    # Channel 10, Command 90, Value 10 (Area Threshold 100%)
        "1191",    # Channel 11, Command 91, Value 1 (Stage Threshold 1 cm)
        "1192",    # Channel 11, Command 91, Value 92 (Stage Threshold 92 cm)
        "1292",    # Channel 12, Command 92, Value 2 (Monitoring Frequency 2 minutes)
        "1393",    # Channel 13, Command 93, Value 3 (Emergency Frequency 3 minutes)
        "1494",    # Channel 14, Command 94, Value 4 (Photo Interval 4 minutes)
        "1595",    # Channel 15, Command 95, Value 5 (Neighborhood Emergency Frequency 5 minutes)
        "2000",    # Channel 20, Command 00, Value 0 (Transmission disabled)
        "2001",    # Channel 20, Command 00, Value 1 (Transmission enabled)
        "2200",    # Channel 22, Command 00, Value 0 (Debug mode disabled)
        "2201",    # Channel 22, Command 00, Value 1 (Debug mode enabled)
        "2300",    # Channel 23, Command 00, Value 0 (GPS disabled)
        "2301",    # Channel 23, Command 00, Value 1 (GPS enabled)
        "3000",    # Channel 30, Command 00, Value 0 (Battery threshold 0%)
        "3015",    # Channel 30, Command 00, Value 15 (Battery threshold 15%)
        "3100",    # Channel 31, Command 00, Value 0 (Compression level 0, will be clamped to 1)
        "3150",    # Channel 31, Command 00, Value 50 (Compression level 50, will be clamped to 10)
        "3200",    # Channel 32, Command 00, Value 0 (Max retransmissions 0)
        "3250",    # Channel 32, Command 00, Value 50 (Max retransmissions 50)
        "4000",    # Channel 40, Command 00, Value 0 (Auto shutdown disabled)
        "4001",    # Channel 40, Command 00, Value 1 (Auto shutdown enabled)
        "4100",    # Channel 41, Command 00, Value 0 (Shutdown iteration limit 0)
        "4120",    # Channel 41, Command 00, Value 20 (Shutdown iteration limit 20)
        "4200",    # Channel 42, Command 00, Value 0 (Data retention 0 days)
        "4260",    # Channel 42, Command 00, Value 60 (Data retention 60 days)
        "4300",    # Channel 43, Command 00, Value 0 (Backup disabled)
        "4301",    # Channel 43, Command 00, Value 1 (Backup enabled)
        "2100",    # Channel 21, Command 00, Value 0 (Emergency mode activated)
        "9900",    # Channel 99, Command 00, Value 0 (Emergency mode deactivated)
    ]
    
    print("🧪 Testing new LoRa reception format")
    print("=" * 50)
    print("Format: [Channel][Command][Value]")
    print("=" * 50)
    
    for test_case in test_cases:
        print(f"\n📡 Testing: {test_case}")
        print("-" * 30)
        handler.test_reception_format(test_case)
        print("-" * 30)
    
    # Test invalid formats
    print(f"\n❌ Testing invalid formats")
    print("-" * 30)
    invalid_cases = [
        "21",      # Legacy format (still supported)
        "123",     # Too short for new format
        "abc",     # Non-numeric
        "",        # Empty
    ]
    
    for test_case in invalid_cases:
        print(f"\n📡 Testing invalid: {test_case}")
        print("-" * 20)
        handler.test_reception_format(test_case)
        print("-" * 20)
    
    # Show final configuration
    print(f"\n📋 Final Configuration:")
    print("=" * 30)
    for key, value in handler.config.items():
        print(f"{key}: {value}")
    
    handler.close()

if __name__ == "__main__":
    test_reception_format()
