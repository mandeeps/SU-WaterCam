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
Run directly for testing:
    /home/pi/miniforge3/envs/5band/bin/python tools/segformer_daemon.py \
        --model /home/pi/segformer_5band/segformer_5band_int8.onnx

Or via systemd (see config/segformer_daemon.service).

The socket path is /tmp/segformer.sock by default. ticktalk_main.py's
segformer() function connects to this socket if it exists, and falls back to
the legacy subprocess call otherwise.
"""

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

SOCKET_PATH = "/tmp/segformer.sock"
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
    log.info(
        "Model loaded: %s | input '%s' %s",
        os.path.basename(model_path),
        input_meta.name,
        input_meta.shape,
    )
    return session


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def load_and_preprocess(tiff_path: str, expected_h: int, expected_w: int) -> np.ndarray:
    import rasterio
    import cv2

    with rasterio.open(tiff_path) as src:
        img = src.read().astype(np.float32)  # (bands, H, W)

    # Resize if the TIFF dimensions differ from the model's expected input.
    if img.shape[1] != expected_h or img.shape[2] != expected_w:
        img = np.stack(
            [cv2.resize(img[i], (expected_w, expected_h), interpolation=cv2.INTER_AREA)
             for i in range(img.shape[0])],
            axis=0,
        )

    # Per-band min-max normalisation to [0, 1]
    for i in range(img.shape[0]):
        lo, hi = float(img[i].min()), float(img[i].max())
        if hi > lo:
            img[i] = (img[i] - lo) / (hi - lo)

    return img[np.newaxis].astype(np.float32)  # (1, bands, H, W)


def run_inference(session, tiff_path: str, output_path: str) -> float:
    from PIL import Image

    input_name = session.get_inputs()[0].name
    input_shape = session.get_inputs()[0].shape  # [batch, bands, H, W]

    # Shape may be symbolic (strings) for dynamic axes; fall back to 512.
    expected_h = input_shape[2] if isinstance(input_shape[2], int) else 512
    expected_w = input_shape[3] if isinstance(input_shape[3], int) else 512

    arr = load_and_preprocess(tiff_path, expected_h, expected_w)

    t0 = time.perf_counter()
    logits = session.run(None, {input_name: arr})[0]  # (1, classes, h, w)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    pred = np.argmax(logits[0], axis=0).astype(np.uint8)

    # Scale class indices to full 0–255 range so the PNG is human-readable.
    n_classes = logits.shape[1]
    if n_classes > 1:
        pred_vis = (pred * (255 // (n_classes - 1))).astype(np.uint8)
    else:
        pred_vis = pred

    Image.fromarray(pred_vis).save(output_path)
    return elapsed_ms


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

def handle_connection(conn: socket.socket, session) -> None:
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
            if len(data) > MAX_REQUEST_BYTES:
                raise ValueError(f"Request exceeded {MAX_REQUEST_BYTES} bytes")
            if b"\n" in data:
                break

        req = json.loads(data.split(b"\n", 1)[0].strip())
        tiff_path = req["tiff_path"]
        output_path = req["output_path"]

        if not os.path.exists(tiff_path):
            raise FileNotFoundError(f"TIFF not found: {tiff_path}")

        elapsed_ms = run_inference(session, tiff_path, output_path)
        log.info("Segmented %s in %.0f ms → %s", tiff_path, elapsed_ms, output_path)

        resp = json.dumps({"status": "ok", "inference_ms": round(elapsed_ms)}) + "\n"
        conn.sendall(resp.encode())

    except Exception as exc:
        log.error("Inference failed: %s", exc)
        try:
            resp = json.dumps({"status": "error", "message": str(exc)}) + "\n"
            conn.sendall(resp.encode())
        except Exception:
            pass
    finally:
        conn.close()


def serve(session, socket_path: str) -> None:
    if os.path.exists(socket_path):
        if not stat.S_ISSOCK(os.stat(socket_path).st_mode):
            log.error("Path %s exists but is not a socket — refusing to unlink", socket_path)
            sys.exit(1)
        os.unlink(socket_path)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    os.chmod(socket_path, 0o660)
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
