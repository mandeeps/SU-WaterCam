"""
Shared preprocessing for SegFormer 5-band inference and INT8 calibration.

Single source of truth so runtime (segformer_daemon) and calibration
(export_segformer_onnx) always apply identical transforms.
"""

import numpy as np


def preprocess_bands(img: np.ndarray, height: int, width: int) -> np.ndarray:
    """Resize and per-band min-max normalize a (bands, H, W) array.

    Accepts any numeric dtype; always works on an internal float32 copy so
    the caller's buffer is never modified and uint8 inputs don't silently
    truncate normalised values back to integers.
    Constant bands (hi == lo) are zeroed out rather than left unnormalized.
    Returns a (1, bands, H, W) float32 array clipped to [0, 1].
    """
    import cv2

    img = img.astype(np.float32, copy=True)

    if img.shape[1] != height or img.shape[2] != width:
        img = np.stack(
            [cv2.resize(img[i], (width, height), interpolation=cv2.INTER_AREA)
             for i in range(img.shape[0])],
            axis=0,
        )

    for i in range(img.shape[0]):
        lo, hi = float(img[i].min()), float(img[i].max())
        if hi > lo:
            img[i] = (img[i] - lo) / (hi - lo)
        else:
            img[i] = 0.0

    return np.clip(img, 0.0, 1.0)[np.newaxis]
