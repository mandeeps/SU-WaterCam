#!/usr/bin/env python
"""
SegFormer inference daemon.

Keeps the ONNX model loaded in memory and accepts inference requests over a
Unix domain socket, eliminating the 10–20 s cold-start cost of the old
conda segment_tiff_5band.py subprocess (removed). It is the only segmentation
path ticktalk_main uses.

Protocol
--------
Client → server (newline-terminated JSON):
    {"tiff_path": "/abs/path/color_preserved_5_band.tiff",
     "output_path": "/abs/path/color_preserved_5_band_segmentation.png"}

Server → client (newline-terminated JSON):
    {"status": "ok",   "inference_ms": 1234}
    {"status": "error","message": "..."}

Usage
-----
Run directly for testing (pass an explicit socket path under /tmp, because
/run/segformer/ is created by systemd RuntimeDirectory and will not exist
for a non-root user running outside systemd):

    /home/pi/SU-WaterCam/venv/bin/python tools/segformer_daemon.py \
        --model /home/pi/segformer_5band/segformer_5band_int8.onnx \
        --socket /tmp/segformer_test.sock

Or via systemd (see config/segformer_daemon.service), which creates
/run/segformer/ automatically at daemon startup:

The production socket path is /run/segformer/segformer.sock (systemd
RuntimeDirectory=segformer creates and owns this directory).
ticktalk_main.py's segformer() function connects to this socket if it
exists, and falls back to the legacy subprocess call otherwise.
"""

# PEP 604 unions (`dict | None`) are evaluated at def time, and the node runs
# Python 3.9, so annotations are deferred to keep this importable there.
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import stat
import sys
import threading
import time

import numpy as np

from segformer_preprocess import (ModelContractError, band_order_problem,
                                  check_model_contract, graph_input_channels,
                                  model_band_indices, model_metadata, normalization_mode,
                                  resize_interpolation,
                                  normalization_spec, preprocess_bands,
                                  tiff_band_order, water_class_index)

SOCKET_PATH = "/run/segformer/segformer.sock"
LOG_FORMAT = "%(asctime)s [segformer_daemon] %(levelname)s: %(message)s"
MAX_REQUEST_BYTES = 8192

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ONNX Runtime session
# ---------------------------------------------------------------------------

def load_session(model_path: str):
    import onnxruntime as ort

    if not os.path.exists(model_path):
        raise FileNotFoundError(f"ONNX model not found: {model_path}")

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.inter_op_num_threads = 1
    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    session = ort.InferenceSession(
        model_path,
        sess_options=opts,
        providers=["CPUExecutionProvider"],
    )
    input_meta = session.get_inputs()[0]
    # Refuse a model whose declared channels or normalization we cannot honour,
    # before it ever sees a frame. A model with no metadata passes (min-max).
    problems = check_model_contract(model_metadata(session), graph_input_channels(session))
    if problems:
        raise ModelContractError(
            f"{os.path.basename(model_path)} refused: " + "; ".join(problems))
    _, how = normalization_mode(session)
    log.info(
        "Model loaded: %s | input '%s' %s | normalisation: %s",
        os.path.basename(model_path),
        input_meta.name,
        input_meta.shape,
        how,
    )
    return session


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def load_and_preprocess(tiff_path: str, expected_h: int, expected_w: int,
                        spec: dict | None = None, bands: list | None = None,
                        interpolation: str = "area") -> np.ndarray:
    """`bands`: 0-based TIFF bands to read, in model order; None reads them all."""
    import rasterio

    with rasterio.open(tiff_path) as src:
        # (bands, H, W); preprocess_bands handles float32 conversion
        img = src.read() if bands is None else src.read([b + 1 for b in bands])

    return preprocess_bands(img, expected_h, expected_w, spec=spec, interpolation=interpolation)


#: The deployed mmseg test pipeline resizes with `img_scale=(1024, 512)` and
#: `keep_ratio=True` (local_configs/.../flood_5band_2cls.65k.test.py). A dynamic
#: graph carries no size of its own, so we reproduce that here rather than
#: inventing one.
DEFAULT_IMG_SCALE = (1024, 512)

