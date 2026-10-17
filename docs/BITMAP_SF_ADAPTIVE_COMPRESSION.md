# Bitmap SF-Adaptive Compression — Device-Side Changes

The bitmap byte format itself (header, bitpacking and RLE payloads, decoding) is specified in [BITMAP_DECODING_SPEC.md](BITMAP_DECODING_SPEC.md).

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

### When the bitmap goes out over LoRa (`tools/bitmap_policy.py`)

A wake takes about two captures a minute apart, and a site's mask is usually
unchanged for long periods (all water or all dry), so the ~230-byte bitmap for
every capture mostly repeats the last one. The sensor packet already carries
the capture's water % (07 37) and, with flood detection on, the flood flags
(07 17 / 07 27). `lora_token_with_tracker()` therefore sends a capture's bitmap
over LoRa only if any of these holds:

- no bitmap has been sent before, or `data/lora_bitmap_state.json` is missing or unreadable;
- the water % differs from the last bitmap sent by `max(1, area_threshold)` points or more;
- `camera_flood_detected` or `camera_flood_growing` is 1, or either changed since the last bitmap sent;
- emergency mode is on;
- the last bitmap sent is `bitmap_refresh_hours` old or more (runtime parameter,
  default 24, range 0–168; 0 sends every bitmap, the previous behaviour);
- the capture's water % is unknown (no usable mask), so nothing is dropped silently.

Otherwise the bitmap is skipped and the log has one line giving the reason, the
last bitmap's water % and its age; the sensor packet goes out as usual. IP
uploads send the bitmap every capture.

The state is written (atomically, `tools/config_io.py`) when the mDot transmits
the bitmap, live or drained from the store-and-forward queue; never when it is
only queued or skipped. While a bitmap waits in the queue, later captures are
compared with the last one transmitted, and one the policy sends replaces the
queued one (only the newest bitmap waits; an older one would only show the
dashboard an older scene). Superseded bitmaps are not counted in `sf_dropped`.

```json
{"water_pct": 12, "time": 1760000000, "capture": "20261015-120000",
 "camera_flood_detected": 0, "camera_flood_growing": 0}
```

`time` is the capture time. A `time` in the future (the clock moved back) counts
as due for a refresh.

## Bitmaps that wait in the queue (`tools/bitmap_fit.py`)

- Sent now: compressed for the full room now (`limit - 4`). If the bitmap was
  compressed for a larger room (the stored limit lags, or a pending MAC answer
  takes a few bytes: AT+TXS reports e.g. 240 B), it is compressed again from
  the capture's mask for the room now and sent.
- No room now (DR0, 11 B): compressed for the queue with 12 B of headroom for
  MAC answers in FOpts (242 − 4 − 12 = 226 B), since it goes out later at an
  unknown room. The queue entry keeps the mask path.
- Draining: a queued bitmap larger than the room then is compressed again from
  its mask at that room (method 2 per `bitmap_method2_enabled`); it waits only
  when no bitmap fits at all (room < 36 B) or the mask is gone.

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

## UART Inter-Process Lock

`LoRaHandler` uses `fcntl.lockf` on `/run/lock/watercam-lora.lock` to serialize
UART access across concurrent processes. The file is created with mode `0o600`
(owner-only) to prevent symlink/hardlink attacks. `/run/lock` is a tmpfs directory
cleared on reboot — if diagnosing UART contention issues, check for stale holders
with `lsof /run/lock/watercam-lora.lock`.

## Server-Side Counterpart

The WaterCam API also needed a fix: its raw bitmap detection only accepted `method == 0`
(bitpacked + Brotli). Method 1 (RLE + Brotli) bitmaps — which `compress_image()` may
choose when they produce a smaller output — were being silently discarded. This is fixed
in `app/chirpstack.py` in the companion PR.
