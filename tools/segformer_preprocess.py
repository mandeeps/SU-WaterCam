"""
Shared preprocessing for SegFormer 5-band inference and INT8 calibration.

Single source of truth so runtime (segformer_daemon) and calibration
(export_segformer_onnx) always apply identical transforms.

**The model decides how it is normalised, not this file.** A model trained by
the annotator's `training/` pipeline is normalised with band statistics
measured over its training split, and those are compiled into the exported
graph, which then expects raw resized bands in 0-255 units. Normalising such
an input again here would feed it something it never saw in training -- no
error, just a quietly worse mask. `normalization_mode()` reads what the graph
declares and `preprocess_bands(..., normalize=False)` honours it.

Models exported before this metadata existed declare nothing, and are handled
exactly as before: per-band min-max, computed per image.
"""

# PEP 604 unions (`dict | None`) are evaluated at def time, and the node runs
# Python 3.9, so annotations are deferred to keep this importable there.
from __future__ import annotations

import numpy as np

#: prefix a graph uses to say it normalises its own input
EMBEDDED_PREFIX = "embedded:"
#: what this module can reproduce itself, from values stamped in the graph
SUPPORTED_EXTERNAL = ("minmax", "meanstd", "percentile")

#: Keyword -> canonical band name, for reading the band descriptions
#: `tools/coreg_multiple.py` writes into the five-band TIFF ("Red Channel
#: (Optical)", "Thermal Data (Normalized 0-255)", "NIR Band (NIR-ON minus
#: NIR-OFF)", ...). Checked in this order, so "nir" is matched before "red"
#: can match inside it.
BAND_KEYWORDS = (
    ("nir", "nir"),
    ("thermal", "thermal"), ("lwir", "thermal"),
    ("red", "red"), ("green", "green"), ("blue", "blue"),
)


def normalization_spec(session) -> dict:
    """How to preprocess for a loaded onnxruntime session.

    Returns {"mode": ..., "desc": ...} plus any arrays the mode needs.
      embedded    - the graph normalises itself; feed it raw resized bands
      minmax      - per-image, per-band; the historical default
      meanstd     - fixed statistics, read from the graph's metadata
      percentile  - fixed low/high clip, read from the graph's metadata

    Anything unreadable or unrecognised degrades to `minmax`, which is what
    every model exported before this metadata existed expects.
    """
    import json

    try:
        meta = session.get_modelmeta().custom_metadata_map or {}
    except Exception:                      # noqa: BLE001 - never break inference
        return {"mode": "minmax", "desc": "min-max (no readable metadata)"}

    declared = meta.get("normalization", "")
    if declared.startswith(EMBEDDED_PREFIX):
        return {"mode": "embedded",
                "desc": f"none here; graph applies {declared[len(EMBEDDED_PREFIX):]}"}

    # A model that declares nothing is *assumed* to want min-max, which is the
    # historical default. Say so, so a log line cannot be read as the model
    # having asked for it. Silently treating a default as a contract is how the
    # normalisation mismatch this metadata exists to prevent went unnoticed.
    if not declared:
        return {"mode": "minmax",
                "desc": "min-max per image (DEFAULT — model declares nothing)"}

    method = declared.split(":", 1)[-1]
    if method == "minmax":
        return {"mode": "minmax", "desc": "min-max per image (declared)"}
    if method not in SUPPORTED_EXTERNAL:
        return {"mode": "minmax",
                "desc": f"min-max FALLBACK (WARNING: model declares unknown '{declared}')"}
    try:
        if method == "meanstd":
            return {"mode": "meanstd", "desc": "mean/std from model metadata (declared)",
                    "mean": np.asarray(json.loads(meta["norm_mean"]), np.float32),
                    "std": np.asarray(json.loads(meta["norm_std"]), np.float32)}
        return {"mode": "percentile", "desc": "percentile from model metadata (declared)",
                "lo": np.asarray(json.loads(meta["norm_p_lo"]), np.float32),
                "hi": np.asarray(json.loads(meta["norm_p_hi"]), np.float32)}
    except (KeyError, ValueError) as e:
        return {"mode": "minmax",
                "desc": f"min-max (WARNING: '{declared}' declared but unusable: {e})"}


