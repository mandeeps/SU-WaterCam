"""Bitmap method 2: lossless context-model coding of a 1-bit water mask.

JBIG-style: each pixel, in raster order, is coded with an adaptive binary range
coder under a probability that depends on 10 already-coded neighbours (the
JBIG1 three-line template). Smooth masks cost a fraction of a bit a pixel;
about 0.6x the size of bit-packed or RLE rows + Brotli (methods 0 and 1) on
real masks, and never much larger. docs/BITMAP_DECODING_SPEC.md is the spec;
the API (app/bitmap.py) and the dashboard decode it.

Stream (after the usual 5 B header: method 2, width u16, height u16):
  - pixels in raster order, 1 = water; neighbours outside the image read as 0;
  - context, MSB first: row y-2 x-1..x+1, row y-1 x-2..x+2, row y x-2, x-1;
  - per context counts n0, n1 (start 0), p0 = ((4*n0 + 1) << 16) // (4*(n0+n1) + 2),
    after coding a pixel its count goes up and, once n0+n1 > 1024, both are halved
    rounding up ((n + 1) >> 1);
  - LZMA-style binary range coder, 32-bit range, bound = (range >> 16) * p0,
    0 takes [low, low+bound), renormalise while range < 2**24;
  - the coder's always-zero first byte is not stored, and trailing zero bytes are
    dropped: the decoder reads zeros past the end. An all-dry mask has no payload.

Integer-only, so the decoders (Python, JavaScript) reproduce it exactly.
"""
import numpy as np

PROB_BITS = 16
TOP = 1 << 24
MASK32 = 0xFFFFFFFF
D = 4
LIMIT = 1024
NCTX = 1 << 10


class TooLarge(Exception):
    """The payload would exceed the byte limit given to encode()."""


#: Template offsets (dx, dy), MSB first
TEMPLATE = ((-1, -2), (0, -2), (1, -2),
            (-2, -1), (-1, -1), (0, -1), (1, -1), (2, -1),
            (-2, 0), (-1, 0))
#: Rows a block of contexts is computed for at a time, so that an encode that
#: gives up early (max_bytes) hasn't computed the contexts of the whole image.
BLOCK_ROWS = 32


def _context_blocks(arr):
    """(contexts, bits) lists for consecutive blocks of rows, raster order. The
    encoder knows the whole image, so numpy computes a block's contexts at once."""
    h, w = arr.shape
    p = np.zeros((h + 2, w + 4), np.int32)
    p[2:, 2:2 + w] = arr
    for y0 in range(0, h, BLOCK_ROWS):
        rows = min(BLOCK_ROWS, h - y0)
        ctx = np.zeros((rows, w), np.int32)
        for dx, dy in TEMPLATE:
            ctx = (ctx << 1) | p[2 + y0 + dy:2 + y0 + dy + rows, 2 + dx:2 + dx + w]
        yield ctx.ravel().tolist(), arr[y0:y0 + rows].ravel().tolist()


def encode(arr, max_bytes=None):
    """Payload bytes for a (H, W) 0/1 array. With max_bytes, raises TooLarge as soon
    as the payload is certain to be longer, which keeps a size search cheap."""
    arr = np.asarray(arr, dtype=np.uint8)
    n0 = [0] * NCTX
    n1 = [0] * NCTX
    low = 0
    rng = MASK32
    cache = 0
    cache_size = 1
    out = bytearray()
    # A nonzero byte past this index of `out` (whose byte 0 is dropped) can't be
    # stripped as trailing zeros, so the payload is then longer than max_bytes.
    abort_at = None if max_bytes is None else max_bytes + 1
    for ctxs, bits in _context_blocks(arr):
        for c, b in zip(ctxs, bits):
            a = n0[c]
            t = a + n1[c]
            # 16 <= p0 < 2**16 for D=4, LIMIT=1024 (t <= 1024), so no clamping is needed
            bound = (rng >> PROB_BITS) * (((D * a + 1) << PROB_BITS) // (D * t + 2))
            if b:
                low += bound
                rng -= bound
                n1[c] += 1
            else:
                rng = bound
                n0[c] = a + 1
            if t >= LIMIT:
                n0[c] = (n0[c] + 1) >> 1
                n1[c] = (n1[c] + 1) >> 1
            while rng < TOP:
                rng = (rng << 8) & MASK32
                # shift_low
                if low < 0xFF000000 or low > MASK32:
                    carry = low >> 32
                    temp = cache
                    while True:
                        byte = (temp + carry) & 0xFF
                        out.append(byte)
                        if abort_at is not None and byte and len(out) > abort_at:
                            raise TooLarge()
                        temp = 0xFF
                        cache_size -= 1
                        if cache_size == 0:
                            break
                    cache = (low >> 24) & 0xFF
                cache_size += 1
                low = (low & 0x00FFFFFF) << 8
    # Flush: the value in [low, low + rng) with the most trailing zero bits, so
    # that the most bytes can be dropped.
    lo, hi = low, low + rng
    for k in range(40, -1, -1):
        m = (1 << k) - 1
        v = (lo + m) & ~m
        if v < hi:
            break
    low = v
    for _ in range(5):
        if low < 0xFF000000 or low > MASK32:
            carry = low >> 32
            temp = cache
            while True:
                out.append((temp + carry) & 0xFF)
                temp = 0xFF
                cache_size -= 1
                if cache_size == 0:
                    break
            cache = (low >> 24) & 0xFF
        cache_size += 1
        low = (low & 0x00FFFFFF) << 8
    payload = bytes(out[1:]).rstrip(b'\x00')
    if max_bytes is not None and len(payload) > max_bytes:
        raise TooLarge()
    return payload


def decode(data, width, height):
    """(H, W) uint8 0/1 array from a method 2 payload."""
    data = bytes(data)
    n = len(data)
    pos = 4
    code = int.from_bytes((data[:4] + b'\x00\x00\x00\x00')[:4], 'big')
    rng = MASK32
    n0 = [0] * NCTX
    n1 = [0] * NCTX
    pad = [0] * 4
    r2 = [0] * (width + 6)          # row y-2, two zeros left, four right
    r1 = [0] * (width + 6)          # row y-1
    rows = bytearray()
    for _y in range(height):
        cur = [0, 0]
        c2 = (r2[2] << 1) | r2[3]
        c1 = (r1[2] << 2) | (r1[3] << 1) | r1[4]
        c0 = 0
        for x in range(width):
            c = (c2 << 7) | (c1 << 2) | c0
            a = n0[c]
            t = a + n1[c]
            bound = (rng >> PROB_BITS) * (((D * a + 1) << PROB_BITS) // (D * t + 2))
            if code < bound:
                rng = bound
                b = 0
                n0[c] = a + 1
            else:
                code -= bound
                rng -= bound
                b = 1
                n1[c] += 1
            if t >= LIMIT:
                n0[c] = (n0[c] + 1) >> 1
                n1[c] = (n1[c] + 1) >> 1
            while rng < TOP:
                rng = (rng << 8) & MASK32
                code = ((code << 8) | (data[pos] if pos < n else 0)) & MASK32
                pos += 1
            cur.append(b)
            c2 = ((c2 << 1) & 7) | r2[x + 4]
            c1 = ((c1 << 1) & 31) | r1[x + 5]
            c0 = ((c0 << 1) & 3) | b
        rows.extend(cur[2:])
        cur.extend(pad)
        r2, r1 = r1, cur
    return np.frombuffer(bytes(rows), np.uint8).reshape((height, width))
