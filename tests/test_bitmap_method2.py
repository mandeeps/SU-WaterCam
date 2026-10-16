"""Bitmap method 2 (tools/bitmap_cm.py), its gate (bitmap_method2_enabled) and
the full-resolution IP bitmap (tools/compress_segmented.compress_full_resolution).

With the gate off the LoRa bitmap must be exactly what it was before: methods
0/1, long side at most 160.
"""
import hashlib
import json
import os
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from tools import bitmap_cm  # noqa: E402
from tools import compress_segmented as cs  # noqa: E402

VECTORS = os.path.join(os.path.dirname(__file__), "fixtures", "bitmap_vectors.json")


def _smooth(h, w, seed=0):
    rs = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    field = np.sin(xx / (w / 5.0 + 1)) + np.cos(yy / (h / 4.0 + 1)) + 0.2 * rs.rand(h, w)
    return (field > 0.5).astype(np.uint8)


def _mask_image(arr):
    return Image.fromarray((np.asarray(arr, np.uint8) * 255), mode="L")


# ── coder ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("arr", [
    np.zeros((1, 1)), np.ones((1, 1)), np.ones((5, 3)), np.zeros((48, 64)), np.ones((48, 64)),
    np.random.RandomState(1).randint(0, 2, (7, 50)), np.random.RandomState(2).randint(0, 2, (50, 1)),
    np.random.RandomState(3).randint(0, 2, (1, 77)), _smooth(93, 157), _smooth(240, 320, 5),
], ids=lambda a: "x".join(map(str, a.shape)))
def test_round_trip(arr):
    arr = arr.astype(np.uint8)
    data = bitmap_cm.encode(arr)
    assert np.array_equal(bitmap_cm.decode(data, arr.shape[1], arr.shape[0]), arr)


def test_all_dry_has_no_payload_and_trailing_zeros_are_dropped():
    assert bitmap_cm.encode(np.zeros((120, 160), np.uint8)) == b""
    data = bitmap_cm.encode(_smooth(60, 80))
    assert data and data[-1] != 0


def test_limit_raises_exactly_when_too_long():
    for arr in (_smooth(60, 80), np.random.RandomState(4).randint(0, 2, (20, 30)).astype(np.uint8)):
        n = len(bitmap_cm.encode(arr))
        assert bitmap_cm.encode(arr, max_bytes=n) == bitmap_cm.encode(arr)
        with pytest.raises(bitmap_cm.TooLarge):
            bitmap_cm.encode(arr, max_bytes=n - 1)


def test_shared_vectors_match_the_encoder_and_decode():
    """The fixture the API and dashboard decoders are tested against is what this
    encoder produces (tests/make_bitmap_vectors.py regenerates it)."""
    with open(VECTORS) as f:
        vectors = json.load(f)["vectors"]
    assert {v["method"] for v in vectors} == {0, 1, 2}
    for v in vectors:
        data = bytes.fromhex(v["bitmap_hex"])
        arr = cs.decompress(data)
        assert arr.shape == (v["height"], v["width"]), v["name"]
        assert hashlib.sha256(arr.astype(np.uint8).tobytes()).hexdigest() == v["pixels_sha256"], v["name"]
        assert int(arr.sum()) == v["water"]
        if v["method"] == 2:
            assert bitmap_cm.encode(arr) == data[5:], v["name"]


# ── method selection ──────────────────────────────────────────────────────────

def test_compress_methods_without_method2_is_brotli_only():
    arr = _smooth(93, 157)
    comp, method = cs.compress_methods(arr)
    assert method in (0, 1)
    assert (comp, method) == cs.compress_brotli(arr)


