# Bitmap SF-Adaptive Compression — Device-Side Changes

**Date:** May 2026  
**Related repo:** WaterCam API (`fix/bitmap-sf-adaptive-raw-mode`)  
**Affected files:** `ticktalk_main.py`

---

## Problem

Bitmaps transmitted over LoRaWAN arrived at the server as truncated Brotli streams,
causing `Unexpected EOF` on every decompression attempt. All 26 bitmaps in the InfluxDB
backlog were affected.

## Root Cause

The device always compressed bitmaps to ≤228 B (`compress_segmented.py` default), which
accounts for the 14-byte TTLoRa header overhead at SF7 (242 B LoRaWAN limit). When the
mDot operated at SF8 or higher, the LoRaWAN gateway truncated the payload to the SF's
hard limit without any error indication to the device.

The `LoRaHandler.current_size_limit` field tracked the mDot-reported payload budget
(updated via `+TXS:` AT responses), and `_attempt_transmission()` correctly rejected
payloads exceeding it. However, if `current_size_limit` had not yet been updated from
its default of 242 B (e.g., first boot, `+TXS:` not yet received), the check would pass
even when the actual SF limit was much smaller. The device would send the packet, the
gateway would truncate it, and the server received incomplete data.

Even when `current_size_limit` was correctly set, the size was checked *after* compression
— meaning the bitmap was already compressed to 228 B for a budget of, say, 53 B. The
`_attempt_transmission()` rejection prevented the send, but the bitmap was not re-compressed
at the correct size.

## Fix

### `_BITMAP_RAW_MODE_THRESHOLD = 128`

Defines the LoRaWAN payload budget (in bytes) below which the device switches to raw
transmission mode. Threshold of 128 B corresponds approximately to SF8/US915 500 kHz
(125 B limit); SF7 (133–242 B) uses tokenized mode.

### `compress_bitmap()` — SF-aware sizing

Before calling `compress_image()`, the function now queries `get_size_limit()` from the
`LoRaHandler` singleton. This returns the budget last reported by the mDot (defaulting
to 242 B if no `+TXS:` response has been received yet).

Two modes:

| Condition | `max_bytes` passed to `compress_image()` | Why |
|---|---|---|
| `lora_limit > 128` | `lora_limit − 14` | Leave 14 B for TTLoRa header |
| `lora_limit ≤ 128` | `lora_limit` | Full budget; no header in raw mode |

`compress_image()` runs a binary search (`find_best_size()`) to find the largest image
dimension that produces a compressed payload within `max_bytes`. If even a 32×32 image
exceeds the budget, `compress_bitmap()` returns `b''` and no transmission is attempted.

### `lora_token()` / `lora_token_with_tracker()` — conditional TTToken wrapping

Using the same threshold, the function decides how to transmit:

**Raw mode** (`lora_limit ≤ 128`):
- Sends bare bitmap bytes via `handler.queue_binary_transmit(bitmap)`.
- No TTToken/TTLoRa header — saves 14 bytes for actual image data.
- The server API's raw bitmap detection path handles this format
  (`app/chirpstack.py` lines 645–670 in the WaterCam API repo).

**Tokenized mode** (`lora_limit > 128`):
- Sends TTToken-wrapped bitmap (14-byte TTLoRa header + bitmap).
- Also sends bare bitmap bytes as before (dual transmission for redundancy).

Both functions now guard against an empty bitmap (`if not bitmap: return early`) to avoid
sending zero-byte payloads to the mDot.

## Payload Budget by SF (US915)

| SF | LoRaWAN limit | Mode | Bitmap budget | Old behaviour |
|---|---|---|---|---|
| SF7 / 500 kHz | 242 B | tokenized | 228 B | 228 B — fits, unchanged |
| SF7 / 125 kHz | 133 B | tokenized | 119 B | 228 B sent → **truncated to 133 B** |
| SF8 / 500 kHz | 125 B | **raw** | 125 B | 228 B sent → **truncated to 125 B** |
| SF8 / 125 kHz | 61 B | **raw** | 61 B | 228 B sent → **truncated to 61 B** |
| SF9 | 53 B | **raw** | 53 B | 228 B sent → **truncated to 53 B** |
| SF12 | ~11 B | **raw** | skipped (< 32 B min) | 228 B sent → truncated, unusable |

## Interaction with `compress_segmented.py`

`compress_segmented.py` was already correct — `find_best_size()` binary-searches for the
largest image that fits within `max_bytes`, so it naturally adapts when given a smaller
budget. The only missing piece was that `compress_bitmap()` was not passing the right
`max_bytes` value.

`compress_image()` may return `{'success': False}` when no image fits (budget < 32 B
minimum). This case is now handled explicitly: `compress_bitmap()` returns `b''` and
`lora_token()` skips transmission with a log message.

## Server-Side Counterpart

The WaterCam API also needed a fix: its raw bitmap detection only accepted `method == 0`
(bitpacked + Brotli). Method 1 (RLE + Brotli) bitmaps — which `compress_image()` may
choose when they produce a smaller output — were being silently discarded. This is fixed
in `app/chirpstack.py` in the companion PR.
