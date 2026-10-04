# Flood Bitmap Format — Decoding Specification

This document specifies how to **decode** flood detection bitmaps produced by SU-WaterCam so another agent or service can implement a decoder without reading the source.

---

## 1. Where the bitmap appears

- **Over LoRa (TLV payload)**: The bitmap is sent as a **blob** with channel `0x08` and type `0x18`.
- **Blob layout**: `[Channel: 1 byte][Type: 1 byte][Length: 2 bytes, big-endian][Data: Length bytes]`
- **Data** (the bytes after the 2-byte length) are the **compressed bitmap** described below. The TLV layer does not change the format of `Data`; it only adds the 4-byte prefix.

So after you strip the TLV prefix (or read the blob payload), you have the **compressed bitmap** byte string. Decoding that is described in §2 and §3.

---

## 2. Compressed bitmap layout (byte string)

The **compressed bitmap** is a single byte string with this layout:

| Offset | Size   | Name    | Description |
|--------|--------|--------|-------------|
| 0      | 1 byte | method | Compression method: `0` = bitpacking, `1` = RLE. |
| 1      | 2 bytes| width  | Image width in pixels, **big-endian** unsigned 16-bit. |
| 3      | 2 bytes| height | Image height in pixels, **big-endian** unsigned 16-bit. |
| 5      | N bytes| payload| Brotli-compressed payload (see §3). |

- **Total length**: `5 + len(payload)` bytes.  
- **Semantics**: The decoded image is a **binary** (two-class) mask: each pixel is 0 (e.g. land) or 1 (e.g. water/flood). Dimensions are `width × height` (columns × rows).

---

## 3. Payload format by method

After Brotli-decompressing the **payload** (bytes from offset 5 to end), you get **raw** bytes. Their layout depends on **method**.

### 3.1 Method 0 — Bitpacking

- **Layout**: Row-major. Each row is packed into bits, MSB first within each byte, 8 pixels per byte.  
- **Bytes per row**: `bpr = (width + 7) // 8` (ceiling of width/8).  
- **Total raw size**: `bpr * height` bytes.

**Decoding:**

1. Decompress `payload` with Brotli → `raw` (bytes).
2. For each row `y` in `0 .. height-1`:
   - Row bytes: `raw[y * bpr : (y+1) * bpr]`.
   - For each column `x` in `0 .. width-1`:
     - Byte index in row: `byteIdx = x // 8`.
     - Bit index in byte (MSB first): `bitIdx = 7 - (x % 8)`.
     - Pixel: `(rowBytes[byteIdx] >> bitIdx) & 1`.
3. Output a 2D array `pixels[height][width]` with values 0 or 1.

**Reference (Python, matches `tools/compress_segmented.decompress`):**

```python
bpr = (width + 7) // 8
for i in range(height):
    row_bytes = raw[i * bpr : (i + 1) * bpr]
    row_bits = np.unpackbits(np.frombuffer(row_bytes, dtype=np.uint8))[:width]
    arr[i] = row_bits
```

### 3.2 Method 1 — RLE (run-length encoding)

- **Layout**: Flat run-length pairs. Each pair is `[value: 1 byte][count: 1 byte]`. Values are 0 or 1. Runs are concatenated in row-major order (row 0, then row 1, …).
- **Total pixels**: Must equal `width * height`.

**Decoding:**

1. Decompress `payload` with Brotli → `raw` (bytes).
2. Expand runs: `i = 0`; while `i < len(raw)`: `value = raw[i]`, `count = raw[i+1]`; append `value` repeated `count` times; `i += 2`.
3. Reshape the flat list into `height` rows of `width` pixels: row-major, so first `width` values are row 0, next `width` are row 1, etc.

**Reference (Python):**

```python
flat = []
i = 0
while i < len(raw):
    value, count = raw[i], raw[i + 1]
    flat.extend([value] * count)
    i += 2
arr = np.array(flat, dtype=np.uint8).reshape((height, width))
```

---

## 4. End-to-end decoding steps

1. **Get the blob payload**  
   From your LoRa/API payload, locate the blob with channel `0x08` and type `0x18`. The blob **value** is the compressed bitmap (no TLV prefix in this value).

2. **Parse header**  
   - `method = data[0]`  
   - `width = (data[1] << 8) | data[2]`  (big-endian)  
   - `height = (data[3] << 8) | data[4]` (big-endian)  
   - `payload = data[5:]`

3. **Brotli-decompress**  
   - `raw = brotli_decompress(payload)`  
   - Use a Brotli library for your language (e.g. `brotli` in Python, `brotli` or `brotli-wasm` in JS).

4. **Decode to binary image**  
   - If `method == 0`: use bitpacking (§3.1).  
   - If `method == 1`: use RLE (§3.2).  
   - Result: 2D array of shape `(height, width)` with values 0 or 1.

5. **Display or export**  
   - 0 = one class (e.g. background/land), 1 = other (e.g. flood/water).  
   - Typical display: map 0 → black, 1 → white (or color) and render as image.

---

## 5. Constraints and notes

- **Size**: Compressed bitmap (header + payload) is usually ≤ 228 bytes when sent with a TT token, or ≤ 242 bytes otherwise. TLV adds 4 bytes (channel + type + length).
- **Dimensions**: Width and height are at least 32 and at most 160; they come from the encoder and are stored in the 5-byte header.
- **Byte order**: All multi-byte fields (width, height, and the 2-byte length in TLV) are **big-endian**.
- **Reference implementation**: Python decoder is in `tools/compress_segmented.decompress()`; encoder is `compress_image()` in the same file.

---

## 6. Summary for implementers

| Layer        | Format |
|-------------|--------|
| TLV (LoRa)  | `08 18 [len_hi] [len_lo] [compressed_bitmap...]` |
| Compressed  | `[method 1B][width 2B BE][height 2B BE][brotli_payload]` |
| Method 0    | Brotli payload = bitpacked rows, 8 px/byte, MSB first |
| Method 1    | Brotli payload = RLE pairs (value, count), row-major |

Decoding: strip TLV → read method, width, height, payload → Brotli decompress → interpret payload by method → reshape to `(height, width)` binary image.
