#!/usr/bin/env python
"""
Simple test to verify hex string formatting
"""

import pytest
from lora_handler_concurrent import LoRaHandler

@pytest.mark.skip(reason="hardware integration: calls process_transmit_queue() which waits 60s for mDot")
def test_hex_formatting():
    """Test that hex strings are properly formatted"""
    print("=== Testing Hex String Formatting ===")
    
    handler = LoRaHandler()
    
    # Test hex string
    hex_string = "aabbccddee"
    print(f"Input hex string: {hex_string}")
    
    try:
        # Queue the hex string
        result = handler.queue_binary_transmit(hex_string)
        print(f"✓ Queued successfully: {result}")
        
        # Process the queue to see the formatted AT command
        print("\nProcessing queue to see AT command format...")
        handler.process_transmit_queue()
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
    
    handler.close()

if __name__ == "__main__":
    test_hex_formatting()
