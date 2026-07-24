#!/usr/bin/env python3
"""
DEPRECATED: superseded by tools/camera_calibration.py, a copy of the
canonical calibration tool from this project's separate Georeferencing
codebase (not published on GitHub). That script's output schema
(K/D/img_size/rms) is what tools/coreg_multiple.py actually reads; this
script's
camera_matrix/dist_coeffs schema is not consumed anywhere. Kept around only
for on-device Picamera2 capture convenience (tools/camera_calibration.py
uses cv2.VideoCapture, which can't drive the Pi CSI camera) — if you use
this script to capture images, run them through tools/camera_calibration.py's
calibrate_camera() rather than this script's own calibration/output step.

Camera calibration and parameter collection script.

This script helps you calibrate the main RGB/NIR camera and save
intrinsic parameters for later use in georeferencing and image
coregistration.

Default chessboard pattern
--------------------------
- Inner corners: 24 (columns) × 17 (rows)
  - This corresponds to the 25×18-square calib.io checkerboard used for
    this project's calibration.
- Physical square size: 0.03 m (30 mm) per square edge.
  - You can change this with --square-size, but it MUST match the
    actual printed square size to get a correct scale in meters.

It supports two workflows:
- Capture calibration images directly from Picamera2
- Calibrate from an existing directory of chessboard images

Typical usage (on the device):
    # Capture and calibrate in one go (using Picamera2)
    python3 tools/camera_calibration.py --capture --output calibration/camera_calibration.json

    # Calibrate from an existing directory of images
    python3 tools/camera_calibration.py --image-dir calibration/images --output calibration/camera_calibration.json

The output JSON contains:
- image_size: [width, height]
- camera_matrix: 3x3 intrinsics
- dist_coeffs: distortion coefficients
- reprojection_error: mean reprojection error
- fov_degrees: horizontal/vertical/diagonal estimated from intrinsics
"""

import argparse
import json
import math
import os
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np


def create_chessboard_object_points(board_size: Tuple[int, int], square_size: float) -> np.ndarray:
    """
    Create the 3D object points for a planar chessboard pattern.

    board_size: (cols, rows) inner corners (e.g. 9x6)
    square_size: physical size of each square (in meters, or any consistent unit)
    """
    cols, rows = board_size
    objp = np.zeros((rows * cols, 3), np.float32)
    # X axis: columns, Y axis: rows; Z = 0 (planar board)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objp *= square_size
    return objp


