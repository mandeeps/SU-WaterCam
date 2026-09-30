# Bitmap Compression and LoRa Transmission Analysis

## Overview
This document explains how flood detection bitmaps are compressed, transmitted over LoRa, and how they should be decoded on the dashboard.

**Decoding specification (for implementers/agents):** See **`docs/BITMAP_DECODING_SPEC.md`** for a concise, step-by-step format specification and decoding procedure (TLV layout, compressed bitmap header, method 0/1 payload layout, and Brotli).

## Compression Process

### Source Code
The compression is handled by `tools/compress_segmented.py` and called from `ticktalk_main.py` via the `compress_bitmap()` function.

### Compression Algorithm

The compression process follows these steps:

1. **Image Loading**: Loads the segmentation mask,
   `color_preserved_5_band_segmentation.png`. The mask is named after the five-band TIFF it
   comes from; captures segmented before 2026-09-26 carry the superseded name
   `final_5_band_segmentation.png`, and `coreg_multiple.segmentation_path()` accepts either.

   The mask is single-channel uint8 at the source resolution of the capture (972×1296), holding
   class indices scaled across 0–255 — so `{0, 255}` for the two-class water model.

   > **Masks written before 2026-09-26 are not masks.** `segment_tiff_5band.py` wrote this PNG
   > with `plt.savefig(bbox_inches='tight', dpi=150)`, producing an RGBA *rendering* of the class
   > palette at whatever size matplotlib chose — for a 972×1296 input, an 871×1162×4 image with
   > over 200 distinct values. `tools/watercam.py` only ever used that path, so a node running
   > `watercam.service` was compressing a picture of a mask. It never failed, because step 2
   > grayscales and thresholds whatever it is given: the render compressed to 216 bytes, under the
   > 228-byte cap, and produced a plausible-looking bitmap at the wrong aspect ratio with
   > antialiased edges. The same mask now compresses to 202 bytes. Bitmaps transmitted before that
   > date should be read with this in mind.

2. **Binary Thresholding**: 
   - Converts image to grayscale
   - Applies Otsu's method or mean threshold to create binary image (0/1 values)
   - Function: `to_binary()` → returns numpy array of uint8 (0 or 1)
   - On a true mask this is exact: the values are already `{0, 255}` and any threshold between
     them gives the same result. It was the tolerance of this step that let the rendering problem
     above go unnoticed.

3. **Size Optimization**:
   - Uses binary search to find the largest image size that fits within the byte limit (default: 228 bytes)
   - Resizes image while maintaining aspect ratio using LANCZOS resampling
   - Minimum size: 32x32 pixels
   - Maximum tested size: 160x160 pixels
   - Function: `find_best_size()`

4. **Compression Method Selection**:
   Two compression methods are tried, and the smaller result is chosen:
   
   **Method 0 (Bitpacking)**:
   - Packs each row of binary data into bits (8 pixels per byte)
   - Compresses with Brotli
   
   **Method 1 (RLE)**:
   - Applies Run-Length Encoding (RLE) to the flattened binary array
   - Compresses with Brotli
   
   Function: `compress_methods()` → returns `(compressed_bytes, method_id)`

5. **Header Creation**:
   Creates a minimal 5-byte header:
   - Byte 0: Method ID (0 or 1)
   - Bytes 1-2: Width (big-endian, 2 bytes)
   - Bytes 3-4: Height (big-endian, 2 bytes)
   
   Function: `minimal_header()`

6. **Final Output**:
   - Total size: `header (5 bytes) + compressed_data`
   - Maximum size constraint: 228 bytes (for TT token) or 242 bytes (without TT token)
   - Returns: `header + compressed_data` as bytes

### Compression Function Signature

```python
def compress_image(input_path, max_bytes=228, min_size=32, output_path=None, save_images=False):
    """
    Returns:
        dict: {
            'success': bool,
            'compressed_data': bytes,  # header + compressed payload
            'width': int,
            'height': int,
            'method': int,  # 0 or 1
            'total_size': int,
            'compressed_file_path': str
        }
    """
```

## Transmission Over LoRa

### Transmission Format

The bitmap is transmitted using the **TLV (Type-Length-Value) format**:

- **Channel**: `0x08`
- **Type**: `0x18` (flood bitmap compressed binary)
- **Format**: `[Channel:1B][Type:1B][Length:2B][Data:Length]`

### Transmission Code Path

1. **Compression**: `compress_bitmap()` → returns `compressed_data` (bytes)

2. **Encoding**: The bitmap is added to the LoRa packet via `compressed_encoding()`:
   ```python
   if 'flood_bitmap_compressed' in data:
       add_blob(0x08, 0x18, data['flood_bitmap_compressed'])
   ```
   
   The `add_blob()` function creates:
   - Channel byte: `0x08`
   - Type byte: `0x18`
   - Length: 2 bytes (big-endian uint16)
   - Data: The compressed bitmap bytes

3. **Transmission**: The packet is sent via `queue_binary_transmit()` → `transmit()`

### Packet Structure

```
[0x08][0x18][Length_High][Length_Low][Method][Width_High][Width_Low][Height_High][Height_Low][Compressed_Payload...]
```

Where:
- `Length` = total compressed bitmap size (including 5-byte header)
- `Method` = compression method (0 or 1)
- `Width/Height` = image dimensions
- `Compressed_Payload` = Brotli-compressed data

## Dashboard Decoding

### Current ChirpStack Codec

The ChirpStack device profile codec (`config/chirpstack_device_profile_codec.js`) currently extracts the bitmap but **does not decompress it**:

