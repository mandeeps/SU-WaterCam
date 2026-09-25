#!/usr/bin/env python
"""
SegFormer inference daemon.

Keeps the ONNX model loaded in memory and accepts inference requests over a
Unix domain socket, eliminating the 10–20 s cold-start cost that occurs when
segment_tiff_5band.py is spawned as a fresh subprocess each wake cycle.

Protocol
--------
Client → server (newline-terminated JSON):
    {"tiff_path": "/abs/path/final_5_band.tiff",
     "output_path": "/abs/path/final_5_band_segmentation.png"}

Server → client (newline-terminated JSON):
    {"status": "ok",   "inference_ms": 1234}
    {"status": "error","message": "..."}

Usage
-----
Run directly for testing (pass an explicit socket path under /tmp, because
/run/segformer/ is created by systemd RuntimeDirectory and will not exist
for a non-root user running outside systemd):

    /home/pi/miniforge3/envs/5band/bin/python tools/segformer_daemon.py \
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
import time

import numpy as np

from segformer_preprocess import (normalization_mode, normalization_spec,
                                  preprocess_bands)

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
                        spec: dict | None = None) -> np.ndarray:
    import rasterio

    with rasterio.open(tiff_path) as src:
        img = src.read()  # (bands, H, W); preprocess_bands handles float32 conversion

    return preprocess_bands(img, expected_h, expected_w, spec=spec)


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


def run_inference(session, tiff_path: str, output_path: str) -> float:
    import cv2
    import rasterio
    from PIL import Image

    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape  # [batch, bands, H, W]

    with rasterio.open(tiff_path) as src:
        ori_h, ori_w = src.height, src.width

    # A static graph fixes its own input size. A dynamic one does not, and
    # squashing the capture into a square would destroy its aspect ratio —
    # this mask is georeferenced downstream from IMU pose, so a distorted mask
    # distorts the georeferencing, silently and without error.
    static_h = input_shape[2] if isinstance(input_shape[2], int) else None
    static_w = input_shape[3] if isinstance(input_shape[3], int) else None
    if static_h and static_w:
        expected_h, expected_w = static_h, static_w
    else:
        expected_h, expected_w = keep_ratio_size(ori_h, ori_w)

    # The model states how it was normalised; anything else hands it a
    # distribution it never saw in training, silently and without error.
    arr = load_and_preprocess(tiff_path, expected_h, expected_w,
                              spec=normalization_spec(session))

    # Pad up to SIZE_DIVISOR so the graph's scale_factor upsample is exact.
    # Zeros are the post-Normalize mean, matching what the torch path pads with.
    pad_h = (-expected_h) % SIZE_DIVISOR
    pad_w = (-expected_w) % SIZE_DIVISOR
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

    # Scale class indices to full 0–255 range so the PNG is human-readable.
    # Float division ensures the highest index maps exactly to 255
    # (integer division, e.g. 255//4=63, would cap at 252 for 5 classes).
    n_classes = logits.shape[1]
    if n_classes > 1:
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

        elapsed_ms = run_inference(session, tiff_path, output_path)
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


def serve(session, socket_path: str) -> None:
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


def main():
    args = parse_args()

    model_path = args.model
    if not os.path.exists(model_path):
        log.warning("INT8 model not found at %s, trying FP32 fallback", model_path)
        model_path = args.fallback_fp32
        if not os.path.exists(model_path):
            log.error(
                "No ONNX model found. Run tools/export_segformer_onnx.py first."
            )
            sys.exit(1)

    session = load_session(model_path)
    serve(session, args.socket)


if __name__ == "__main__":
    main()
