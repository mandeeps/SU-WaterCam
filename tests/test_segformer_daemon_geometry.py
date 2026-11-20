"""The daemon must hand ORT an input that matches the graph's declared shape.

A static axis is a contract: resizing or padding it to anything else is an ORT
InvalidArgument. A symbolic axis must keep the capture's aspect ratio, because
the mask is georeferenced from IMU pose and a distorted mask distorts that.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from segformer_daemon import SIZE_DIVISOR, input_geometry  # noqa: E402

# The deployed capture
ORI_H, ORI_W = 972, 1296


def test_fully_dynamic_uses_keep_ratio_and_pads_to_divisor():
    h, w, pad_h, pad_w = input_geometry([1, 5, "height", "width"], ORI_H, ORI_W)
    assert (h, w) == (512, 683)                  # mmcv.imrescale for img_scale (1024, 512)
    assert (h + pad_h) % SIZE_DIVISOR == 0 and (w + pad_w) % SIZE_DIVISOR == 0


def test_fully_static_is_used_as_is_and_never_padded():
    assert input_geometry([1, 5, 512, 512], ORI_H, ORI_W) == (512, 512, 0, 0)


def test_static_axis_not_multiple_of_divisor_is_not_padded():
    # Padding a static axis would make the input disagree with the graph
    assert input_geometry([1, 5, 500, 700], ORI_H, ORI_W) == (500, 700, 0, 0)


@pytest.mark.parametrize("static_h", [384, 512, 600])
def test_static_height_is_kept_and_width_follows_aspect(static_h):
    h, w, pad_h, pad_w = input_geometry([1, 5, static_h, "width"], ORI_H, ORI_W)
    assert h == static_h and pad_h == 0
    assert w == round(ORI_W * static_h / ORI_H)
    assert (w + pad_w) % SIZE_DIVISOR == 0


def test_static_width_is_kept_and_height_follows_aspect():
    h, w, pad_h, pad_w = input_geometry([1, 5, "height", 640], ORI_H, ORI_W)
    assert w == 640 and pad_w == 0
    assert h == round(ORI_H * 640 / ORI_W)
    assert (h + pad_h) % SIZE_DIVISOR == 0


def test_none_axes_are_treated_as_symbolic():
    # onnxruntime reports unnamed dynamic dims as None
    assert input_geometry([1, 5, None, None], ORI_H, ORI_W)[:2] == (512, 683)


# ── size policy "divisible" (off by default; needs the next checkpoint) ─────

def test_default_policy_is_pad():
    # Unchanged behaviour unless SEGFORMER_SIZE_POLICY=divisible is set
    assert input_geometry([1, 5, "height", "width"], ORI_H, ORI_W) == \
        input_geometry([1, 5, "height", "width"], ORI_H, ORI_W, policy="pad")


def test_divisible_resizes_instead_of_padding():
    h, w, pad_h, pad_w = input_geometry([1, 5, "height", "width"], ORI_H, ORI_W,
                                        policy="divisible")
    assert (pad_h, pad_w) == (0, 0)
    assert h % SIZE_DIVISOR == 0 and w % SIZE_DIVISOR == 0
    assert (h, w) == (512, 672)                  # 683 rounds to the nearest 32


def test_divisible_never_touches_a_static_axis():
    assert input_geometry([1, 5, 500, 700], ORI_H, ORI_W, policy="divisible") == \
        (500, 700, 0, 0)
    h, w, pad_h, pad_w = input_geometry([1, 5, 600, "width"], ORI_H, ORI_W,
                                        policy="divisible")
    assert (h, pad_h) == (600, 0)                # static height kept, not rounded
    assert w % SIZE_DIVISOR == 0 and pad_w == 0  # symbolic width rounded, not padded
