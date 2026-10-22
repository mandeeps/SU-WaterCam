"""tools/compress_segmented.py: fixed threshold and the mask format check.

The old mean/Otsu threshold split a uniform image at its own value, so an
all-water mask was sent as no water at all.
"""
import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from tools.compress_segmented import (compress_image, decompress, mask_format_error,
                                      to_binary, water_percent)


def _save(tmp_path, arr, mode="L", name="mask.png"):
    p = tmp_path / name
    Image.fromarray(arr, mode=mode).save(str(p))
    return str(p)


def _sent_water_fraction(path):
    result = compress_image(path, save_images=False)
    assert result["success"] is True
    return float(decompress(result["compressed_data"]).mean())


def test_all_water_is_sent_as_all_water(tmp_path):
    path = _save(tmp_path, np.full((120, 160), 255, np.uint8))
    assert _sent_water_fraction(path) == 1.0
    assert water_percent(Image.open(path)) == 100


def test_all_dry_is_sent_as_dry(tmp_path):
    path = _save(tmp_path, np.zeros((120, 160), np.uint8))
    assert _sent_water_fraction(path) == 0.0
    assert water_percent(Image.open(path)) == 0


def test_half_water_is_sent_as_half(tmp_path):
    arr = np.zeros((120, 160), np.uint8)
    arr[:, :80] = 255
    path = _save(tmp_path, arr)
    assert _sent_water_fraction(path) == pytest.approx(0.5, abs=0.02)
    assert water_percent(Image.open(path)) == 50


def test_threshold_is_fixed_at_127():
    arr = np.array([[0, 127, 128, 255]], np.uint8)
    assert to_binary(Image.fromarray(arr, mode="L")).tolist() == [[0, 0, 1, 1]]


def test_rgb_mask_is_refused(tmp_path):
    arr = np.zeros((32, 32, 3), np.uint8)
    arr[:16] = (0, 0, 255)
    path = _save(tmp_path, arr, mode="RGB")
    result = compress_image(path, save_images=False)
    assert result["success"] is False
    assert "mode RGB" in result["error"]
    assert not (tmp_path / "mask_compressed.bin").exists()


def test_rgba_mask_is_refused():
    img = Image.fromarray(np.zeros((8, 8, 4), np.uint8), mode="RGBA")
    assert "mode RGBA" in mask_format_error(img)


def test_greyscale_photo_is_refused():
    arr = np.tile(np.arange(256, dtype=np.uint8), (16, 1))
    assert "not binary" in mask_format_error(Image.fromarray(arr, mode="L"))


def test_class_index_mask_is_refused():
    # 0/1 class indices would all read as dry under the 127 threshold
    arr = np.zeros((16, 16), np.uint8)
    arr[:8] = 1
    assert mask_format_error(Image.fromarray(arr, mode="L")) is not None


def test_binary_masks_are_accepted():
    arr = np.zeros((16, 16), np.uint8)
    arr[:8] = 255
    assert mask_format_error(Image.fromarray(arr, mode="L")) is None
    assert mask_format_error(Image.fromarray(arr, mode="L").convert("1")) is None


def test_compress_image_closes_the_file(tmp_path, monkeypatch):
    from PIL import Image
    import numpy as np
    from tools import compress_segmented as cs
    arr = np.zeros((64, 64), np.uint8)
    arr[:20] = 255
    path = tmp_path / "mask.png"
    Image.fromarray(arr, mode="L").save(path)
    opened = []
    real_open = Image.open

    def spy(*a, **k):
        img = real_open(*a, **k)
        opened.append(img)
        return img
    monkeypatch.setattr(cs.Image, "open", spy)
    assert cs.compress_image(str(path))["success"]
    assert opened and all(img.fp is None for img in opened)
