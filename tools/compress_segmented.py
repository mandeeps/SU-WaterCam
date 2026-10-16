#!/usr/bin/env python3
"""
Compressor for segmented binary classified images, targeting <=228 bytes output for LoRa transmission - TT token header is 14 bytes so 228 is the limit for the bitmap. If sending without a TT token, the limit is 242 bytes
"""
import sys
import numpy as np
from PIL import Image
import brotli
from pathlib import Path

try:
    from tools import bitmap_cm
except ImportError:  # run as a script from tools/ (tools/watercam.py)
    import bitmap_cm

# --- Utility functions ---
#: Masks are single-channel 0/255, water bright (tools/segformer_daemon.py).
#: A fixed threshold, not Otsu or the mean: those split a uniform image at its
#: own value, so an all-water mask came out as no water at all.
WATER_THRESHOLD = 127
#: Share of pixels other than 0 and 255 above which an image is not a mask.
MAX_INTERMEDIATE_FRACTION = 0.01
MASK_MODES = ('L', '1')

def mask_format_error(img: Image.Image):
    """Why img is not a binary water mask (a string), or None if it is one.

    Refuses colour images (the older RGB/RGBA colour masks, whose water is not
    the bright class) and greyscale images with many values between 0 and 255
    (a photo, or class indices 0/1 that the threshold would read as dry)."""
    if img.mode not in MASK_MODES:
        return f"mask must be single-channel 0/255 (mode L), got mode {img.mode}"
    if img.mode == '1':
        return None
    arr = np.asarray(img)
    if arr.size == 0:
        return "mask is empty"
    intermediate = np.count_nonzero((arr != 0) & (arr != 255)) / arr.size
    if intermediate > MAX_INTERMEDIATE_FRACTION:
        return (f"mask is not binary: {intermediate:.1%} of pixels are neither 0 nor 255")
    return None

def to_binary(img: Image.Image) -> np.ndarray:
    """1 where water (above WATER_THRESHOLD), 0 elsewhere."""
    arr = np.array(img.convert('L'))
    return (arr > WATER_THRESHOLD).astype(np.uint8)

def water_percent(img: Image.Image) -> int:
    """Share of the mask that is water, 0-100 (rounded)."""
    return int(round(100.0 * float(to_binary(img).mean())))

def resize_keep_aspect(img, target):
    img = img.copy()
    img.thumbnail(target, Image.Resampling.LANCZOS)
    return img

def bitpack_rows(arr):
    # arr: (H,W) uint8 0/1
    H, W = arr.shape
    bpr = (W+7)//8
    packed = [np.packbits(arr[i])[:bpr].tobytes() for i in range(H)]
    return b''.join(packed)

#: Long side cap of the LoRa bitmap: 160 px for methods 0/1 (what every decoder
#: has accepted so far), 640 px once method 2 is enabled (runtime parameter
#: bitmap_method2_enabled, see docs/BITMAP_DECODING_SPEC.md).
MAX_SIZE = 160
MAX_SIZE_METHOD2 = 640
#: Largest full-resolution bitmap the IP uplink sends (channel 08 18). The TLV
#: length is u16, and the API stores the bitmap as hex in one InfluxDB string
#: field (64 KiB), so a bitmap must stay under 32 KiB; real masks measured up to
#: ~28 kB. A larger one falls back to the LoRa-sized bitmap.
FULL_RES_MAX_BYTES = 32000
#: Brotli quality of the full-resolution bitmap. 11 (the default) is ~12x slower
#: at 1296x972 (~0.3 s on a workstation, a few seconds on a Pi) for ~25 % fewer
#: bytes (5.9 vs 8.0 kB median on real masks). Decoders don't depend on it.
FULL_RES_BROTLI_QUALITY = 9

def rle(arr):
    """(value, count) byte pairs, count 1-255, row-major (method 1, before Brotli).
    Vectorised: the per-pixel loop took seconds on a full-resolution mask."""
    flat = np.asarray(arr, np.uint8).ravel()
    starts = np.concatenate(([0], np.flatnonzero(flat[1:] != flat[:-1]) + 1))
    lengths = np.diff(np.append(starts, flat.size))
    # A run longer than 255 is split into pairs of 255 and the remainder
    pairs = (lengths + 254) // 255
    ends = np.cumsum(pairs) - 1
    counts = np.full(int(pairs.sum()), 255, np.uint8)
    counts[ends] = lengths - 255 * (pairs - 1)
    out = np.empty(2 * counts.size, np.uint8)
    out[0::2] = np.repeat(flat[starts], pairs)
    out[1::2] = counts
    return out.tobytes()

