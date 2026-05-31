#!/usr/bin/env python
"""
Test script to demonstrate hex string transmission without conversion
"""

import pytest
from lora_handler_concurrent import LoRaHandler, transmit_binary, transmit_auto, get_lora_handler
import time

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_hex_string_transmission():
    """Test transmitting hex strings directly without conversion"""
    print("=== Testing Hex String Transmission ===")
    
    handler = LoRaHandler()
    
    # Test 1: Hex string (should be sent directly as AT+SENDB=hexstring)
    print("\n1. Testing hex string transmission...")
    hex_string = "0004040100ff000000020000040000004600341bd301f88fd4582d3a9be0e95bbabd5439429e11d8aa241887da42759ca529812c4d9ea5759b09757999ecef9f8f8bdf065c4eef7ead197098a8170cfcdb76ce2d0eb15971ce2dcd684c3293ab951b6075891772f1ff9962e40371b26b993f618a8ea0a1ca61781a26536ad8caf81075e4afd17ed39ed047cab080dc9d0c62e5b44e8e7e13134e8f929e60fcfc7944c9cbb620a71ae4f3729cf8ac3f55c2d7d505c06a8e04555a503779b54970296c51b8e61dca294fbd1d79e5f3ee4e9e8d7a0732d673e090f6b4a15a28308c5d2d016691d2c3230271b790f80e"
    
    print(f"Hex string length: {len(hex_string)} characters")
    print(f"First 50 chars: {hex_string[:50]}...")
    
    try:
        result = handler.queue_binary_transmit(hex_string)
        print(f"✓ Hex string queued: {result}")
        print(f"  Queue size: {handler.transmit_queue.qsize()}")
    except Exception as e:
        print(f"✗ Hex string queuing failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Test 2: Bytes data (should be converted to hex)
    print("\n2. Testing bytes transmission...")
    bytes_data = b'\x00\x01\x02\x03\x04\x05'
    
    try:
        result = handler.queue_binary_transmit(bytes_data)
        print(f"✓ Bytes data queued: {result}")
        print(f"  Queue size: {handler.transmit_queue.qsize()}")
    except Exception as e:
        print(f"✗ Bytes data queuing failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Test 3: Process the queue to see the AT commands
    print("\n3. Processing queue to see AT commands...")
    try:
        handler.process_transmit_queue()
        print("✓ Queue processed successfully")
    except Exception as e:
        print(f"✗ Queue processing failed: {e}")
        import traceback
        traceback.print_exc()
    
    handler.close()

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_convenience_functions():
    """Test convenience functions with hex strings"""
    print("\n=== Testing Convenience Functions ===")
    
    # Test transmit_binary with hex string
    print("\n1. Testing transmit_binary with hex string...")
    hex_string = "deadbeef12345678"
    
    try:
        result = transmit_binary(hex_string)
        print(f"✓ transmit_binary result: {result}")
    except Exception as e:
        print(f"✗ transmit_binary failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Test transmit_auto with hex string
    print("\n2. Testing transmit_auto with hex string...")
    try:
        result = transmit_auto(hex_string)
        print(f"✓ transmit_auto result: {result}")
    except Exception as e:
        print(f"✗ transmit_auto failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Process all queues
    print("\n3. Processing all queues...")
    try:
        handler = get_lora_handler()
        handler.process_transmit_queue()
        print("✓ All queues processed")
    except Exception as e:
        print(f"✗ Queue processing failed: {e}")
        import traceback
        traceback.print_exc()

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_mixed_data_types():
    """Test mixing different data types"""
    print("\n=== Testing Mixed Data Types ===")
    
    handler = LoRaHandler()
    
    # Mix of data types
    test_items = [
        ("hex_string", "aabbccddee"),
        ("bytes", b'\x11\x22\x33\x44'),
        ("dict", {"timestamp": int(time.time()), "temp": 22.5}),
        ("another_hex", "ffeeddccbbaa")
    ]
    
    for item_type, data in test_items:
        print(f"\nTesting {item_type}: {data}")
        try:
            if item_type == "dict":
                result = handler.queue_transmit(data)
            else:
                result = handler.queue_binary_transmit(data)
            print(f"✓ Queued successfully: {result}")
        except Exception as e:
            print(f"✗ Failed: {e}")
    
    print(f"\nFinal queue size: {handler.transmit_queue.qsize()}")
    
    # Process the queue
    print("\nProcessing mixed queue...")
    try:
        handler.process_transmit_queue()
        print("✓ Mixed queue processed successfully")
    except Exception as e:
        print(f"✗ Mixed queue processing failed: {e}")
        import traceback
        traceback.print_exc()
    
    handler.close()

def main():
    """Run all tests"""
    print("Hex String Transmission Test")
    print("=" * 50)
    
    try:
        test_hex_string_transmission()
        test_convenience_functions()
        test_mixed_data_types()
        
        print("\n" + "=" * 50)
        print("✓ All tests completed!")
        
    except Exception as e:
        print(f"\n✗ Test suite crashed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