#: SegFormer's encoder downsamples by 32, and the export substitutes a
#: scale_factor for an explicit output size, which is only exact when both
#: input dimensions divide by 32.
SIZE_DIVISOR = 32


def keep_ratio_size(oh: int, ow: int, img_scale=DEFAULT_IMG_SCALE) -> tuple[int, int]:
    """`mmcv.imrescale` semantics: fit inside img_scale without distorting aspect.

    `img_scale` is (long_edge, short_edge). Returns the (h, w) to resize to.
    For this deployment's 1296x972 capture that gives 512x683, which is what the
    torch path runs at.
    """
    long_edge, short_edge = max(img_scale), min(img_scale)
    scale = min(long_edge / max(oh, ow), short_edge / min(oh, ow))
    return int(oh * scale + 0.5), int(ow * scale + 0.5)


def input_geometry(input_shape, ori_h: int, ori_w: int) -> tuple[int, int, int, int]:
    """(resize_h, resize_w, pad_h, pad_w) for a graph's [batch, bands, H, W] input.

    Each spatial axis is handled on its own. A static axis is the graph's
    contract: resize to exactly that and never pad it, or ORT rejects the input.
    A symbolic axis is derived from the capture -- keep-ratio when both are
    symbolic, otherwise from the static axis so the aspect ratio survives --
    and padded up to SIZE_DIVISOR so the graph's scale_factor upsample is exact.
    """
    static_h = input_shape[2] if isinstance(input_shape[2], int) else None
    static_w = input_shape[3] if isinstance(input_shape[3], int) else None
    if static_h and static_w:
        h, w = static_h, static_w
    elif static_h:
        h, w = static_h, max(1, int(ori_w * static_h / ori_h + 0.5))
    elif static_w:
        h, w = max(1, int(ori_h * static_w / ori_w + 0.5)), static_w
    else:
        h, w = keep_ratio_size(ori_h, ori_w)
    pad_h = 0 if static_h else (-h) % SIZE_DIVISOR
    pad_w = 0 if static_w else (-w) % SIZE_DIVISOR
    return h, w, pad_h, pad_w


#: The last band-order complaint, so a per-frame check does not fill the log
#: with the same line every wake cycle.
_band_warning = [""]