def canonical_bands(descriptions) -> list:
    """Band descriptions from the TIFF -> canonical names, `None` where unknown."""
    out = []
    for d in descriptions or ():
        low = (d or "").lower()
        out.append(next((name for kw, name in BAND_KEYWORDS if kw in low), None))
    return out


def band_order_problem(session, descriptions) -> str:
    """Does the TIFF's band order match what the model was trained on?

    The model stamps its band order at export (`training/models/segformer.py`).
    Feeding the bands in a different order is the one mistake that produces a
    plausible-looking mask from the wrong data: every channel is a valid image,
    so nothing raises and nothing looks wrong until the water is in the wrong
    place. Returns "" when the orders agree or when there is nothing to compare.
    """
    try:
        meta = session.get_modelmeta().custom_metadata_map or {}
    except Exception:                      # noqa: BLE001 - never break inference
        return ""
    want = [b.strip().lower() for b in (meta.get("bands") or "").split(",") if b.strip()]
    if not want:
        return ""                          # exported before this metadata existed
    got = canonical_bands(descriptions)
    if not got or all(g is None for g in got):
        return ""                          # TIFF carries no readable descriptions
    if len(got) != len(want):
        return (f"model expects {len(want)} bands ({','.join(want)}) but the TIFF "
                f"has {len(got)}")
    named = [g for g in got if g is not None]
    if [g for g, w in zip(got, want) if g is not None and g != w]:
        return (f"model expects bands {','.join(want)} but the TIFF is "
                f"{','.join(g or '?' for g in got)}")
    if len(named) < len(want):
        return ""                          # partial match, nothing contradicts
    return ""


def normalization_mode(session) -> tuple[bool, str]:
    """(does this module normalise, human description) -- convenience wrapper."""
    spec = normalization_spec(session)
    return spec["mode"] != "embedded", spec["desc"]


def preprocess_bands(img: np.ndarray, height: int, width: int,
                     normalize: bool = True, spec: dict | None = None) -> np.ndarray:
    """Resize a (bands, H, W) array and apply the normalisation the model wants.

    Accepts any numeric dtype; always works on an internal float32 copy so
    the caller's buffer is never modified and uint8 inputs don't silently
    truncate normalised values back to integers.
    Constant bands (hi == lo) are zeroed out rather than left unnormalized.

    `spec` comes from `normalization_spec(session)`. Without it the behaviour
    is the historical one: per-band min-max, unless `normalize=False`, which
    resizes only (for a graph that normalises internally).
    """
    import cv2

    img = img.astype(np.float32, copy=True)

    if img.shape[1] != height or img.shape[2] != width:
        img = np.stack(
            [cv2.resize(img[i], (width, height), interpolation=cv2.INTER_AREA)
             for i in range(img.shape[0])],
            axis=0,
        )

    mode = (spec or {}).get("mode", "minmax" if normalize else "embedded")
    if mode == "embedded":
        return img[np.newaxis]
    if mode == "meanstd":
        mean = spec["mean"].reshape(-1, 1, 1)
        std = np.maximum(spec["std"].reshape(-1, 1, 1), 1e-6)
        return ((img - mean) / std)[np.newaxis]
    if mode == "percentile":
        lo = spec["lo"].reshape(-1, 1, 1)
        rng = np.maximum(spec["hi"].reshape(-1, 1, 1) - lo, 1e-6)
        return np.clip((img - lo) / rng, 0.0, 1.0)[np.newaxis]

    for i in range(img.shape[0]):
        lo, hi = float(img[i].min()), float(img[i].max())
        if hi > lo:
            img[i] = (img[i] - lo) / (hi - lo)
        else:
            img[i] = 0.0

    return np.clip(img, 0.0, 1.0)[np.newaxis]