def compress_brotli(arr, quality=11):
    """(compressed, method) for the smaller of methods 0 and 1."""
    c0 = brotli.compress(bitpack_rows(arr), quality=quality)  # 11 is the default
    c1 = brotli.compress(rle(arr), quality=quality)
    if len(c0) <= len(c1):
        return c0, 0
    return c1, 1

def compress_methods(arr, method2=False):
    """(compressed, method_id): the smallest of methods 0, 1 and, with method2,
    2 (tools/bitmap_cm.py). Method 2 is only kept when strictly smaller, and its
    encoder gives up as soon as it can't be, so trying it costs little."""
    comp, method = compress_brotli(arr)
    if method2:
        try:
            return bitmap_cm.encode(arr, max_bytes=len(comp) - 1), 2
        except bitmap_cm.TooLarge:
            pass
    return comp, method

def minimal_header(width, height, method):
    # 1 byte method, 2 bytes width, 2 bytes height (big endian)
    return bytes([method]) + width.to_bytes(2,'big') + height.to_bytes(2,'big')

def decompress(data):
    method = data[0]
    width = int.from_bytes(data[1:3],'big')
    height = int.from_bytes(data[3:5],'big')
    payload = data[5:]
    if method == 0:
        raw = brotli.decompress(payload)
        bpr = (width+7)//8
        arr = np.zeros((height, width), np.uint8)
        for i in range(height):
            row = np.unpackbits(np.frombuffer(raw[i*bpr:(i+1)*bpr], np.uint8))[:width]
            arr[i] = row
        return arr
    elif method == 1:
        raw = np.frombuffer(brotli.decompress(payload), np.uint8)
        flat = np.repeat(raw[0::2], raw[1::2])
        return flat.reshape((height, width))
    elif method == 2:
        return bitmap_cm.decode(payload, width, height)
    else:
        raise ValueError('Unknown method')

def find_best_size(img, max_bytes, min_size=32, method2=False):
    """(arr, compressed, method, header) at the largest long side that fits
    max_bytes, or None. Without method2 exactly as before: methods 0/1, at most
    MAX_SIZE px. With method2: methods 0-2, at most MAX_SIZE_METHOD2 px."""
    if method2:
        return _find_best_size_method2(img, max_bytes, min_size)
    low, high = min_size, MAX_SIZE
    best = None
    while low <= high:
        mid = (low + high) // 2
        img_resized = resize_keep_aspect(img, (mid, mid))
        arr_bin = to_binary(img_resized)
        comp, method = compress_methods(arr_bin)
        header = minimal_header(arr_bin.shape[1], arr_bin.shape[0], method)
        total = len(header) + len(comp)
        if total <= max_bytes:
            best = (arr_bin, comp, method, header)
            low = mid + 1
        else:
            high = mid - 1
    return best

def _find_best_size_method2(img, max_bytes, min_size):
    # The same binary search, with cheaper probes. A probe only asks whether
    # anything fits: method 2 first, with the budget as its limit so that it
    # stops as soon as it is over, then RLE + Brotli, the one that can beat it
    # (uniform scenes at large sizes). Bit-packed Brotli is left out of the
    # probes: at 640 px it takes longer than the rest of a probe, and method 2
    # is smaller wherever it would fit. The full cap is tried first, as smooth
    # and uniform scenes fit there; above 256 px the search stops within ~3 %.
    # Then the smallest of all methods at the size found.
    budget = max_bytes - 5
    probes = {}

    def probe(size):
        if size not in probes:
            arr = to_binary(resize_keep_aspect(img, (size, size)))
            try:
                found = (bitmap_cm.encode(arr, max_bytes=budget), 2)
            except bitmap_cm.TooLarge:
                comp = brotli.compress(rle(arr))
                found = (comp, 1) if len(comp) <= budget else None
            probes[size] = (arr, found)
        return probes[size][1] is not None

    if probe(MAX_SIZE_METHOD2):
        size = MAX_SIZE_METHOD2
    else:
        size = None
        low, high = min_size, MAX_SIZE_METHOD2 - 1
        while low <= high:
            if size is not None and size >= 256 and high - low < size // 32:
                break
            mid = (low + high) // 2
            if probe(mid):
                size = mid
                low = mid + 1
            else:
                high = mid - 1
        if size is None:
            return None
    arr, (comp, method) = probes[size]
    c, m = compress_brotli(arr)
    if len(c) < len(comp) or (len(c) == len(comp) and method == 2):
        comp, method = c, m
    elif method != 2:
        try:
            comp, method = bitmap_cm.encode(arr, max_bytes=len(comp) - 1), 2
        except bitmap_cm.TooLarge:
            pass
    return arr, comp, method, minimal_header(arr.shape[1], arr.shape[0], method)

