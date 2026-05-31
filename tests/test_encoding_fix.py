#!/usr/bin/env python
"""
Test script to verify the encoding fix works
"""

from lora_handler_concurrent import LoRaHandler

def test_basic_encoding():
    """Test basic encoding without problematic fields"""
    print("Testing basic encoding...")
    
    handler = LoRaHandler()
    
    # Basic data without tilt_roll_yaw or lat_lon_z
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

def test_f32_3_encoding():
    """Test encoding with f32_3 fields that were causing issues"""
    print("\nTesting f32_3 encoding...")
    
    handler = LoRaHandler()
    
    # Data with f32_3 fields
    f32_3_data = {
        "timestamp": 1748892908,
        "tilt_roll_yaw": [0.1, 0.2, 0.3],
        "lat_lon_z": [40.7128, -74.0060, 12.5],
        "temperature_celsius": 22.5
    }
    
    try:
        packet = handler.compressed_encoding(f32_3_data)
        print(f"✓ f32_3 encoding successful: {len(packet)} bytes")
        print(f"  Hex: {packet.hex()}")
        return True
    except Exception as e:
        print(f"✗ f32_3 encoding failed: {e}")
        return False

def test_complex_encoding():
    """Test encoding with all field types"""
    print("\nTesting complex encoding...")
    
    handler = LoRaHandler()
    
    # Complex data with all field types
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
        "flood_bitmap_compressed": b"test_binary_data",
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

import pytest

@pytest.mark.skip(reason="hardware integration: calls transmit() which waits 60s for mDot response")
def test_queue_transmit():
    """Test the full queue and transmit process"""
    print("\nTesting queue and transmit...")
    
    handler = LoRaHandler()
    
    # Test data
    test_data = {
        "timestamp": 1748892908,
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "tilt_roll_yaw": [0.1, 0.2, 0.3]
    }
    
    try:
        # Queue the data
        success = handler.queue_transmit(test_data)
        if success:
            print("✓ Data queued successfully")
            
            # Check queue size
            queue_size = handler.transmit_queue.qsize()
            print(f"  Queue size: {queue_size}")
            
            # Process queue
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
        print(f"✗ Queue test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("LoRa Encoding Fix Tests")
    print("=" * 40)
    
    tests = [
        test_basic_encoding,
        test_f32_3_encoding,
        test_complex_encoding,
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
        print("✓ All tests passed! The encoding fix works.")
    else:
        print("✗ Some tests failed. Check the output above.")
    
    # Clean up
    handler = LoRaHandler()
    handler.close()

if __name__ == "__main__":
    main()
