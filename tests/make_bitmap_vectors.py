"""Write tests/fixtures/bitmap_vectors.json: bitmaps from the node's encoders
(methods 0, 1, 2) of synthetic masks, with the pixels they must decode to.

The same file is committed in the API repo (tests/fixtures/), where the Python
decoder (app/bitmap.py) and the dashboard's JavaScript decoder are tested
against it. Regenerate after an encoder change and copy it across:

    python tests/make_bitmap_vectors.py

Each vector: name, method, width, height, bitmap_hex (header + payload),
water (pixel count), pixels_sha256 (of one 0/1 byte a pixel, raster order) and,
for small ones, pixels_bitpacked_hex (rows bit-packed MSB first, padded to bytes).
"""
import hashlib
import json
import os
import sys

import brotli
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tools import bitmap_cm  # noqa: E402
from tools.compress_segmented import bitpack_rows, minimal_header, rle  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "bitmap_vectors.json")


def smooth(h, w, seed):
    """A mask with ragged, curved water edges and a few specks."""
    rs = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    field = (np.sin(xx / (w / 5.0 + 1) + seed) + np.cos(yy / (h / 4.0 + 1)) +
             0.4 * np.sin((xx + 2 * yy) / 7.0) + 0.25 * rs.rand(h, w))
    return (field > 0.5).astype(np.uint8)


def payload(arr, method):
    if method == 0:
        return brotli.compress(bitpack_rows(arr))
    if method == 1:
        return brotli.compress(rle(arr))
    return bitmap_cm.encode(arr)


def vector(name, arr, method):
    arr = np.ascontiguousarray(arr, np.uint8)
    h, w = arr.shape
    data = minimal_header(w, h, method) + payload(arr, method)
    v = {"name": name, "method": method, "width": w, "height": h,
         "bitmap_hex": data.hex(), "water": int(arr.sum()),
         "pixels_sha256": hashlib.sha256(arr.tobytes()).hexdigest()}
    if w * h <= 160 * 120:
        v["pixels_bitpacked_hex"] = bitpack_rows(arr).hex()
    return v


def vectors():
    rs = np.random.RandomState(7)
    yy, xx = np.mgrid[0:120, 0:160]
    disc = (((xx - 60) ** 2 + (yy - 70) ** 2) < 45 ** 2).astype(np.uint8)
    out = [
        vector("m2 all dry 64x48", np.zeros((48, 64)), 2),
        vector("m2 all water 160x120", np.ones((120, 160)), 2),
        vector("m2 1x1 water", np.ones((1, 1)), 2),
        vector("m2 1x1 dry", np.zeros((1, 1)), 2),
        vector("m2 random 7x5", rs.randint(0, 2, (5, 7)), 2),
        vector("m2 random 13x50", rs.randint(0, 2, (50, 13)), 2),
        vector("m2 disc 160x120", disc, 2),
        vector("m2 smooth 157x93", smooth(93, 157, 1), 2),
        vector("m2 smooth 333x251", smooth(251, 333, 2), 2),
        vector("m2 smooth 640x480", smooth(480, 640, 3), 2),
        vector("m2 noise 320x240", (rs.rand(240, 320) > 0.7), 2),
        vector("m0 smooth 157x93", smooth(93, 157, 1), 0),
        vector("m1 smooth 157x93", smooth(93, 157, 1), 1),
        vector("m1 all water 160x120", np.ones((120, 160)), 1),
        vector("m0 full-res smooth 1296x972", smooth(972, 1296, 4), 0),
        vector("m1 full-res smooth 1296x972", smooth(972, 1296, 4), 1),
    ]
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"comment": "Synthetic bitmap test vectors from SU-WaterCam tests/make_bitmap_vectors.py; "
                              "spec docs/BITMAP_DECODING_SPEC.md",
                   "vectors": vectors()}, f, indent=1)
        f.write("\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