def compress_full_resolution(input_path, max_bytes=FULL_RES_MAX_BYTES):
    """The mask at its own resolution, lossless, for the IP uplink: header +
    methods 0/1 (Brotli; method 2 is too slow at full resolution on a Pi).
    None when it is not a binary mask or the result is over max_bytes."""
    with Image.open(input_path) as img:
        if mask_format_error(img):
            return None
        arr = to_binary(img)
    h, w = arr.shape
    if w > 0xFFFF or h > 0xFFFF:
        return None
    comp, method = compress_brotli(arr, quality=FULL_RES_BROTLI_QUALITY)
    data = minimal_header(w, h, method) + comp
    return data if len(data) <= max_bytes else None

def compress_image(input_path, max_bytes=228, min_size=32, output_path=None, save_images=False,
                   method2=False):
    """
    Compress an image to binary format with size constraint.
    
    Args:
        input_path (str or Path): Path to input image
        max_bytes (int): Maximum compressed size in bytes (default: 228)
        min_size (int): Minimum image dimension (default: 32)
        output_path (str or Path, optional): Path for compressed output file
        save_images (bool): Whether to save intermediate images (default: True)
        method2 (bool): Also try method 2 and allow up to 640 px (runtime
            parameter bitmap_method2_enabled; only once the API decodes it)
    
    Returns:
        dict: Dictionary containing compression results:
            - 'success' (bool): Whether compression was successful
            - 'error' (str): Why the image was refused (not a binary water mask)
            - 'compressed_data' (bytes): The compressed data (if successful)
            - 'width' (int): Final image width (if successful)
            - 'height' (int): Final image height (if successful)
            - 'method' (int): Compression method used (if successful)
            - 'total_size' (int): Total compressed size (if successful)
            - 'used_image_path' (str): Path to saved used image (if save_images=True)
            - 'decompressed_image_path' (str): Path to saved decompressed image (if save_images=True)
    """
    input_path = Path(input_path)
    
    # Load and compress image
    img = Image.open(input_path)
    error = mask_format_error(img)
    if error:
        print(f"⚠️ {input_path}: {error}: not compressed")
        return {'success': False, 'error': error}
    result = find_best_size(img, max_bytes, min_size, method2=method2)
    
    if not result:
        return {'success': False}
    
    arr_bin, comp, method, header = result
    total_size = len(header) + len(comp)
    
    # Determine output directory (same as input file)
    output_dir = input_path.parent
    
    # Generate output paths
    if output_path is None:
        output_path = output_dir / f"{input_path.stem}_compressed.bin"
    else:
        output_path = Path(output_path)
    
    # Save compressed file
    with open(output_path, 'wb') as f:
        f.write(header + comp)
    
    result_dict = {
        'success': True,
        'compressed_data': header + comp,
        'width': arr_bin.shape[1],
        'height': arr_bin.shape[0],
        'method': method,
        'total_size': total_size,
        'compressed_file_path': str(output_path)
    }
    
    if save_images:
        # Save used image and decompressed image to same directory
        used_path = output_dir / f"{input_path.stem}_used_for_compression.png"
        decompressed_path = output_dir / f"{input_path.stem}_decompressed.png"
        
        Image.fromarray(arr_bin*255).save(used_path)
        
        # Verify decompression
        arr2 = decompress(header + comp)
        assert np.array_equal(arr_bin, arr2), 'Decompression mismatch!'
        Image.fromarray(arr2*255).save(decompressed_path)
        
        result_dict['used_image_path'] = str(used_path)
        result_dict['decompressed_image_path'] = str(decompressed_path)
    
    return result_dict

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('input')
    parser.add_argument('--max-bytes', type=int, default=228)
    parser.add_argument('--min-size', type=int, default=32)
    parser.add_argument('--output', default=None)
    parser.add_argument('--no-images', action='store_true', help='Skip saving intermediate images')
    parser.add_argument('--method2', action='store_true', help='Also try method 2 (context model), up to 640 px')
    args = parser.parse_args()

    result = compress_image(
        input_path=args.input,
        max_bytes=args.max_bytes,
        min_size=args.min_size,
        output_path=args.output,
        save_images=not args.no_images,
        method2=args.method2,
    )
    
    if result['success']:
        print(f"Success at {result['width']}x{result['height']}: {result['total_size']} bytes")
        if not args.no_images:
            print('Decompression verified.')
    else:
        print(result.get('error') or 'Could not compress to target size.')

if __name__ == '__main__':
    main() 