def test_compress_methods_keeps_the_smallest():
    arr = _smooth(93, 157)
    brotli_comp, _ = cs.compress_brotli(arr)
    comp, method = cs.compress_methods(arr, method2=True)
    assert method == 2 and len(comp) < len(brotli_comp)
    # All water at 640x480: Brotli (RLE) is smaller than the context model
    water = np.ones((480, 640), np.uint8)
    comp, method = cs.compress_methods(water, method2=True)
    assert method in (0, 1) and len(comp) < len(bitmap_cm.encode(water))


def test_rle_matches_the_old_loop():
    def old_rle(arr):
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
    rs = np.random.RandomState(5)
    for arr in (np.zeros((3, 600), np.uint8), np.ones((1, 1), np.uint8), np.ones((2, 255), np.uint8),
                rs.randint(0, 2, (40, 50)).astype(np.uint8), (rs.rand(100, 300) > 0.97).astype(np.uint8)):
        assert cs.rle(arr) == old_rle(arr)


# ── size search and the gate ──────────────────────────────────────────────────

def _old_find_best_size(img, max_bytes, min_size=32):
    """find_best_size before method 2, verbatim."""
    low, high = min_size, 160
    best = None
    while low <= high:
        mid = (low + high) // 2
        arr_bin = cs.to_binary(cs.resize_keep_aspect(img, (mid, mid)))
        comp, method = cs.compress_brotli(arr_bin)
        header = cs.minimal_header(arr_bin.shape[1], arr_bin.shape[0], method)
        if len(header) + len(comp) <= max_bytes:
            best = (arr_bin, comp, method, header)
            low = mid + 1
        else:
            high = mid - 1
    return best


@pytest.mark.parametrize("budget", [60, 120, 238])
def test_gate_off_is_the_old_search(budget):
    img = _mask_image(_smooth(972, 1296, 1))
    new = cs.find_best_size(img, budget)
    old = _old_find_best_size(img, budget)
    assert new[3] + new[1] == old[3] + old[1]
    assert max(new[0].shape) <= 160 and new[2] in (0, 1)


@pytest.mark.parametrize("budget", [60, 120, 238])
def test_gate_on_fits_more_pixels(budget):
    img = _mask_image(_smooth(972, 1296, 1))
    off = cs.find_best_size(img, budget)
    arr, comp, method, header = cs.find_best_size(img, budget, method2=True)
    assert len(header) + len(comp) <= budget
    assert max(arr.shape) <= cs.MAX_SIZE_METHOD2
    assert arr.size > off[0].size
    assert np.array_equal(cs.decompress(header + comp), arr)


def test_gate_on_uniform_scene_goes_to_the_cap():
    arr, comp, method, header = cs.find_best_size(_mask_image(np.zeros((972, 1296))), 238, method2=True)
    assert max(arr.shape) == 640 and method == 2 and len(header + comp) == 5


def test_compress_image_passes_the_gate(tmp_path):
    p = tmp_path / "mask.png"
    _mask_image(_smooth(972, 1296, 2)).save(str(p))
    off = cs.compress_image(str(p), max_bytes=238)
    on = cs.compress_image(str(p), max_bytes=238, method2=True)
    assert off["method"] in (0, 1) and max(off["width"], off["height"]) <= 160
    assert on["method"] == 2 and on["width"] > off["width"] and on["total_size"] <= 238


def test_gate_parameter_defaults_on_and_is_settable(tmp_path):
    from tools.lora_runtime_integration import LoRaRuntimeManager
    assert LoRaRuntimeManager._DEFAULT_PARAMS["bitmap_method2_enabled"] is True
    assert "bitmap_method2_enabled" in LoRaRuntimeManager._SETTABLE_PARAMS


