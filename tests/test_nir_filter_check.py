"""tools/nir_filter_check.py: the IR-cut filter check on a capture's NIR pair."""
import numpy as np
from PIL import Image

from tools import nir_filter_check as nfc


def _scene(seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(60, 200, size=(486, 648, 3)).astype(np.float32)


def _write(dirname, name, arr):
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(dirname / name, quality=90)


def _pair(tmp_path, off, on):
    _write(tmp_path, "20261025-135529-NIR-OFF.jpg", off)
    _write(tmp_path, "20261025-135530-NIR-ON.jpg", on)
    return str(tmp_path)


def test_filter_moved(tmp_path):
    off = _scene()
    on = off.copy()
    on[..., 0] += 40   # infrared lifts red and blue: the ON frame turns pink
    on[..., 2] += 35
    assert nfc.status_code_for_capture(_pair(tmp_path, off, on)) == nfc.STATUS_OK


def test_filter_stuck(tmp_path):
    off = _scene()
    assert nfc.status_code_for_capture(_pair(tmp_path, off, off.copy())) == nfc.STATUS_NOT_SWITCHING


def test_movement_alone_is_not_a_switch(tmp_path):
    # Water and leaves move between the frames: pixels change, the colour means don't
    off = _scene(1)
    on = _scene(2)
    assert nfc.status_code_for_capture(_pair(tmp_path, off, on)) == nfc.STATUS_NOT_SWITCHING


def test_dark_pair_gives_no_verdict(tmp_path):
    dark = np.full((486, 648, 3), 5.0, dtype=np.float32)
    assert nfc.status_code_for_capture(_pair(tmp_path, dark, dark)) is None


def test_no_pair_gives_no_verdict(tmp_path):
    assert nfc.status_code_for_capture(None) is None
    assert nfc.status_code_for_capture(str(tmp_path)) is None
    _write(tmp_path, "20261025-135529-NIR-OFF.jpg", _scene())
    assert nfc.status_code_for_capture(str(tmp_path)) is None


def test_unreadable_frame_gives_no_verdict(tmp_path):
    _write(tmp_path, "20261025-135529-NIR-OFF.jpg", _scene())
    (tmp_path / "20261025-135530-NIR-ON.jpg").write_bytes(b"not a jpeg")
    assert nfc.status_code_for_capture(str(tmp_path)) is None


def test_lora_and_codec_field():
    from tools.lora_handler_concurrent import _encode_compressed_packet
    assert _encode_compressed_packet({"nir_filter_status": 1}) == b"\x07\x47\x01"
    assert _encode_compressed_packet({"nir_filter_status": None}) == b""
