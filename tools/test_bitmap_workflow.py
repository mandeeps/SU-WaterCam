#!/usr/bin/env python3
"""
Test script for the full bitmap generation and transmission workflow.

Calls existing code to:
  1. Take regular and NIR photos (optional: use --existing-dir to skip)
  2. Take LWIR photos (Lepton) (optional: use --existing-dir to skip)
  3. Run coregistration
  4. Run SegFormer on the 5-band TIFF
  5. Generate compressed bitmap from segmentation
  6. Queue and send the bitmap via the existing LoRa handler (optional: --no-lora to skip)

Usage:
  # Full workflow (capture, coreg, segformer, bitmap, LoRa)
  python tools/test_bitmap_workflow.py [--output-dir DIR]

  # Use an existing capture directory (skip camera/Lepton)
  python tools/test_bitmap_workflow.py --existing-dir /path/to/images/YYYYMMDD-HHMMSS

  # Generate bitmap and skip LoRa transmission
  python tools/test_bitmap_workflow.py --existing-dir /path/to/dir --no-lora
"""

import argparse
import os
import subprocess
import sys


def get_default_images_base():
    """Same base path as ticktalk_main get_time()."""
    return os.environ.get("SU_WATERCAM_IMAGES", "/home/pi/SU-WaterCam/images")


def run_capture(base_path: str) -> str:
    """Take regular + NIR photos and LWIR (Lepton). Uses tools.take_nir_photos."""
    from tools import take_nir_photos

    # main() creates base_path/YYYYMMDD-HHMMSS and returns (basename, directory)
    _, directory = take_nir_photos.main(base_path)
    take_nir_photos.flir(directory)
    return directory


def run_coregistration(directory: str) -> str:
    """Run coregistration on directory. Uses tools.coreg_multiple.coreg."""
    from tools.coreg_multiple import coreg

    coreg(directory)
    return directory


#: Canonical five-band names, mirroring tools/coreg_multiple.py. Duplicated
#: rather than imported because this script runs standalone on a node where
#: coreg_multiple's heavy dependencies may not be importable.
MODEL_INPUT_TIFF = "color_preserved_5_band.tiff"
SEGMENTATION_PNG = "color_preserved_5_band_segmentation.png"
LEGACY_SEGMENTATION_PNG = "final_5_band_segmentation.png"


def _segmentation_path(directory: str) -> str:
    """Prefer the standard mask name; accept the superseded one."""
    std = os.path.join(directory, SEGMENTATION_PNG)
    if os.path.exists(std):
        return std
    legacy = os.path.join(directory, LEGACY_SEGMENTATION_PNG)
    return legacy if os.path.exists(legacy) else std


def run_segformer(directory: str) -> str:
    """Run SegFormer on the five-band TIFF. Same subprocess as ticktalk_main."""
    segformer_python = os.environ.get(
        "SEGFORMER_PYTHON", "/home/pi/miniforge3/envs/5band/bin/python"
    )
    segformer_script = os.environ.get(
        "SEGFORMER_SCRIPT", "/home/pi/segformer_5band/segment_tiff_5band.py"
    )
    segformer_cwd = os.environ.get("SEGFORMER_CWD", "/home/pi/segformer_5band")
    tiff_path = os.path.join(directory, MODEL_INPUT_TIFF)
    if not os.path.isfile(tiff_path):
        raise FileNotFoundError(f"Coreg output not found: {tiff_path}")
    proc = subprocess.Popen(
        [segformer_python, segformer_script, tiff_path],
        cwd=segformer_cwd,
    )
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"SegFormer exited with code {proc.returncode}")
    return os.path.join(directory, SEGMENTATION_PNG)


def generate_bitmap(segmentation_path: str) -> bytes:
    """Compress segmentation image to bitmap bytes. Uses tools.compress_segmented.compress_image."""
    from tools.compress_segmented import compress_image

    result = compress_image(segmentation_path)
    if not result.get("success"):
        raise RuntimeError("Bitmap compression failed")
    return result["compressed_data"]


def queue_and_send_bitmap(bitmap_bytes: bytes) -> None:
    """Queue bitmap for transmission using existing LoRa handler (TLV 0x08/0x18)."""
    from tools.lora_handler_concurrent import get_lora_handler

    handler = get_lora_handler()
    # Use the same encoding as main flow: flood_bitmap_compressed -> add_blob(0x08, 0x18, ...)
    handler.queue_transmit({"flood_bitmap_compressed": bitmap_bytes})
    handler.process_transmit_queue()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test bitmap generation and transmission workflow using existing code."
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Base directory for new captures (default: SU_WATERCAM_IMAGES or /home/pi/SU-WaterCam/images)",
    )
    parser.add_argument(
        "--existing-dir",
        default=None,
        help=f"Use this directory instead of capturing; must contain NIR-OFF, NIR-ON, and .pgm, or already {MODEL_INPUT_TIFF} / {SEGMENTATION_PNG}",
    )
    parser.add_argument(
        "--no-lora",
        action="store_true",
        help="Do not send bitmap over LoRa (only generate and print size)",
    )
    parser.add_argument(
        "--skip-segformer",
        action="store_true",
        help=f"Skip SegFormer; require existing {SEGMENTATION_PNG} in --existing-dir",
    )
    args = parser.parse_args()

    base_path = args.output_dir or get_default_images_base()
    if args.existing_dir:
        directory = os.path.abspath(args.existing_dir)
        if not os.path.isdir(directory):
            print(f"Error: --existing-dir is not a directory: {directory}", file=sys.stderr)
            return 1
        print(f"Using existing directory: {directory}")
    else:
        try:
            directory = run_capture(base_path)
        except Exception as e:
            print(f"Capture failed: {e}", file=sys.stderr)
            return 1
        print(f"Capture directory: {directory}")

    # Coregistration (unless the TIFF is already there and we are skipping segformer with existing seg)
    tiff_path = os.path.join(directory, MODEL_INPUT_TIFF)
    if not os.path.isfile(tiff_path):
        try:
            run_coregistration(directory)
        except Exception as e:
            print(f"Coregistration failed: {e}", file=sys.stderr)
            return 1
    else:
        print(f"{MODEL_INPUT_TIFF} already present, skipping coregistration.")

    # SegFormer
    seg_path = _segmentation_path(directory)
    if args.skip_segformer:
        if not os.path.isfile(seg_path):
            print(f"Error: --skip-segformer but no {seg_path}", file=sys.stderr)
            return 1
        print("Using existing segmentation (--skip-segformer).")
    else:
        try:
            seg_path = run_segformer(directory)
        except Exception as e:
            print(f"SegFormer failed: {e}", file=sys.stderr)
            return 1

    # Bitmap
    try:
        bitmap_bytes = generate_bitmap(seg_path)
    except Exception as e:
        print(f"Bitmap generation failed: {e}", file=sys.stderr)
        return 1
    print(f"Bitmap size: {len(bitmap_bytes)} bytes")

    if args.no_lora:
        print("Skipping LoRa transmission (--no-lora).")
        return 0

    try:
        queue_and_send_bitmap(bitmap_bytes)
        print("Bitmap queued and transmitted via LoRa handler.")
    except Exception as e:
        print(f"LoRa transmission failed: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