```javascript
case '8-18': {
  const len = view.getUint16(offset, false);
  offset += 2;
  const bitmap = [];
  for (let i = 0; i < len; i++) bitmap.push(view.getUint8(offset++));
  result.flood_bitmap_compressed = bitmap;  // Raw compressed bytes
  break;
}
```

### Required Decoding Steps

To properly decode the bitmap on the dashboard, you need to:

1. **Extract the compressed data** (already done by ChirpStack codec)

2. **Parse the header**:
   ```javascript
   const method = bitmap[0];
   const width = (bitmap[1] << 8) | bitmap[2];  // Big-endian
   const height = (bitmap[3] << 8) | bitmap[4];  // Big-endian
   const payload = bitmap.slice(5);  // Compressed data
   ```

3. **Decompress using Brotli**:
   - Use a JavaScript Brotli decompressor library (e.g., `brotli-wasm` or `brotli/decompress`)
   - Decompress the payload bytes

4. **Reconstruct the binary image**:
   
   **If method == 0 (Bitpacking)**:
   ```javascript
   const bytesPerRow = Math.ceil(width / 8);
   const binaryArray = [];
   for (let row = 0; row < height; row++) {
     const rowStart = row * bytesPerRow;
     const rowBytes = decompressed.slice(rowStart, rowStart + bytesPerRow);
     for (let col = 0; col < width; col++) {
       const byteIdx = Math.floor(col / 8);
       const bitIdx = 7 - (col % 8);
       const bit = (rowBytes[byteIdx] >> bitIdx) & 1;
       binaryArray.push(bit);
     }
   }
   ```

   **If method == 1 (RLE)**:
   ```javascript
   const binaryArray = [];
   for (let i = 0; i < decompressed.length; i += 2) {
     const value = decompressed[i];
     const count = decompressed[i + 1];
     for (let j = 0; j < count; j++) {
       binaryArray.push(value);
     }
   }
   ```

5. **Create image for display**:
   ```javascript
   // Reshape to 2D array
   const imageData = [];
   for (let y = 0; y < height; y++) {
     const row = [];
     for (let x = 0; x < width; x++) {
       const idx = y * width + x;
       const pixel = binaryArray[idx] * 255;  // 0 or 255
       row.push([pixel, pixel, pixel, 255]);  // RGBA
     }
     imageData.push(row);
   }
   
   // Convert to ImageData or Canvas for display
   ```

### Python Decompression Reference

The Python decompression function (`decompress()` in `compress_segmented.py`) provides a reference implementation:

```python
def decompress(data):
    method = data[0]
    width = int.from_bytes(data[1:3], 'big')
    height = int.from_bytes(data[3:5], 'big')
    payload = data[5:]
    
    if method == 0:
        # Bitpacking method
        raw = brotli.decompress(payload)
        bpr = (width + 7) // 8  # bytes per row
        arr = np.zeros((height, width), np.uint8)
        for i in range(height):
            row = np.unpackbits(np.frombuffer(raw[i*bpr:(i+1)*bpr], np.uint8))[:width]
            arr[i] = row
        return arr
    elif method == 1:
        # RLE method
        raw = brotli.decompress(payload)
        flat = []
        i = 0
        while i < len(raw):
            v, count = raw[i], raw[i+1]
            flat.extend([v] * count)
            i += 2
        arr = np.array(flat, np.uint8).reshape((height, width))
        return arr
```

## Implementation Recommendations

### For Dashboard Implementation

1. **Add Brotli decompression library**:
   - Install: `npm install brotli` or use `brotli-wasm` for browser compatibility
   - Or use a WebAssembly version for better performance

2. **Create a decode function**:
   ```javascript
   function decodeFloodBitmap(compressedBytes) {
     // Parse header
     const method = compressedBytes[0];
     const width = (compressedBytes[1] << 8) | compressedBytes[2];
     const height = (compressedBytes[3] << 8) | compressedBytes[4];
     const payload = compressedBytes.slice(5);
     
     // Decompress
     const decompressed = brotli.decompress(payload);
     
     // Reconstruct image based on method
     // ... (see steps above)
     
     return { width, height, imageData };
   }
   ```

3. **Update ChirpStack codec** (optional):
   - You can add decompression directly in the codec, or
   - Keep raw bytes and decompress in the dashboard application

### Size Constraints

- **Maximum compressed size**: 228 bytes (with TT token) or 242 bytes (without TT token)
- **Typical image dimensions**: 32x32 to ~160x160 pixels (varies based on content)
- **Compression ratio**: Typically achieves 10-50x compression depending on image complexity

## Testing

To test decompression, you can:

1. **Use the Python decompression function**:
   ```python
   from tools.compress_segmented import compress_image, decompress
   
   # Compress
   result = compress_image("path/to/segmentation.png")
   compressed = result['compressed_data']
   
   # Decompress
   arr = decompress(compressed)
   
   # Verify
   assert np.array_equal(arr, expected_array)
   ```

2. **Verify in JavaScript**:
   - Use the same compressed bytes from Python
   - Decompress using JavaScript Brotli library
   - Compare results

## Summary

- **Compression**: Binary thresholding → resize → bitpack/RLE → Brotli compression
- **Transmission**: Channel 0x08, Type 0x18, TLV format with length prefix
- **Decoding**: Parse header → Brotli decompress → reconstruct binary image → display
- **Key Library**: Brotli decompressor (JavaScript implementation needed for dashboard)

