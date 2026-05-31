#!/usr/bin/env python
"""
Test script to reproduce the specific encoding error
"""

import pytest
from lora_handler_concurrent import LoRaHandler
import time

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_specific_error():
    """Test the specific error scenario"""
    print("=== Testing Specific Error Scenario ===")
    
    handler = LoRaHandler()
    
    # Test data that might cause the bytes error
    test_data = {
        "timestamp": int(time.time()),
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "flood_bitmap_compressed": "test_string_data"  # This is a string, not bytes
    }
    
    print(f"Testing with data: {test_data}")
    print(f"Data types: {[(k, type(v)) for k, v in test_data.items()]}")
    
    try:
        print("\nAttempting to encode data...")
        packet = handler.compressed_encoding(test_data)
        print(f"✓ Encoding successful: {len(packet)} bytes")
        print(f"  Hex: {packet.hex()}")
        
        # Now try to queue it
        print("\nAttempting to queue encoded data...")
        result = handler.queue_transmit(test_data)
        print(f"✓ Queuing successful: {result}")
        
        # Process the queue
        print("\nProcessing queue...")
        handler.process_transmit_queue()
        print("✓ Queue processing successful")
        
    except Exception as e:
        print(f"✗ Error occurred: {e}")
        import traceback
        traceback.print_exc()
    
    handler.close()

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_with_bytes_blob():
    """Test with proper bytes blob"""
    print("\n=== Testing with Bytes Blob ===")
    
    handler = LoRaHandler()
    
    # Test data with proper bytes blob
    test_data = {
        "timestamp": int(time.time()),
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "flood_bitmap_compressed": b"test_binary_data"  # This is bytes
    }
    
    print(f"Testing with data: {test_data}")
    print(f"Data types: {[(k, type(v)) for k, v in test_data.items()]}")
    
    try:
        print("\nAttempting to encode data...")
        packet = handler.compressed_encoding(test_data)
        print(f"✓ Encoding successful: {len(packet)} bytes")
        print(f"  Hex: {packet.hex()}")
        
        # Now try to queue it
        print("\nAttempting to queue encoded data...")
        result = handler.queue_transmit(test_data)
        print(f"✓ Queuing successful: {result}")
        
        # Process the queue
        print("\nProcessing queue...")
        handler.process_transmit_queue()
        print("✓ Queue processing successful")
        
    except Exception as e:
        print(f"✗ Error occurred: {e}")
        import traceback
        traceback.print_exc()
    
    handler.close()

def main():
    """Run the tests"""
    print("Specific Error Reproduction Test")
    print("=" * 50)
    
    test_specific_error()
    test_with_bytes_blob()
    
    print("\n" + "=" * 50)
    print("Tests completed!")

if __name__ == "__main__":
    main()
