#!/usr/bin/env python
"""
Export the deployed SegFormer 5-band model to ONNX and apply INT8 static quantization.

Run this script on the Pi (inside the 5band conda env) or on any machine that has
access to the model checkpoint directory:

    /home/pi/miniforge3/envs/5band/bin/python tools/export_segformer_onnx.py \
        --checkpoint /home/pi/segformer_5band \
        --output /home/pi/segformer_5band \
        --calibration-dir /home/pi/SU-WaterCam/images

The script produces:
    segformer_5band_fp32.onnx   - FP32 ONNX model (benchmark baseline)
    segformer_5band_int8.onnx   - INT8 statically-quantized model (deployment target)

Prerequisites (already in the 5band conda env):
    pip install onnx onnxruntime torch transformers rasterio opencv-python-headless

For mmseg checkpoints (optional, alternative to HuggingFace):
    pip install mmengine mmsegmentation
"""

import argparse
import glob
import os

import numpy as np

from segformer_preprocess import preprocess_bands


# ---------------------------------------------------------------------------
# Model loading helpers
# ---------------------------------------------------------------------------

def load_model_huggingface(checkpoint_dir: str):
    """Load via HuggingFace transformers (most common SegFormer training setup)."""
    from transformers import SegformerForSemanticSegmentation
    model = SegformerForSemanticSegmentation.from_pretrained(checkpoint_dir)
    model.eval()
    return model


def load_model_mmseg(checkpoint_dir: str):
    """Load via mmseg config+checkpoint (alternative training framework)."""
    from mmseg.apis import init_model
    cfg_candidates = sorted(glob.glob(os.path.join(checkpoint_dir, "*.py")))
    ckpt_candidates = sorted(glob.glob(os.path.join(checkpoint_dir, "*.pth")))
    if not cfg_candidates or not ckpt_candidates:
        raise FileNotFoundError("No .py config or .pth checkpoint found for mmseg loader")
    if len(cfg_candidates) > 1:
        raise RuntimeError(
            f"Multiple .py configs found in {checkpoint_dir}: {cfg_candidates}. "
            "Move all but the correct config out of the directory."
        )
    if len(ckpt_candidates) > 1:
        raise RuntimeError(
            f"Multiple .pth checkpoints found in {checkpoint_dir}: {ckpt_candidates}. "
            "Move all but the correct checkpoint out of the directory."
        )
    model = init_model(cfg_candidates[0], ckpt_candidates[0], device="cpu")
    model.eval()
    return model, "mmseg"


def load_model(checkpoint_dir: str):
    """Try HuggingFace first, fall back to mmseg."""
    try:
        model = load_model_huggingface(checkpoint_dir)
        print(f"Loaded model via HuggingFace transformers from {checkpoint_dir}")
        return model, "huggingface"
    except Exception as hf_err:
        print(f"HuggingFace load failed ({hf_err}), trying mmseg...")
    try:
        return load_model_mmseg(checkpoint_dir)
    except Exception as mm_err:
        print(f"mmseg load failed ({mm_err})")
        raise RuntimeError(
            "Could not load model. Ensure the checkpoint directory contains either "
            "a HuggingFace config.json + pytorch_model.bin, or an mmseg *.py + *.pth."
        )


# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------

def export_to_onnx(model, output_path: str, framework: str,
                   height: int = 512, width: int = 512, n_bands: int = 5) -> None:
    import torch

    dummy = torch.randn(1, n_bands, height, width)

    if framework == "huggingface":
        input_names = ["pixel_values"]
        output_names = ["logits"]

        # HuggingFace SegformerForSemanticSegmentation returns a dataclass;
        # wrap it so torch.onnx.export sees a plain tensor output.
        class _Wrapper(torch.nn.Module):
            def __init__(self, m):
                super().__init__()
                self.m = m

            def forward(self, x):
                return self.m(pixel_values=x).logits

        wrapper = _Wrapper(model)
        torch.onnx.export(
            wrapper,
            dummy,
            output_path,
            input_names=input_names,
            output_names=output_names,
            dynamic_axes={
                "pixel_values": {0: "batch"},
                "logits": {0: "batch"},
            },
            opset_version=17,
            do_constant_folding=True,
        )
    else:
        # mmseg models accept a plain tensor in test mode
        torch.onnx.export(
            model,
            dummy,
            output_path,
            input_names=["input"],
            output_names=["output"],
            dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
            opset_version=17,
            do_constant_folding=True,
        )

    print(f"Exported ONNX FP32 model to {output_path}")


