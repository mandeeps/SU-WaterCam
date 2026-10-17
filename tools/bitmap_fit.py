"""Fitting a capture's LoRa bitmap packet (TLV 08 18) to the mDot's payload room.

The room the mDot reports (AT+TXS) changes from one send to the next: 11 B at
DR0 after a rejoin, up to 242 B once ADR raises the data rate, and a few bytes
less than the data rate's maximum while a MAC command answer is pending (it
rides in FOpts, up to 15 B, ahead of our payload).

- A bitmap sent now is compressed for the full room now (packet_budget(limit)):
  resolution first; "Tx buffer filled by MAC Commands" is handled by the
  daemon's transmit() flushing the MAC queue and retrying.
- A bitmap that goes into the store-and-forward queue (no room now) is
  compressed with QUEUE_MARGIN bytes of headroom (queue_budget(limit)): it will
  be sent at an unknown room later, typically the data rate's maximum minus a
  pending MAC answer or two (LinkADRAns 2 B, DevStatusAns 3 B, RXParamSetupAns
  2 B, RXTimingSetupAns 1 B, ...). 12 B covers several at once while costing
  about 5 % of the bitmap at 242 B.
- When a queued bitmap still doesn't fit when it is drained, it is compressed
  again from the capture's mask at the room then (refit_packet()); the queue
  entry keeps the mask path for that (tools/lora_store_forward.py).
"""
import os
from typing import Callable, Optional

#: The largest LoRaWAN payload the mDot takes (US915 DR4 / SF8 500 kHz, and DR8+).
MAX_PAYLOAD = 242
#: 08 18 + 2-byte length ahead of the bitmap (tools/lora_handler_concurrent.py).
TLV_OVERHEAD = 4
#: Headroom kept in a queued bitmap packet for MAC command answers (FOpts).
QUEUE_MARGIN = 12
#: Smallest bitmap compress_image() makes (a 32 px header + data); below it there is no room.
MIN_BITMAP = 32


def packet_budget(limit: int) -> Optional[int]:
    """Bitmap bytes for a packet sent now at room *limit*; None if no bitmap fits."""
    budget = min(int(limit), MAX_PAYLOAD) - TLV_OVERHEAD
    return budget if budget >= MIN_BITMAP else None


def queue_budget() -> int:
    """Bitmap bytes for a packet that waits in the queue: the largest room
    (MAX_PAYLOAD) less the TLV and QUEUE_MARGIN (226 B)."""
    return MAX_PAYLOAD - TLV_OVERHEAD - QUEUE_MARGIN


def method2_allowed(get_parameter: Optional[Callable] = None) -> bool:
    """Runtime parameter bitmap_method2_enabled (default on); off if it can't be read."""
    try:
        if get_parameter is None:
            from tools.lora_runtime_integration import get_parameter
        return get_parameter('bitmap_method2_enabled', True) is not False
    except Exception:
        return False


def compress_mask(mask: str, max_bytes: int, method2: bool) -> bytes:
    """The mask compressed to at most *max_bytes*, or b'' (no mask, not a mask, too small)."""
    if not mask or not os.path.exists(mask):
        return b''
    from tools.compress_segmented import compress_image
    try:
        result = compress_image(mask, max_bytes=max_bytes, method2=method2)
    except Exception as e:
        print(f"⚠️ Bitmap re-compression of {mask} failed: {e}")
        return b''
    if not result.get('success'):
        return b''
    return bytes(result['compressed_data'])


def refit_packet(mask: str, limit: int, encode: Callable, method2: bool) -> Optional[bytes]:
    """A bitmap packet for room *limit* now, compressed again from *mask*; None if
    there is no room for a bitmap, no mask, or the result still doesn't fit."""
    budget = packet_budget(limit)
    if budget is None:
        return None
    bitmap = compress_mask(mask, budget, method2)
    if not bitmap:
        return None
    packet = encode({'flood_bitmap_compressed': bitmap})
    return packet if len(packet) <= limit else None
