"""
Camera Calibration & Georeferencing Pipeline
=============================================
DEPENDENCIES:
    pip install opencv-python numpy scipy Pillow piexif pyproj

WORKFLOW OVERVIEW:
    1. Capture calibration images of a checkerboard
    2. Detect corners and compute intrinsic parameters (K, D)
    3. Save/load calibration parameters
    4. Undistort images using computed parameters
    5. Georeference image pixels using GPS metadata + camera geometry
"""

import cv2
import numpy as np
import json
import os
import glob
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from camera_geometry import build_rotation_matrix
from geo_core import pixel_to_world_flat

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# STEP 1: Set your checkerboard dimensions.
# These are the number of INTERIOR corners (not squares).
# Example: a 9x6 board has 8x5 interior corners.
BOARD_W = 24          # interior corners horizontally
BOARD_H = 17          # interior corners vertically
SQUARE_SIZE_M = 0.03  # physical size of one square in meters (e.g. 2.5 cm)

BOARD_SIZE = (BOARD_W, BOARD_H)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CameraIntrinsics:
    """
    Stores intrinsic camera parameters.

    K (camera matrix):
        [[fx,  0, cx],
         [ 0, fy, cy],
         [ 0,  0,  1]]
        fx, fy = focal lengths in pixels
        cx, cy = principal point (optical center in pixels)

    D (distortion coefficients):
        [k1, k2, p1, p2, k3] or with CALIB_RATIONAL_MODEL [k1, k2, p1, p2, k3, k4, k5, k6]
        k1..k3 (and optionally k4..k6) = radial; p1, p2 = tangential

    rms: reprojection error in pixels (lower is better; <1.0 is good)
    camera_height_m: optional mounting height above ground (saved for georeferencing)
    """
    K: list          # 3x3 camera matrix as nested list
    D: list          # 1x5 or 1x8 distortion coefficients
    img_size: list   # [width, height]
    rms: float       # reprojection error
    camera_height_m: Optional[float] = None  # height above ground in meters (user-provided)

    def K_np(self):
        return np.array(self.K, dtype=np.float64)

    def D_np(self):
        return np.array(self.D, dtype=np.float64)

    def save(self, path: str):
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2)
        print(f"Calibration saved to {path}")

    @classmethod
    def load(cls, path: str):
        with open(path) as f:
            d = json.load(f)
        # Support older calibration files without camera_height_m
        return cls(
            K=d["K"],
            D=d["D"],
            img_size=d["img_size"],
            rms=d["rms"],
            camera_height_m=d.get("camera_height_m"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: CAPTURE CALIBRATION IMAGES (optional live capture)
# ─────────────────────────────────────────────────────────────────────────────

def capture_calibration_images(output_dir: str, n_images: int = 20, camera_index: int = 0):
    """
    STEP 2 (Live capture): Open a camera feed and save frames when SPACE is
    pressed. Press Q to quit.

    INSTRUCTIONS (for low reprojection error):
      - Print a checkerboard pattern (search "OpenCV checkerboard PDF" online).
      - Mount it flat on a rigid surface — no warping.
      - Vary pose: tilt the board (not always level), different distances (near/far),
        and positions (center, corners, edges). Level-only or same-distance views
        give high RMS; variety is essential.
      - Aim for 15-25 images with varied orientations.
      - Avoid motion blur; ensure good, even lighting.

    Alternatively, skip this and point `calibrate_camera()` at a folder of
    pre-captured images.
    """
    os.makedirs(output_dir, exist_ok=True)
    cap = cv2.VideoCapture(camera_index)
    count = 0

    print(f"[CAPTURE] Press SPACE to save image, Q to quit. Target: {n_images} images.")

    while count < n_images:
        ret, frame = cap.read()
        if not ret:
            print("Camera read failed.")
            break

        display = frame.copy()
        cv2.putText(display, f"Saved: {count}/{n_images}  SPACE=save  Q=quit",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        cv2.imshow("Calibration Capture", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord(' '):
            path = os.path.join(output_dir, f"calib_{count:03d}.jpg")
            cv2.imwrite(path, frame)
            print(f"  Saved {path}")
            count += 1
        elif key == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print(f"[CAPTURE] Done. {count} images saved to {output_dir}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: INTRINSIC CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

def _reprojection_errors_per_image(objpoints, imgpoints, K, D, rvecs, tvecs):
    """Compute RMS reprojection error per image (in pixels)."""
    errors = []
    for i in range(len(objpoints)):
        projected, _ = cv2.projectPoints(
            objpoints[i], rvecs[i], tvecs[i], K, D
        )
        projected = projected.reshape(-1, 2)
        diff = imgpoints[i].reshape(-1, 2) - projected
        rms_i = np.sqrt(np.mean(diff ** 2))
        errors.append(float(rms_i))
    return np.array(errors)


def calibrate_camera(image_dir: str, save_path: str = "calibration.json",
                     show_corners: bool = True,
                     reject_outliers: bool = True,
                     outlier_threshold_px: float = 2.0,
                     use_rational_model: bool = False,
                     max_outlier_rounds: int = 3,
                     camera_height_m: Optional[float] = None,
                     board_w: Optional[int] = None,
                     board_h: Optional[int] = None,
                     square_size_m: Optional[float] = None) -> CameraIntrinsics:
    """
    STEP 3: Detect checkerboard corners in all images and compute intrinsics.

    WHAT THIS DOES:
      - Finds the 2D positions of checkerboard corners in each image
      - Pairs them with known 3D positions (flat board = Z=0)
      - Solves for K and D using OpenCV's calibrateCamera()
      - Optionally removes images with high per-image error and re-calibrates

    INSTRUCTIONS FOR LOWER RMS ERROR:
      - Vary board pose: tilt the board (left/right, up/down), don't keep it
        always level. Level-only views give poor constraint on intrinsics.
      - Vary distance: include both near and far shots so the board fills
        different portions of the frame (reduces correlation between K and pose).
      - Move the board to different positions: corners, center, edges of frame.
      - Ensure SQUARE_SIZE_M is exact (measure with a ruler).
      - Keep board flat and rigid; avoid motion blur and uneven lighting.
    Aim for RMS < 1.0 pixel. If higher: add more varied poses or enable
    reject_outliers to drop bad frames.

    board_w, board_h, square_size_m:
        If provided, override module-level BOARD_W, BOARD_H, SQUARE_SIZE_M
        (interior corners and square size in metres).
    """
    bw = int(board_w) if board_w is not None else BOARD_W
    bh = int(board_h) if board_h is not None else BOARD_H
    sq = float(square_size_m) if square_size_m is not None else SQUARE_SIZE_M
    board_size = (bw, bh)

    # 3D object points for one board image (Z=0 since board is flat)
    objp = np.zeros((bw * bh, 3), np.float32)
    objp[:, :2] = np.mgrid[0:bw, 0:bh].T.reshape(-1, 2)
    objp *= sq

    objpoints = []  # 3D points in world space
    imgpoints = []  # 2D points in image space
    valid_paths = []  # paths for images that had corners found (same order as objpoints)
    img_size  = None

    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    image_paths = sorted(glob.glob(os.path.join(image_dir, "*.jpg")) +
                         glob.glob(os.path.join(image_dir, "*.png")))

    if not image_paths:
        raise FileNotFoundError(f"No .jpg or .png images found in {image_dir}")

    print(f"[CALIBRATE] Processing {len(image_paths)} images...")

    for path in image_paths:
        img  = cv2.imread(path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img_size = (gray.shape[1], gray.shape[0])  # (width, height)

        found, corners = cv2.findChessboardCorners(gray, board_size, None)

        if found:
            # Refine corner positions to sub-pixel accuracy
            corners_refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            objpoints.append(objp)
            imgpoints.append(corners_refined)
            valid_paths.append(path)

            if show_corners:
                vis = cv2.drawChessboardCorners(img.copy(), board_size, corners_refined, found)
                cv2.imshow("Detected Corners", vis)
                cv2.waitKey(200)
            print(f"  ✓ {Path(path).name}")
        else:
            print(f"  ✗ {Path(path).name} — corners not found (check lighting/board size)")

    cv2.destroyAllWindows()

    if len(objpoints) < 6:
        raise RuntimeError(f"Only {len(objpoints)} valid images. Need at least 6.")

    # Calibration flags: rational model adds k4,k5,k6 and can improve wide-angle/phone lenses
    calib_flags = 0
    if use_rational_model:
        calib_flags |= cv2.CALIB_RATIONAL_MODEL

    for round_num in range(max_outlier_rounds):
        n_used = len(objpoints)
        print(f"\n[CALIBRATE] Running calibration on {n_used} images (round {round_num + 1})...")

        rms, K, D, rvecs, tvecs = cv2.calibrateCamera(
            objpoints, imgpoints, img_size, None, None, flags=calib_flags
        )

        per_image_errors = _reprojection_errors_per_image(
            objpoints, imgpoints, K, D, rvecs, tvecs
        )

        if reject_outliers and round_num < max_outlier_rounds - 1:
            bad = per_image_errors > outlier_threshold_px
            n_bad = int(np.sum(bad))
            if n_bad == 0:
                break
            if n_used - n_bad < 6:
                print(f"  [CALIBRATE] Would remove {n_bad} outliers but need ≥6 images; keeping all.")
                break
            # Remove worst images
            keep = ~bad
            objpoints = [objpoints[i] for i in range(n_used) if keep[i]]
            imgpoints = [imgpoints[i] for i in range(n_used) if keep[i]]
            valid_paths = [valid_paths[i] for i in range(n_used) if keep[i]]
            removed_names = [Path(valid_paths[i]).name for i in range(n_used) if bad[i]]
            print(f"  Removed {n_bad} outlier(s): {removed_names}")
        else:
            break

    # Report per-image errors for the final solution
    print(f"\n  Per-image RMS (px): min={per_image_errors.min():.3f}, max={per_image_errors.max():.3f}, mean={per_image_errors.mean():.3f}")
    print("\n  Filename                          RMS (px)")
    print("  " + "-" * 42)
    for path, rms in zip(valid_paths, per_image_errors):
        print(f"  {Path(path).name:32s}  {rms:.4f}")
    worst_idx = int(np.argmax(per_image_errors))
    if per_image_errors[worst_idx] > 1.0:
        print(f"\n  Worst image: {Path(valid_paths[worst_idx]).name} ({per_image_errors[worst_idx]:.3f} px)")

    print(f"\n{'='*50}")
    print(f"  RMS Reprojection Error: {rms:.4f} px")
    print(f"  (< 0.5 = excellent, < 1.0 = good, > 1.0 = add varied poses or check SQUARE_SIZE_M)")
    print(f"\n  Camera Matrix K:\n{K}")
    print(f"\n  Distortion Coefficients D:\n{D.ravel()}")
    print(f"{'='*50}\n")

    intrinsics = CameraIntrinsics(
        K=K.tolist(),
        D=D.tolist(),
        img_size=list(img_size),
        rms=float(rms),
        camera_height_m=camera_height_m,
    )
    intrinsics.save(save_path)
    return intrinsics


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: UNDISTORT IMAGES
# ─────────────────────────────────────────────────────────────────────────────

def undistort_image(image_path: str, intrinsics: CameraIntrinsics,
                    output_path: str = None) -> np.ndarray:
    """
    STEP 4: Remove lens distortion from an image using calibrated parameters.

    INSTRUCTIONS:
      - Always undistort before any geometric measurements or georeferencing.
      - The output image will have black borders where pixels were remapped;
        you can crop these using the optimal new camera matrix (alpha=0 below).
    """
    img = cv2.imread(image_path)
    K   = intrinsics.K_np()
    D   = intrinsics.D_np()
    w, h = intrinsics.img_size

    # Get optimal new camera matrix (alpha=0 crops black borders, alpha=1 keeps all pixels)
    K_new, roi = cv2.getOptimalNewCameraMatrix(K, D, (w, h), alpha=0)

    undistorted = cv2.undistort(img, K, D, None, K_new)

    # Crop to valid region
    x, y, cw, ch = roi
    undistorted = undistorted[y:y+ch, x:x+cw]

    if output_path:
        cv2.imwrite(output_path, undistorted)
        print(f"Undistorted image saved to {output_path}")

    return undistorted


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: READ GPS METADATA FROM IMAGE EXIF
# ─────────────────────────────────────────────────────────────────────────────

def read_exif_gps(image_path: str) -> dict:
    """
    STEP 5: Extract GPS metadata from image EXIF data.

    Returns dict with:
        lat        : decimal degrees (positive = N)
        lon        : decimal degrees (positive = E)
        altitude   : meters above sea level (if available)
        heading    : compass bearing in degrees (if available)
        pitch      : camera tilt in degrees (if available)
        roll       : camera roll in degrees (if available)
    """
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS

    img  = Image.open(image_path)
    exif = img._getexif()

    if not exif:
        raise ValueError(f"No EXIF data found in {image_path}")

    # Decode tag names
    decoded = {TAGS.get(k, k): v for k, v in exif.items()}
    gps_raw = decoded.get("GPSInfo", {})
    gps     = {GPSTAGS.get(k, k): v for k, v in gps_raw.items()}

    def dms_to_decimal(dms, ref):
        """Convert degrees/minutes/seconds tuple to decimal degrees."""
        d, m, s = [float(x) for x in dms]
        decimal  = d + m / 60 + s / 3600
        if ref in ('S', 'W'):
            decimal = -decimal
        return decimal

    result = {}

    if "GPSLatitude" in gps:
        result["lat"] = dms_to_decimal(gps["GPSLatitude"], gps.get("GPSLatitudeRef", "N"))
    if "GPSLongitude" in gps:
        result["lon"] = dms_to_decimal(gps["GPSLongitude"], gps.get("GPSLongitudeRef", "E"))
    if "GPSAltitude" in gps:
        result["altitude"] = float(gps["GPSAltitude"])
    if "GPSImgDirection" in gps:
        result["heading"] = float(gps["GPSImgDirection"])
    if "GPSDestBearing" in gps:
        result["pitch"] = float(gps["GPSDestBearing"])

    print(f"[EXIF GPS] {result}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: BUILD EXTRINSIC MATRIX FROM GPS + ORIENTATION
# ─────────────────────────────────────────────────────────────────────────────

def build_extrinsic_matrix(lat: float, lon: float, altitude: float,
                            heading_deg: float, pitch_deg: float,
                            roll_deg: float = 0.0) -> tuple:
    """
    STEP 6: Build the camera extrinsic matrix [R | t] from GPS + orientation.

    The extrinsic matrix transforms points from WORLD coordinates (ENU local
    frame centered on the camera) to CAMERA coordinates.

    COORDINATE SYSTEM:
        World (ENU): X=East, Y=North, Z=Up
        Camera:      X=right, Y=down, Z=forward (into scene)

    PARAMETERS:
        heading_deg : compass bearing (0=North, 90=East, clockwise)
        pitch_deg   : tilt from horizontal (0=level, -90=straight down)
        roll_deg    : roll (0=level)

    RETURNS:
        R (3x3): rotation matrix (world -> camera)
        t (3x1): translation (camera origin in world ENU coords)
        origin  : (lat, lon, alt) of camera
    """
    # Use shared geometry so conventions match georeference_tool
    R = build_rotation_matrix(heading_deg, pitch_deg, roll_deg)

    # Translation: for local ENU we treat camera as origin
    t = np.zeros((3, 1))

    return R, t, (lat, lon, altitude)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8: PROJECT IMAGE PIXEL TO GROUND COORDINATES
# ─────────────────────────────────────────────────────────────────────────────

def pixel_to_ground_coords(pixel_xy: tuple, intrinsics: CameraIntrinsics,
                            R: np.ndarray, t: np.ndarray,
                            origin_lat: float, origin_lon: float,
                            camera_height_m: float) -> tuple:
    """
    STEP 7: Project a 2D image pixel to a geographic coordinate on the ground.

    This uses the ground plane intersection method:
      1. Convert pixel to a normalized ray in camera space using K^-1
      2. Rotate the ray into the world (ENU) frame using R^T
      3. Find where the ray intersects Z=0 (ground plane)
      4. Convert ENU offset (meters) to lat/lon using pyproj

    PARAMETERS:
        pixel_xy         : (u, v) pixel coordinates in the undistorted image
        intrinsics       : calibrated camera intrinsics
        R, t             : extrinsic rotation and translation
        origin_lat/lon   : GPS position of the camera
        camera_height_m  : height of camera above ground in meters

    RETURNS:
        (latitude, longitude) of the ground point below that pixel

    NOTE: This assumes flat ground. For sloped terrain, you'd need a DEM.
    """
    K = intrinsics.K_np()
    result = pixel_to_world_flat(
        pixel_xy[0], pixel_xy[1], K, R,
        origin_lat, origin_lon, camera_height_m,
    )
    if result is None:
        raise ValueError("Ray is parallel to the ground plane or points upward — no intersection.")
    lat_out, lon_out = result
    return lat_out, lon_out


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9: GEOREFERENCE ENTIRE IMAGE (produce coordinate grid)
# ─────────────────────────────────────────────────────────────────────────────

def georeference_image(image_path: str, intrinsics: CameraIntrinsics,
                        heading_deg: float, pitch_deg: float, roll_deg: float,
                        camera_height_m: float,
                        sample_step: int = 50) -> list:
    """
    STEP 8: Georeference an image by projecting a grid of pixels to ground coords.

    INSTRUCTIONS:
        - heading_deg: camera compass bearing from GPS/IMU
        - pitch_deg: camera tilt — for a downward-looking flood camera,
                     this will be negative (e.g., -75° = mostly downward)
        - roll_deg: camera roll from IMU
        - camera_height_m: mounting height above ground
        - sample_step: pixels between sampled grid points (lower = denser but slower)

    RETURNS:
        List of dicts: {pixel_x, pixel_y, lat, lon}

    This output can be used as Ground Control Points (GCPs) for GIS software
    (e.g., QGIS, GDAL) to produce a georeferenced GeoTIFF.
    """
    gps = read_exif_gps(image_path)
    lat, lon, alt = gps["lat"], gps["lon"], gps.get("altitude", camera_height_m)

    # Override orientation from EXIF if available, else use provided values
    heading = gps.get("heading", heading_deg)
    pitch   = gps.get("pitch",   pitch_deg)
    roll    = gps.get("roll",    roll_deg)

    R, t, origin = build_extrinsic_matrix(lat, lon, alt, heading, pitch, roll)

    img = cv2.imread(image_path)
    h, w = img.shape[:2]

    gcps = []
    print(f"[GEOREFERENCE] Projecting pixel grid (step={sample_step}px)...")

    for v in range(0, h, sample_step):
        for u in range(0, w, sample_step):
            try:
                lat_g, lon_g = pixel_to_ground_coords(
                    (u, v), intrinsics, R, t, lat, lon, camera_height_m
                )
                gcps.append({
                    "pixel_x": u, "pixel_y": v,
                    "lat": lat_g, "lon": lon_g
                })
            except ValueError:
                pass  # skip pixels with no ground intersection

    print(f"[GEOREFERENCE] {len(gcps)} ground control points computed.")
    return gcps


def save_gcps_to_csv(gcps: list, output_path: str):
    """Save GCPs to CSV for use in QGIS or GDAL."""
    import csv
    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["pixel_x", "pixel_y", "lat", "lon"])
        writer.writeheader()
        writer.writerows(gcps)
    print(f"GCPs saved to {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10: APPLY GCPS IN GDAL (produce GeoTIFF)
# ─────────────────────────────────────────────────────────────────────────────

def create_geotiff(image_path: str, gcps: list, output_path: str):
    """
    STEP 9: Write a georeferenced GeoTIFF using GDAL and the computed GCPs.

    INSTRUCTIONS:
        - Requires: pip install gdal  (or install via conda: conda install gdal)
        - Output is a GeoTIFF readable by QGIS, ArcGIS, or any GIS tool.
        - The GCP-based warping gives a proper georeferenced raster output.
    """
    try:
        from osgeo import gdal, osr
    except ImportError:
        print("GDAL not installed. Save GCPs to CSV and use QGIS to georeference manually.")
        return

    # Open source image
    src_ds = gdal.Open(image_path)
    driver = gdal.GetDriverByName("GTiff")
    dst_ds = driver.CreateCopy(output_path, src_ds, 0)

    # Define spatial reference (WGS84)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)  # WGS84 geographic

    # Build GDAL GCP objects
    gdal_gcps = [
        gdal.GCP(g["lon"], g["lat"], 0, g["pixel_x"], g["pixel_y"])
        for g in gcps
    ]

    dst_ds.SetGCPs(gdal_gcps, srs.ExportToWkt())

    # Warp to remove distortion and apply geotransform
    gdal.Warp(output_path.replace(".tif", "_warped.tif"),
              dst_ds,
              format="GTiff",
              tps=True,           # thin-plate spline warping
              dstSRS="EPSG:4326")

    dst_ds = None
    print(f"GeoTIFF saved to {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 11: PROMPT FOR USER-PROVIDED VALUES
# ─────────────────────────────────────────────────────────────────────────────

def prompt_calibration_inputs() -> tuple:
    """
    Ask the user for values that are not obtained from the calibration images.
    Checkerboard dimensions (BOARD_W, BOARD_H, SQUARE_SIZE_M) remain constants
    at the top of this file.

    Returns:
        (image_dir, save_path, camera_height_m)
        camera_height_m is None if the user leaves it blank.
    """
    print("\n[CALIBRATION INPUTS] Enter values (press Enter for default where shown).\n")

    image_dir = input("Folder containing checkerboard images [./calib_images]: ").strip()
    if not image_dir:
        image_dir = "./calib_images"

    save_path = input("Path to save calibration JSON [./calibration.json]: ").strip()
    if not save_path:
        save_path = "./calibration.json"

    height_str = input(
        "Camera height above ground in meters (for georeferencing; optional): "
    ).strip()
    camera_height_m = None
    if height_str:
        try:
            camera_height_m = float(height_str)
            if camera_height_m <= 0:
                print("  (ignored: height must be positive)")
                camera_height_m = None
        except ValueError:
            print("  (ignored: not a number)")

    return image_dir, save_path, camera_height_m


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 12: MAIN PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── A. CALIBRATE (run once per camera) ───────────────────────────────────

    # Optional: capture calibration images live
    # capture_calibration_images("./calib_images", n_images=20)

    image_dir, save_path, camera_height_m = prompt_calibration_inputs()

    intrinsics = calibrate_camera(
        image_dir=image_dir,
        save_path=save_path,
        show_corners=True,
        camera_height_m=camera_height_m,
    )

    # ── B. LOAD CALIBRATION (subsequent runs) ────────────────────────────────

    # intrinsics = CameraIntrinsics.load("./calibration.json")

    # ── C. UNDISTORT A FIELD IMAGE ────────────────────────────────────────────

    # undistort_image("./field_image.jpg", intrinsics, "./field_image_undistorted.jpg")

    # ── D. GEOREFERENCE A FIELD IMAGE ────────────────────────────────────────

    # For a flood camera mounted ~10m high, looking mostly downward:
    gcps = georeference_image(
        image_path="./field_image.jpg",
        intrinsics=intrinsics,
        heading_deg=90.0,      # camera faces NE
        pitch_deg=0.0,       # camera angled steeply downward
        roll_deg=0.0,
        camera_height_m=1.0,
        sample_step=100
    )

    save_gcps_to_csv(gcps, "./gcps.csv")

    # ── E. PRODUCE GEOTIFF (requires GDAL) ───────────────────────────────────

    # create_geotiff("./field_image_undistorted.jpg", gcps, "./output.tif")
