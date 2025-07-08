#!/usr/bin/env python3
"""
Compressor for segmented binary classified images, targeting <=242 bytes output for LoRa transmission
"""
import sys
import numpy as np
from PIL import Image
import brotli
from pathlib import Path

# --- Utility functions ---
def to_binary(img: Image.Image) -> np.ndarray:
    arr = np.array(img.convert('L'))
    # Otsu's method or mean threshold
    threshold = np.mean(arr) if arr.std() < 1e-3 else otsu(arr)
    return (arr > threshold).astype(np.uint8)

def otsu(arr):
    hist, bins = np.histogram(arr.flatten(), bins=256, range=(0,256))
    total = arr.size
    sumB, wB, maximum, sum1, threshold = 0., 0., 0., np.dot(np.arange(256), hist), 0
    for t in range(256):
        wB += hist[t]
        if wB == 0: continue
        wF = total - wB
        if wF == 0: break
        sumB += t * hist[t]
        mB, mF = sumB / wB, (sum1 - sumB) / wF
        between = wB * wF * (mB - mF) ** 2
        if between > maximum:
            threshold, maximum = t, between
    return threshold

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

def rle(arr):
    flat = arr.flatten()
    out = bytearray()
    prev, count = flat[0], 1
    for v in flat[1:]:
        if v == prev and count < 255:
            count += 1
        else:
            out.extend([prev, count])
            prev, count = v, 1
    out.extend([prev, count])
    return bytes(out)

def compress_methods(arr):
    # Returns (compressed, method_id)
    bitpacked = bitpack_rows(arr)
    c1 = brotli.compress(bitpacked)  # default quality
    c2 = brotli.compress(rle(arr))
    if len(c1) <= len(c2):
        return c1, 0
    else:
        return c2, 1

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
        raw = brotli.decompress(payload)
        flat = []
        i = 0
        while i < len(raw):
            v, count = raw[i], raw[i+1]
            flat.extend([v]*count)
            i += 2
        arr = np.array(flat, np.uint8).reshape((height, width))
        return arr
    else:
        raise ValueError('Unknown method')

def find_best_size(img, max_bytes, min_size=32):
    low, high = min_size, 160
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

def compress_image(input_path, max_bytes=242, min_size=32, output_path=None, save_images=True):
    """
    Compress an image to binary format with size constraint.
    
    Args:
        input_path (str or Path): Path to input image
        max_bytes (int): Maximum compressed size in bytes (default: 242)
        min_size (int): Minimum image dimension (default: 32)
        output_path (str or Path, optional): Path for compressed output file
        save_images (bool): Whether to save intermediate images (default: True)
    
    Returns:
        dict: Dictionary containing compression results:
            - 'success' (bool): Whether compression was successful
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
    result = find_best_size(img, max_bytes, min_size)
    
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
    parser.add_argument('--max-bytes', type=int, default=242)
    parser.add_argument('--min-size', type=int, default=32)
    parser.add_argument('--output', default=None)
    parser.add_argument('--no-images', action='store_true', help='Skip saving intermediate images')
    args = parser.parse_args()

    result = compress_image(
        input_path=args.input,
        max_bytes=args.max_bytes,
        min_size=args.min_size,
        output_path=args.output,
        save_images=not args.no_images
    )
    
    if result['success']:
        print(f"Success at {result['width']}x{result['height']}: {result['total_size']} bytes")
        if not args.no_images:
            print('Decompression verified.')
    else:
        print('Could not compress to target size.')

if __name__ == '__main__':
    main() 