def verify_onnx(onnx_path: str, height: int = 512, width: int = 512, n_bands: int = 5) -> None:
    import onnx
    import onnxruntime as ort

    model = onnx.load(onnx_path)
    onnx.checker.check_model(model)
    print(f"ONNX model check passed: {onnx_path}")

    sess_opts = ort.SessionOptions()
    sess_opts.intra_op_num_threads = 4
    sess = ort.InferenceSession(onnx_path, sess_options=sess_opts,
                                providers=["CPUExecutionProvider"])
    dummy = np.random.rand(1, n_bands, height, width).astype(np.float32)
    input_name = sess.get_inputs()[0].name
    out = sess.run(None, {input_name: dummy})
    print(f"  Inference output shape: {out[0].shape}")
    print(f"  Output dtype: {out[0].dtype}")


# ---------------------------------------------------------------------------
# INT8 static quantization
# ---------------------------------------------------------------------------

def collect_calibration_paths(calibration_dir: str, n_images: int = 50) -> list[str]:
    """Walk calibration_dir for up to n_images final_5_band.tiff paths.

    Uses iglob to stop traversal early once n_images are collected, avoiding
    a full directory walk and in-memory materialisation of all matches.
    """
    paths = []
    pattern = os.path.join(calibration_dir, "**", "final_5_band.tiff")
    for p in glob.iglob(pattern, recursive=True):
        paths.append(p)
        if len(paths) >= n_images:
            break
    return sorted(paths)


class _CalibrationReader:
    """Streams calibration images from disk one at a time to avoid OOM on the Pi.

    With the default 50 images at 5×512×512 float32, pre-loading everything
    would consume ~500 MB; this reader holds at most one sample in memory.
    Falls back to pre-generated random arrays when tiff_paths is empty.
    """

    def __init__(self, tiff_paths: list[str], input_name: str,
                 height: int, width: int, n_bands: int,
                 random_fallback: list[np.ndarray] | None = None):
        self._paths = tiff_paths
        self._fallback = random_fallback
        self._input_name = input_name
        self._height = height
        self._width = width
        self._n_bands = n_bands
        self._idx = 0

    def get_next(self):
        if self._fallback is not None:
            if self._idx >= len(self._fallback):
                return None
            item = {self._input_name: self._fallback[self._idx]}
            self._idx += 1
            return item
        while self._idx < len(self._paths):
            path = self._paths[self._idx]
            self._idx += 1
            try:
                import rasterio
                with rasterio.open(path) as src:
                    img = src.read()  # preprocess_bands handles float32 conversion
                if img.shape[0] != self._n_bands:
                    print(f"  Skipping {path}: expected {self._n_bands} bands, got {img.shape[0]}")
                    continue
                return {self._input_name: preprocess_bands(img, self._height, self._width)}
            except Exception as e:
                print(f"  Skipping {path}: {e}")
        return None

    def rewind(self):
        self._idx = 0


def quantize_to_int8(fp32_onnx_path: str, int8_onnx_path: str,
                     tiff_paths: list[str],
                     height: int = 512, width: int = 512, n_bands: int = 5,
                     random_fallback: list[np.ndarray] | None = None) -> None:
    import onnxruntime as ort
    from onnxruntime.quantization import (
        QuantFormat,
        QuantType,
        quantize_static,
    )
    from onnxruntime.quantization.preprocess import quant_pre_process

    # Pre-process: fuse nodes and insert QDQ pairs at optimal locations.
    prep_path = fp32_onnx_path.replace(".onnx", "_prep.onnx")
    quant_pre_process(fp32_onnx_path, prep_path, skip_optimization=False)
    print(f"Pre-processed model saved to {prep_path}")

    # Determine the input name from the prepared model.
    sess = ort.InferenceSession(prep_path, providers=["CPUExecutionProvider"])
    input_name = sess.get_inputs()[0].name

    reader = _CalibrationReader(tiff_paths, input_name, height, width, n_bands,
                                random_fallback)

    # Softmax is not in onnxruntime's default quantizable op set, so it stays
    # FP32 automatically. LayerNormalization ops are also left to the default
    # policy; nodes_to_exclude expects exact ONNX node names (not op-type
    # prefixes), so op-type-based exclusion is not used here.
    quantize_static(
        model_input=prep_path,
        model_output=int8_onnx_path,
        calibration_data_reader=reader,
        quant_format=QuantFormat.QDQ,
        per_channel=True,
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        optimize_model=True,
    )
    print(f"INT8 quantized model saved to {int8_onnx_path}")