def find_chessboard_corners(
    image: np.ndarray,
    board_size: Tuple[int, int],
    criteria: Tuple[int, int, float],
) -> Tuple[bool, np.ndarray]:
    """Detect and refine chessboard corners in a single image."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    ret, corners = cv2.findChessboardCorners(gray, board_size, None)
    if not ret:
        return False, None  # type: ignore[return-value]

    # Refine corner locations
    corners_subpix = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
    return True, corners_subpix


def calibrate_from_images(
    image_paths: List[Path],
    board_size: Tuple[int, int],
    square_size: float,
) -> dict:
    """
    Run camera calibration from a list of image files.

    Returns a dict with calibration results suitable for JSON serialization.
    """
    if not image_paths:
        raise RuntimeError("No calibration images provided.")

    # Termination criteria for corner refinement
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.001,
    )

    objp = create_chessboard_object_points(board_size, square_size)
    objpoints: List[np.ndarray] = []  # 3D points in world space
    imgpoints: List[np.ndarray] = []  # 2D points in image plane

    image_size = None
    used_images = 0

    for path in image_paths:
        img = cv2.imread(str(path))
        if img is None:
            print(f"Skipping unreadable image: {path}")
            continue

        if image_size is None:
            h, w = img.shape[:2]
            image_size = (w, h)
        else:
            h, w = img.shape[:2]
            if (w, h) != image_size:
                print(f"Skipping {path} due to size mismatch: {(w, h)} != {image_size}")
                continue

        found, corners = find_chessboard_corners(img, board_size, criteria)
        if not found:
            print(f"Chessboard not found in {path}, skipping.")
            continue

        objpoints.append(objp)
        imgpoints.append(corners)
        used_images += 1

    if image_size is None or used_images < 3:
        raise RuntimeError(
            f"Not enough valid calibration images. "
            f"Need at least 3 with detected chessboard. Got {used_images}."
        )

    print(f"Running calibration with {used_images} images, image_size={image_size}")

    # Calibrate camera
    ret, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        objpoints,
        imgpoints,
        image_size,
        None,
        None,
    )

    # Compute reprojection error
    total_error = 0.0
    total_points = 0
    for i in range(len(objpoints)):
        imgpoints2, _ = cv2.projectPoints(
            objpoints[i],
            rvecs[i],
            tvecs[i],
            camera_matrix,
            dist_coeffs,
        )
        err = cv2.norm(imgpoints[i], imgpoints2, cv2.NORM_L2)
        n = len(imgpoints[i])
        total_error += err * err
        total_points += n

    mean_error = math.sqrt(total_error / total_points) if total_points > 0 else float("nan")
    print(f"Mean reprojection error: {mean_error:.4f} pixels")

    # Estimate FOV from intrinsics
    fx = float(camera_matrix[0, 0])
    fy = float(camera_matrix[1, 1])
    cx = float(camera_matrix[0, 2])
    cy = float(camera_matrix[1, 2])
    width, height = image_size

    fov_x = 2.0 * math.degrees(math.atan(width / (2.0 * fx)))
    fov_y = 2.0 * math.degrees(math.atan(height / (2.0 * fy)))
    fov_diag = 2.0 * math.degrees(
        math.atan(math.hypot(width, height) / (2.0 * (fx + fy) * 0.5))
    )

    result = {
        "image_size": {"width": width, "height": height},
        "board_size": {"cols": board_size[0], "rows": board_size[1]},
        "square_size": square_size,
        "camera_matrix": camera_matrix.tolist(),
        "dist_coeffs": dist_coeffs.ravel().tolist(),
        "reprojection_error": mean_error,
        "principal_point": {"cx": cx, "cy": cy},
        "fov_degrees": {
            "horizontal": fov_x,
            "vertical": fov_y,
            "diagonal": fov_diag,
        },
        "num_images_used": used_images,
    }
    return result


def collect_images_from_camera(
    output_dir: Path,
    board_size: Tuple[int, int],
    square_size: float,
    num_images: int,
) -> List[Path]:
    """
    Capture calibration images from Picamera2 until we have enough valid chessboard detections.

    Images with a detected chessboard are saved to output_dir and returned as a list of paths.
    """
    try:
        from picamera2 import Picamera2
    except ImportError:
        raise RuntimeError(
            "Picamera2 is not available. Install python3-picamera2 as a system package."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"format": "RGB888", "size": (2592, 1944)}
    )
    picam2.configure(config)
    picam2.start()

    print(
        f"Collecting calibration images into {output_dir} "
        f"(need {num_images} with detected chessboard)."
    )
    print(
        f"Board size (inner corners): {board_size[0]} x {board_size[1]}, "
        f"square_size={square_size}"
    )
    print("Press Ctrl+C to stop early.")

    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.001,
    )

    saved_paths: List[Path] = []
    captured = 0

    try:
        while len(saved_paths) < num_images:
            frame = picam2.capture_array()
            if frame is None:
                print("Warning: failed to capture frame, retrying...")
                continue

            found, corners = find_chessboard_corners(frame, board_size, criteria)
            captured += 1

            if not found:
                print(f"[{captured}] Chessboard not detected, skipping.")
                continue

            # Draw and save visualization
            vis = frame.copy()
            cv2.drawChessboardCorners(vis, board_size, corners, True)

            filename = output_dir / f"calib_{captured:03d}.png"
            cv2.imwrite(str(filename), vis)
            saved_paths.append(filename)
            print(f"[{captured}] Detected chessboard, saved {filename}")

    except KeyboardInterrupt:
        print("Capture interrupted by user.")
    finally:
        try:
            picam2.close()
        except Exception:
            pass

    if len(saved_paths) < 3:
        print(
            f"Warning: only {len(saved_paths)} valid images captured. "
            "Calibration may be unreliable."
        )

    return saved_paths


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate the RGB/NIR camera and export parameters for georeferencing.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--image-dir",
        type=str,
        help="Directory containing chessboard calibration images.",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Capture calibration images from Picamera2 before calibrating.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="camera_calibration.json",
        help="Path to write calibration JSON.",
    )
    parser.add_argument(
        "--board-cols",
        type=int,
        default=24,
        help="Number of inner corners along the chessboard width (columns).",
    )
    parser.add_argument(
        "--board-rows",
        type=int,
        default=17,
        help="Number of inner corners along the chessboard height (rows).",
    )
    parser.add_argument(
        "--square-size",
        type=float,
        default=0.03,
        help="Physical size of one square (meters or consistent units).",
    )
    parser.add_argument(
        "--num-images",
        type=int,
        default=20,
        help="Target number of valid calibration images.",
    )

    args = parser.parse_args()

    board_size = (args.board_cols, args.board_rows)
    square_size = float(args.square_size)

    image_dir: Path
    image_paths: List[Path] = []

    if args.capture:
        # If capture is requested, collect images to a new directory (or reuse image-dir if provided).
        if args.image_dir:
            image_dir = Path(args.image_dir)
        else:
            image_dir = Path("calibration_images")
        print(f"Using image directory: {image_dir}")
        image_paths = collect_images_from_camera(
            image_dir,
            board_size=board_size,
            square_size=square_size,
            num_images=args.num_images,
        )
    else:
        if not args.image_dir:
            raise SystemExit(
                "Either --capture or --image-dir must be provided. "
                "Use --capture to collect images from Picamera2."
            )
        image_dir = Path(args.image_dir)
        if not image_dir.is_dir():
            raise SystemExit(f"Image directory not found: {image_dir}")
        # Use all image files in directory
        exts = {".png", ".jpg", ".jpeg", ".bmp"}
        image_paths = [
            p
            for p in sorted(image_dir.iterdir())
            if p.is_file() and p.suffix.lower() in exts
        ]

    if not image_paths:
        raise SystemExit("No calibration images found.")

    print(f"Found {len(image_paths)} calibration images.")

    calibration = calibrate_from_images(
        image_paths=image_paths,
        board_size=board_size,
        square_size=square_size,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(calibration, f, indent=2)

    print(f"\nCalibration saved to {output_path}")
    print("Summary:")
    print(f"  Image size       : {calibration['image_size']}")
    print(f"  Reproj. error    : {calibration['reprojection_error']:.4f} px")
    print(
        f"  FOV (h/v/diag)   : "
        f"{calibration['fov_degrees']['horizontal']:.1f} / "
        f"{calibration['fov_degrees']['vertical']:.1f} / "
        f"{calibration['fov_degrees']['diagonal']:.1f} deg"
    )


if __name__ == "__main__":
    main()