def run_inference(session, tiff_path: str, output_path: str, rgb_only: bool = False) -> float:
    import cv2
    import rasterio
    from PIL import Image

    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape  # [batch, bands, H, W]

    with rasterio.open(tiff_path) as src:
        ori_h, ori_w = src.height, src.width
        descriptions = src.descriptions
        tiff_tags = src.tags()

    # Band order is the one mismatch that still produces a plausible mask, so
    # say so loudly and keep going — a metadata disagreement should not take a
    # field node offline.
    bands = model_band_indices(session, descriptions, tiff_tags)
    if rgb_only:
        # The thermal band of this TIFF is a placeholder. Only a model that
        # reads its three optical bands by name may be run on it.
        got = tiff_band_order(descriptions, tiff_tags)
        if bands is None or any(got[b] not in ("red", "green", "blue") for b in bands):
            raise ValueError("this model reads the thermal or NIR band and the capture has no thermal frame")
    problem = band_order_problem(session, descriptions, tiff_tags)
    if problem and problem != _band_warning[0]:
        _band_warning[0] = problem
        logging.warning("band order mismatch: %s", problem)

    # A static graph fixes its own input size. A dynamic one does not, and
    # squashing the capture into a square would destroy its aspect ratio —
    # this mask is georeferenced downstream from IMU pose, so a distorted mask
    # distorts the georeferencing, silently and without error.
    expected_h, expected_w, pad_h, pad_w = input_geometry(input_shape, ori_h, ori_w)

    # The model states how it was normalised; anything else hands it a
    # distribution it never saw in training, silently and without error.
    # An RGB model reads its three bands out of the five-band TIFF by name.
    arr = load_and_preprocess(tiff_path, expected_h, expected_w,
                              spec=normalization_spec(session),
                              bands=bands,
                              interpolation=resize_interpolation(session))

    # Pad symbolic axes up to SIZE_DIVISOR so the graph's scale_factor upsample
    # is exact. Zeros are the post-Normalize mean, matching what the torch path pads with.
    if pad_h or pad_w:
        arr = np.pad(arr, ((0, 0), (0, 0), (0, pad_h), (0, pad_w)))

    t0 = time.perf_counter()
    logits = session.run(None, {input_name: arr})[0]  # (1, classes, h, w)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Crop the pad back off, then finish the job mmseg would have done:
    # resize the logits to the source resolution before taking the argmax.
    logits = logits[:, :, :expected_h, :expected_w]
    if (expected_h, expected_w) != (ori_h, ori_w):
        logits = np.stack(
            [cv2.resize(logits[0, c], (ori_w, ori_h), interpolation=cv2.INTER_LINEAR)
             for c in range(logits.shape[1])],
            axis=0,
        )[np.newaxis]

    pred = np.argmax(logits[0], axis=0).astype(np.uint8)

    # What a node transmits is binary: water or not. The extra classes of the
    # four-class taxonomy (snow_ice, wet_ground) are training signal that makes
    # the water boundary sharper, not output — see photo_processing/training/
    # classes.py. Collapsing here rather than downstream matters because
    # tools/compress_segmented.py thresholds this PNG at 127 into a 1-bit LoRa
    # bitmap and refuses anything but a 0/255 mask: hand it {0, 85, 170, 255}
    # and two non-water classes would count as water.
    n_classes = logits.shape[1]
    water = water_class_index(session)
    if water >= 0:
        pred_vis = np.where(pred == water, 255, 0).astype(np.uint8)
    elif n_classes > 1:
        # No declared taxonomy: every such model is binary, so scaling indices
        # to 0-255 is the same thing and preserves the historical output.
        # Float division so the highest index maps exactly to 255 (integer
        # division, e.g. 255//4=63, would cap at 252 for 5 classes).
        pred_vis = np.round(pred.astype(np.float32) * (255.0 / (n_classes - 1))).astype(np.uint8)
    else:
        pred_vis = pred

    Image.fromarray(pred_vis).save(output_path)
    return elapsed_ms


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

def handle_connection(conn: socket.socket, session) -> None:
    try:
        conn.settimeout(30)
        data = bytearray()
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_REQUEST_BYTES:
                raise ValueError(f"Request exceeded {MAX_REQUEST_BYTES} bytes")
            if b"\n" in data:
                break

        req = json.loads(data.split(b"\n", 1)[0].strip())
        tiff_path = req["tiff_path"]
        output_path = req["output_path"]

        if not os.path.isabs(tiff_path) or not os.path.isabs(output_path):
            raise ValueError("tiff_path and output_path must be absolute paths")
        if not output_path.endswith(".png"):
            raise ValueError(f"output_path must end in .png: {output_path}")
        if not os.path.isdir(os.path.dirname(output_path)):
            raise ValueError(f"output_path parent directory does not exist: {output_path}")
        if not os.path.exists(tiff_path):
            raise FileNotFoundError(f"TIFF not found: {tiff_path}")

        elapsed_ms = run_inference(session, tiff_path, output_path,
                                   rgb_only=req.get("rgb_only") is True)
        log.info("Segmented %s in %.0f ms → %s", tiff_path, elapsed_ms, output_path)

        resp = json.dumps({"status": "ok", "inference_ms": round(elapsed_ms)}) + "\n"
        conn.sendall(resp.encode())

    except Exception as exc:
        log.exception("Inference failed: %s", exc)
        try:
            resp = json.dumps({"status": "error", "message": str(exc)}) + "\n"
            conn.sendall(resp.encode())
        except Exception:
            pass
    finally:
        conn.close()


