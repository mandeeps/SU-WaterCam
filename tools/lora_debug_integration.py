#!/usr/bin/env python3
"""
LoRa Debug Integration

Answers the remote debug-status downlink (channel 50, command 01), which
LoRaHandler.decode() in tools/lora_handler_concurrent.py routes here. The
report itself is built by tools/debug_status_command.py (needs psutil).

Sending the request
-------------------
Send the downlink as the ASCII digits "5001" (any trailing digit works, e.g.
"50011"). The hex/TLV form "500100" does NOT work: decode() tries the TLV
parser first, reads it as channel 0x50 = 80, finds no handler and drops it.

The reply goes out as one uplink, about 156 bytes. It fits the 242-byte limit
at the default data rate but not at the lowest US915 rates (DR1/DR2), where
it is rejected as too large. It also waits in the LoRa daemon's queue until
the next transmit, so a node that powers off first never sends it.
"""

from datetime import datetime
from typing import Dict, Any, Optional


def handle_debug_status_request() -> Dict[str, Any]:
    """Build the debug status report and return it ready to transmit.

    On success `data` is the compact JSON report hex-encoded: the caller hands
    it to queue_binary_transmit(), which treats every str as hex and sends it
    unchanged. Passing the JSON text itself would put non-hex characters on
    the mDot's hex payload path.
    """
    try:
        from tools.debug_status_command import handle_debug_status_command

        print("🔍 Debug status requested via LoRa command")
        result = handle_debug_status_command()

        if result['status'] == 'debug_status_generated':
            payload = result['lora_formatted'].encode('utf-8')
            print(f"✅ Debug status generated: {len(payload)} bytes")
            return {
                'command': 'debug_status_response',
                'timestamp': result['timestamp'],
                'status': 'success',
                'data': payload.hex(),
                'size_bytes': len(payload),
            }

        print(f"❌ Debug status failed: {result.get('error', 'Unknown error')}")
        return {
            'command': 'debug_status_response',
            'timestamp': result['timestamp'],
            'status': 'error',
            'error': result.get('error', 'Unknown error'),
        }

    except Exception as e:
        print(f"❌ Debug status request failed: {e}")
        return {
            'command': 'debug_status_response',
            'timestamp': datetime.now().isoformat(),
            'status': 'error',
            'error': str(e),
        }


def process_debug_command(command_data: str) -> Optional[Dict[str, Any]]:
    """Handle a raw debug command string such as "50011" (type 50, command 01).

    Returns the response for a debug status command, otherwise None.
    """
    try:
        if command_data.startswith('5001'):
            print(f"🔍 Processing debug status command: {command_data}")
            return handle_debug_status_request()
        return None
    except Exception as e:
        print(f"⚠️ Failed to process debug command {command_data}: {e}")
        return None


if __name__ == "__main__":
    response = handle_debug_status_request()
    print(f"status: {response['status']}")
    if response['status'] == 'success':
        print(f"payload ({response['size_bytes']} bytes): {bytes.fromhex(response['data']).decode('utf-8')}")