# ---------------------------------------------------------------------------
# Benchmarking
# ---------------------------------------------------------------------------

def benchmark(onnx_path: str, n_runs: int = 10,
              height: int = 512, width: int = 512, n_bands: int = 5) -> float:
    import time
    import onnxruntime as ort

    sess_opts = ort.SessionOptions()
    sess_opts.intra_op_num_threads = 4
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(onnx_path, sess_options=sess_opts,
                                providers=["CPUExecutionProvider"])

    input_name = sess.get_inputs()[0].name
    dummy = np.random.rand(1, n_bands, height, width).astype(np.float32)

    # Warmup
    for _ in range(2):
        sess.run(None, {input_name: dummy})

    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        sess.run(None, {input_name: dummy})
        times.append(time.perf_counter() - t0)

    mean_ms = np.mean(times) * 1000
    print(f"  {os.path.basename(onnx_path)}: {mean_ms:.0f} ms mean over {n_runs} runs")
    return mean_ms


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Export SegFormer 5-band to ONNX + INT8")
    p.add_argument("--checkpoint", default="/home/pi/segformer_5band",
                   help="Path to model checkpoint directory")
    p.add_argument("--output", default="/home/pi/segformer_5band",
                   help="Directory to write ONNX files")
    p.add_argument("--calibration-dir", default="/home/pi/SU-WaterCam/images",
                   help="Root directory to search for final_5_band.tiff calibration images")
    p.add_argument("--calibration-images", type=int, default=50,
                   help="Max calibration images to use (default 50)")
    p.add_argument("--height", type=int, default=512,
                   help="Inference height (should match INFERENCE_HEIGHT in coreg_multiple.py)")
    p.add_argument("--width", type=int, default=512,
                   help="Inference width (should match INFERENCE_WIDTH in coreg_multiple.py)")
    p.add_argument("--bands", type=int, default=5,
                   help="Number of input bands (default 5)")
    p.add_argument("--skip-quantization", action="store_true",
                   help="Export FP32 only, skip INT8 quantization")
    p.add_argument("--benchmark", action="store_true",
                   help="Benchmark FP32 and INT8 models after export")
    return p.parse_args()


def main():
    args = parse_args()

    os.makedirs(args.output, exist_ok=True)
    fp32_path = os.path.join(args.output, "segformer_5band_fp32.onnx")
    int8_path = os.path.join(args.output, "segformer_5band_int8.onnx")

    # 1. Load PyTorch model
    print("=== Loading model ===")
    model, framework = load_model(args.checkpoint)

    # 2. Export to ONNX FP32
    print("\n=== Exporting to ONNX FP32 ===")
    export_to_onnx(model, fp32_path, framework,
                   height=args.height, width=args.width, n_bands=args.bands)
    verify_onnx(fp32_path, height=args.height, width=args.width, n_bands=args.bands)

    if not args.skip_quantization:
        # 3. Collect calibration paths
        print("\n=== Collecting calibration data ===")
        calib_paths = collect_calibration_paths(args.calibration_dir, args.calibration_images)
        if not calib_paths:
            print(
                f"WARNING: No final_5_band.tiff files found under {args.calibration_dir}. "
                "Using random calibration data — quantization accuracy will be suboptimal."
            )
            random_fallback = [
                np.random.rand(1, args.bands, args.height, args.width).astype(np.float32)
                for _ in range(16)
            ]
        else:
            print(f"Found {len(calib_paths)} calibration image(s) in {args.calibration_dir}")
            random_fallback = None

        # 4. INT8 static quantization
        print("\n=== Quantizing to INT8 ===")
        quantize_to_int8(fp32_path, int8_path, calib_paths,
                         height=args.height, width=args.width, n_bands=args.bands,
                         random_fallback=random_fallback)
        verify_onnx(int8_path, height=args.height, width=args.width, n_bands=args.bands)

    # 5. Optional benchmark
    if args.benchmark:
        print("\n=== Benchmark ===")
        benchmark(fp32_path, height=args.height, width=args.width, n_bands=args.bands)
        if not args.skip_quantization and os.path.exists(int8_path):
            benchmark(int8_path, height=args.height, width=args.width, n_bands=args.bands)

    print("\nDone.")
    print(f"  FP32: {fp32_path}")
    if not args.skip_quantization:
        print(f"  INT8: {int8_path}")
    print("\nNext step: start the segformer daemon:")
    print("  sudo systemctl enable --now segformer_daemon.service")


if __name__ == "__main__":
    main()