def serve(session, socket_path: str, on_listening=None) -> None:
    if os.path.exists(socket_path):
        # lstat avoids following a symlink planted in /tmp by another user.
        if not stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            log.error("Path %s exists but is not a socket — refusing to unlink", socket_path)
            sys.exit(1)
        os.unlink(socket_path)

    parent = os.path.dirname(socket_path)
    if parent:
        try:
            os.makedirs(parent, mode=0o750, exist_ok=True)
        except PermissionError:
            log.error(
                "Cannot create socket directory %s — likely running outside systemd "
                "as a non-root user. Pass --socket /tmp/segformer_test.sock for "
                "manual testing.",
                parent,
            )
            sys.exit(1)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Unix sockets use 0o777 as base mode; umask 0o177 → 0o777 & ~0o177 = 0o600
    # (owner-only), since only the ticktalk process (same uid) should connect.
    old_umask = os.umask(0o177)
    try:
        server.bind(socket_path)
    finally:
        os.umask(old_umask)
    server.listen(4)
    log.info("Listening on %s", socket_path)
    if on_listening is not None:
        on_listening()

    def _shutdown(sig, frame):
        log.info("Received signal %s, shutting down", sig)
        server.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            break
        handle_connection(conn, session)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="SegFormer ONNX inference daemon")
    p.add_argument(
        "--model",
        default="/home/pi/segformer_5band/segformer_5band_int8.onnx",
        help="Path to ONNX model (INT8 preferred, FP32 accepted)",
    )
    p.add_argument(
        "--socket",
        default=SOCKET_PATH,
        help=f"Unix socket path (default: {SOCKET_PATH})",
    )
    p.add_argument(
        "--fallback-fp32",
        default="/home/pi/segformer_5band/segformer_5band_fp32.onnx",
        help="FP32 ONNX to use if INT8 model is not found",
    )
    return p.parse_args()


#: Next to the socket; tools/node_version.py reads it for the uplinks' model_id.
MODEL_INFO_NAME = "model.json"


def record_model(model_path: str, info_path: str, meta: dict | None = None) -> None:
    """Write which model is loaded ({"name", "path", "sha256"}); never raises.

    `meta` (the model's metadata_props) adds "model_name" and "model_version"
    when the model declares them; "name" stays the file name.

    The model can change with no git change at all (a new file, or a drop-in
    pointing --model elsewhere), so the node reports the file's own hash.
    """
    import hashlib

    try:
        real = os.path.realpath(model_path)
        digest = hashlib.sha256()
        with open(real, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        tmp = f"{info_path}.{os.getpid()}"
        with open(tmp, "w") as f:
            rec = {"name": os.path.basename(real), "path": real,
                   "sha256": digest.hexdigest()}
            for k in ("model_name", "model_version"):
                if (meta or {}).get(k):
                    rec[k] = meta[k]
            json.dump(rec, f)
        os.replace(tmp, info_path)
        log.info("Model %s%s sha256 %s", os.path.basename(real),
                 f" ({meta['model_name']} {meta.get('model_version', '')})"
                 if (meta or {}).get("model_name") else "", digest.hexdigest()[:8])
    except OSError as e:
        log.warning("Could not record the loaded model in %s: %s", info_path, e)


def load_first_usable(candidates: list):
    """(path, session) for the first candidate that exists and loads; else exits.

    A model the daemon refuses (`ModelContractError`) or cannot open is logged
    as an error and the next candidate (the FP32 fallback) is tried, so a bad
    drop-in does not leave the node without segmentation if the old model is
    still there.
    """
    tried = False
    for path in candidates:
        if not os.path.exists(path):
            log.warning("model not found at %s", path)
            continue
        tried = True
        try:
            return path, load_session(path)
        except Exception as e:                        # noqa: BLE001
            log.error("cannot use %s: %s", path, e)
    log.error("No usable ONNX model found%s. Run tools/export_segformer_onnx.py first.",
              "" if tried else " (none exist)")
    sys.exit(1)


def main():
    args = parse_args()

    candidates = [args.model]
    if args.fallback_fp32 and args.fallback_fp32 != args.model:
        candidates.append(args.fallback_fp32)
    model_path, session = load_first_usable(candidates)
    # Hashing the model reads the whole file, which on a Pi's SD card delays the
    # socket by seconds. The model id is only needed for uplinks, so do it in the
    # background once the socket is serving.
    info_path = os.path.join(os.path.dirname(args.socket), MODEL_INFO_NAME)
    meta = model_metadata(session)
    serve(session, args.socket,
          on_listening=lambda: threading.Thread(
              target=record_model, args=(model_path, info_path, meta),
              name="record-model", daemon=True).start())


if __name__ == "__main__":
    main()
