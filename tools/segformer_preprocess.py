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


#: Highest model-metadata schema this daemon understands (docs/MODEL_METADATA.md
#: in segformer_5band / photo_processing).
SUPPORTED_SCHEMA = 1
KNOWN_BANDS = ("red", "green", "blue", "thermal", "nir")


class ModelContractError(ValueError):
    """The model declares something this daemon cannot feed it correctly."""


def model_metadata(session) -> dict:
    """The graph's `metadata_props` ({} for a model that declares nothing)."""
    try:
        return dict(session.get_modelmeta().custom_metadata_map or {})
    except Exception:                      # noqa: BLE001 - never break inference
        return {}


def check_model_contract(meta: dict, graph_channels=None) -> list:
    """Reasons to refuse a model, from its metadata; [] when it is fine.

    Refuses what would feed the model a distribution or channel set it never
    saw (a wrong channel count, a normalization or resize this daemon cannot
    reproduce, a schema from the future). Band *order* is not checked here: it
    stays a warning (`band_order_problem`). A model with no metadata passes:
    it is the historical min-max model.
    """
    import json

    if not meta:
        return []
    out = []
    schema = meta.get("schema", "")
    if schema:
        if not schema.isdigit() or int(schema) > SUPPORTED_SCHEMA:
            out.append(f"metadata schema {schema!r} is newer than this daemon "
                       f"understands (max {SUPPORTED_SCHEMA})")

    bands = [b.strip().lower() for b in (meta.get("bands") or "").split(",") if b.strip()]
    declared = meta.get("in_channels", "")
    n = None
    if declared:
        if not declared.isdigit():
            out.append(f"in_channels {declared!r} is not a number")
        else:
            n = int(declared)
    if bands:
        unknown = [b for b in bands if b not in KNOWN_BANDS]
        if unknown:
            out.append(f"unknown band name(s) {','.join(unknown)}: the daemon can "
                       f"only supply {','.join(KNOWN_BANDS)}")
        if len(set(bands)) != len(bands):
            out.append("a band is listed more than once")
        if n is not None and n != len(bands):
            out.append(f"in_channels {n} but {len(bands)} bands declared")
    if n is None and bands:
        n = len(bands)
    if n is not None and isinstance(graph_channels, int) and n != graph_channels:
        out.append(f"metadata says {n} channels but the graph takes {graph_channels}")
    if n is not None and not 1 <= n <= len(KNOWN_BANDS):
        out.append(f"cannot supply {n} channels (the capture has {len(KNOWN_BANDS)} bands)")

    if meta.get("input_layout", "NCHW") != "NCHW":
        out.append(f"input_layout {meta['input_layout']!r}: only NCHW is supported")

    norm = meta.get("normalization", "")
    if norm:
        embedded = norm.startswith(EMBEDDED_PREFIX)
        method = norm.split(":", 1)[-1]
        if embedded:
            if meta.get("input_range", "raw_0_255") != "raw_0_255":
                out.append(f"{norm} but input_range {meta['input_range']!r}: the daemon "
                           f"feeds raw 0-255 to a graph that normalises itself")
        elif method not in SUPPORTED_EXTERNAL:
            out.append(f"normalization {norm!r} is not one the daemon can apply "
                       f"({', '.join(SUPPORTED_EXTERNAL)}, or embedded:<method>)")
        else:
            keys = {"meanstd": ("norm_mean", "norm_std"),
                    "percentile": ("norm_p_lo", "norm_p_hi")}.get(method, ())
            for k in keys:
                try:
                    vals = json.loads(meta[k])
                    if len(vals) != (n or len(vals)):
                        out.append(f"{k} has {len(vals)} values for {n} channels")
                except (KeyError, ValueError, TypeError):
                    out.append(f"normalization {norm!r} needs {k}, which is missing "
                               f"or unreadable")

    interp = (meta.get("resize_interpolation") or "").strip().lower()
    if interp and interp not in RESIZE_INTERPOLATIONS:
        out.append(f"resize_interpolation {interp!r}: the daemon has "
                   f"{', '.join(RESIZE_INTERPOLATIONS)}")
    return out


def graph_input_channels(session):
    """The graph's channel count if it is static, else None."""
    try:
        c = session.get_inputs()[0].shape[1]
    except Exception:                      # noqa: BLE001
        return None
    return c if isinstance(c, int) else None


def canonical_bands(descriptions) -> list:
    """Band descriptions from the TIFF -> canonical names, `None` where unknown."""
    out = []
    for d in descriptions or ():
        low = (d or "").lower()
        out.append(next((name for kw, name in BAND_KEYWORDS if kw in low), None))
    return out


