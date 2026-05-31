#!/usr/bin/env python
"""
Test script to debug LoRa encoding issues
"""

import pytest
from lora_handler_concurrent import LoRaHandler

def test_basic_encoding():
    """Test basic sensor data encoding"""
    print("Testing basic sensor data encoding...")
    
    handler = LoRaHandler()
    
    # Test basic data
    basic_data = {
        "timestamp": 1748892908,
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "battery_percent": 85
    }
    
    try:
        packet = handler.compressed_encoding(basic_data)
        print(f"✓ Basic encoding successful: {len(packet)} bytes")
        print(f"  Hex: {packet.hex()}")
        return True
    except Exception as e:
        print(f"✗ Basic encoding failed: {e}")
        return False

def test_complex_encoding():
    """Test complex sensor data encoding"""
    print("\nTesting complex sensor data encoding...")
    
    handler = LoRaHandler()
    
    # Test complex data with all fields
    complex_data = {
        "timestamp": 1748892908,
        "emergency_status": 0,
        "health_status": 1,
        "movement_threshold": 0,
        "battery_percent": 85,
        "tilt_roll_yaw": [0.1, 0.2, 0.3],
        "lat_lon_z": [40.7128, -74.0060, 12.5],
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "camera_flood_detected": 0,
        "camera_flood_growing": 0,
        "flood_bitmap_compressed": b"test_binary_data",  # bytes
        "status_area_threshold": 10,
        "stage_threshold": 50,
        "monitoring_frequency": 60,
        "emergency_frequency": 5,
        "neighborhood_emergency_frequency": 30
    }
    
    try:
        packet = handler.compressed_encoding(complex_data)
        print(f"✓ Complex encoding successful: {len(packet)} bytes")
        print(f"  Hex: {packet.hex()}")
        return True
    except Exception as e:
        print(f"✗ Complex encoding failed: {e}")
        return False

def test_string_blob_encoding():
    """Test encoding with string blob data"""
    print("\nTesting string blob encoding...")
    
    handler = LoRaHandler()
    
    # Test with string blob data
    string_blob_data = {
        "timestamp": 1748892908,
        "flood_bitmap_compressed": "test_string_data",  # string instead of bytes
        "temperature_celsius": 22.5
    }
    
    try:
        packet = handler.compressed_encoding(string_blob_data)
        print(f"✓ String blob encoding successful: {len(packet)} bytes")
        print(f"  Hex: {packet.hex()}")
        return True
    except Exception as e:
        print(f"✗ String blob encoding failed: {e}")
        return False

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_queue_transmit():
    """Test queuing and processing transmissions"""
    print("\nTesting queue transmission...")
    
    handler = LoRaHandler()
    
    # Test data
    test_data = {
        "timestamp": 1748892908,
        "temperature_celsius": 22.5,
        "relative_humidity": 55
    }
    
    try:
        # Queue the data
        success = handler.queue_transmit(test_data)
        if success:
            print("✓ Data queued successfully")
            
            # Check queue size
            queue_size = handler.transmit_queue.qsize()
            print(f"  Queue size: {queue_size}")
            
            # Process queue (this would normally transmit, but we're just testing)
            print("  Processing queue...")
            handler.process_transmit_queue()
            
            # Check if queue is empty
            if handler.transmit_queue.empty():
                print("✓ Queue processed successfully")
                return True
            else:
                print(f"✗ Queue not empty after processing: {handler.transmit_queue.qsize()} items")
                return False
        else:
            print("✗ Failed to queue data")
            return False
    except Exception as e:
        print(f"✗ Queue transmission test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("LoRa Handler Encoding Tests")
    print("=" * 40)
    
    tests = [
        test_basic_encoding,
        test_complex_encoding,
        test_string_blob_encoding,
        test_queue_transmit
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
    
    print("\n" + "=" * 40)
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("✓ All tests passed!")
    else:
        print("✗ Some tests failed. Check the output above.")
    
    # Clean up
    handler = LoRaHandler()
    handler.close()

if __name__ == "__main__":
    main()