@pytest.mark.parametrize("value,expected", [(False, False), (True, True), (None, True), ("false", True)])
def test_compress_bitmap_reads_the_gate(value, expected):
    import ticktalk_main
    seen = {}

    def fake_compress(path, max_bytes=238, method2=False, **_):
        seen["method2"] = method2
        return {"success": True, "compressed_data": b"\x00\x00\x20\x00\x20x",
                "total_size": 6, "width": 32, "height": 32, "method": 0}

    def get_param(key, default=None):
        return value if key == "bitmap_method2_enabled" else default

    with patch("tools.lora_handler_concurrent.get_size_limit", return_value=242), \
            patch("tools.lora_runtime_integration.get_parameter", side_effect=get_param), \
            patch("tools.compress_segmented.compress_image", side_effect=fake_compress):
        ticktalk_main.compress_bitmap.__wrapped__("fake.png")
    assert seen["method2"] is expected


# ── full-resolution IP bitmap ─────────────────────────────────────────────────

def test_full_resolution_is_lossless_brotli(tmp_path):
    arr = _smooth(972, 1296, 3)
    p = tmp_path / "mask.png"
    _mask_image(arr).save(str(p))
    data = cs.compress_full_resolution(str(p))
    assert data[0] in (0, 1) and int.from_bytes(data[1:3], "big") == 1296
    assert len(data) <= cs.FULL_RES_MAX_BYTES
    assert np.array_equal(cs.decompress(data), arr)


def test_full_resolution_over_the_limit_or_not_a_mask_is_none(tmp_path):
    p = tmp_path / "mask.png"
    _mask_image(_smooth(972, 1296, 3)).save(str(p))
    assert cs.compress_full_resolution(str(p), max_bytes=100) is None
    noise = tmp_path / "noise.png"
    _mask_image(np.random.RandomState(0).rand(972, 1296) > 0.5).save(str(noise))
    assert cs.compress_full_resolution(str(noise)) is None      # ~157 kB, over 32000
    rgb = tmp_path / "rgb.png"
    Image.new("RGB", (64, 48)).save(str(rgb))
    assert cs.compress_full_resolution(str(rgb)) is None


def _ip_channels(dirname, bitmap):
    import ticktalk_main
    with patch("tools.transmit_ip.IPTransmitter") as MockTx, \
            patch("tools.aht20_temperature.get_aht20", return_value={}), \
            patch("tools.get_gps.get_location_with_retry", return_value=({}, None)), \
            patch("tools.battery_manager.get_battery_status", return_value={"battery_pct": 80}), \
            patch("tools.lora_runtime_integration.get_parameter", side_effect=lambda k, d=None: d):
        tx = MockTx.return_value
        tx.enabled = True
        tx.only_if_lora_unavailable = False
        tx.cellular_connection = ""
        tx.is_reachable.return_value = False
        tx._enqueue.return_value = True
        tx.fallback_to_lora = False
        ticktalk_main.ip_uplink_transmit.__wrapped__(bitmap=bitmap, _sensor_tracker=None, dirname=dirname)
    return {c["code"]: c["payload_hex"] for c in tx._enqueue.call_args.args[0]}


LORA_BITMAP = b"\x00\x00\x20\x00\x18" + b"lora"


def test_ip_uplink_sends_the_full_resolution_mask(tmp_path):
    arr = _smooth(972, 1296, 6)
    _mask_image(arr).save(str(tmp_path / "color_preserved_5_band_segmentation.png"))
    channels = _ip_channels(str(tmp_path), LORA_BITMAP)
    data = bytes.fromhex(channels["08 18"])
    assert data != LORA_BITMAP
    assert np.array_equal(cs.decompress(data), arr)


def test_ip_uplink_falls_back_to_the_lora_bitmap_when_too_large(tmp_path):
    _mask_image(np.random.RandomState(1).rand(972, 1296) > 0.5).save(
        str(tmp_path / "color_preserved_5_band_segmentation.png"))
    assert bytes.fromhex(_ip_channels(str(tmp_path), LORA_BITMAP)["08 18"]) == LORA_BITMAP


def test_ip_uplink_without_a_mask_sends_the_lora_bitmap(tmp_path):
    assert bytes.fromhex(_ip_channels(str(tmp_path), LORA_BITMAP)["08 18"]) == LORA_BITMAP