def tiff_band_order(descriptions, tags=None) -> list:
    """The TIFF's band order, preferring its BAND_ORDER tag over prose.

    `tools/coreg_multiple.py` writes BAND_ORDER as the same comma-separated
    canonical names the .onnx declares, so when it is present no interpretation
    is needed. Captures written before that tag existed carry only the prose
    band descriptions, which still have to be keyword-matched.
    """
    declared = ((tags or {}).get("BAND_ORDER") or "").strip()
    if declared:
        return [b.strip().lower() or None for b in declared.split(",")]
    return canonical_bands(descriptions)


def _declared_bands(session) -> list:
    try:
        meta = session.get_modelmeta().custom_metadata_map or {}
    except Exception:                      # noqa: BLE001 - never break inference
        return []
    return [b.strip().lower() for b in (meta.get("bands") or "").split(",") if b.strip()]


def model_band_indices(session, descriptions, tags=None) -> list | None:
    """0-based TIFF bands to feed a model that wants fewer bands than the TIFF has.

    The five-band TIFF also carries what an RGB model needs: an RGB model that
    declares `bands=red,green,blue` gets those three, picked by name. Returns
    None (feed every band, as before) unless the model declares fewer bands
    than the TIFF and the TIFF names each of them exactly once.
    """
    want = _declared_bands(session)
    got = tiff_band_order(descriptions, tags)
    if not want or len(want) >= len(got):
        return None
    if any(got.count(w) != 1 for w in want):
        return None
    return [got.index(w) for w in want]


def band_order_problem(session, descriptions, tags=None) -> str:
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
    got = tiff_band_order(descriptions, tags)
    if not got or all(g is None for g in got):
        return ""                          # TIFF carries no readable descriptions
    if model_band_indices(session, descriptions, tags) is not None:
        return ""                          # a named subset, picked by name
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


def water_class_index(session) -> int:
    """Which logit channel is water, from the graph's `classes` metadata.

    Returns -1 when the model does not declare its taxonomy, which means every
    model exported before 2026-09-27. Those are binary and their index 1 is
    water by construction, so the caller keeps its existing behaviour rather
    than guessing from an absent declaration.
    """
    try:
        meta = session.get_modelmeta().custom_metadata_map or {}
    except Exception:                      # noqa: BLE001 - never break inference
        return -1
    names = [c.strip().lower() for c in (meta.get("classes") or "").split(",") if c.strip()]
    return names.index("water") if "water" in names else -1


#: `resize_interpolation` metadata -> OpenCV flag name. mmcv's Resize, which
#: both the 5-band and type_pool test pipelines use, is bilinear; the node has
#: always used INTER_AREA, so a model that declares nothing keeps it.
RESIZE_INTERPOLATIONS = ("area", "bilinear")


def resize_interpolation(session) -> str:
    """How to resize input bands: the graph's `resize_interpolation`, else "area"."""
    try:
        meta = session.get_modelmeta().custom_metadata_map or {}
    except Exception:                      # noqa: BLE001 - never break inference
        return "area"
    declared = (meta.get("resize_interpolation") or "").strip().lower()
    return declared if declared in RESIZE_INTERPOLATIONS else "area"


def normalization_mode(session) -> tuple[bool, str]:
    """(does this module normalise, human description) -- convenience wrapper."""
    spec = normalization_spec(session)
    return spec["mode"] != "embedded", spec["desc"]


def preprocess_bands(img: np.ndarray, height: int, width: int,
                     normalize: bool = True, spec: dict | None = None,
                     interpolation: str = "area") -> np.ndarray:
    """Resize a (bands, H, W) array and apply the normalisation the model wants.

    Accepts any numeric dtype; always works on an internal float32 copy so
    the caller's buffer is never modified and uint8 inputs don't silently
    truncate normalised values back to integers.
    Constant bands (hi == lo) are zeroed out rather than left unnormalized.

    `spec` comes from `normalization_spec(session)`. Without it the behaviour
    is the historical one: per-band min-max, unless `normalize=False`, which
    resizes only (for a graph that normalises internally).
    `interpolation` comes from `resize_interpolation(session)`.
    """
    import cv2

    img = img.astype(np.float32, copy=True)
    flag = cv2.INTER_LINEAR if interpolation == "bilinear" else cv2.INTER_AREA

    if img.shape[1] != height or img.shape[2] != width:
        img = np.stack(
            [cv2.resize(img[i], (width, height), interpolation=flag)
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
