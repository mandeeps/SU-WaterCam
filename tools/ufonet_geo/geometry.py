"""
Camera geometry: rotation, lens model, rays, flat ground, ENU <-> lat/lon.

Conventions (shared by every module in this repo):
  World frame (ENU):  X = East, Y = North, Z = Up
  Camera frame:       X = right, Y = down, Z = forward (into the scene)
  heading_deg : compass bearing of the boresight (0 = North, 90 = East)
  pitch_deg   : tilt from horizontal (0 = level, -90 = straight down)
  roll_deg    : rotation about the boresight (+ve = right side tilts down)

The lens model is OpenCV's 5-coefficient Brown-Conrady (k1, k2, p1, p2, k3),
the layout calibration.json and camera_calibration.json use.

Everything here needs only numpy, so the API server can use it as is.

ENU offsets convert to lat/lon with a local tangent plane using WGS84's
meridional (M) and prime-vertical (N) radii at the origin latitude. Against
pyproj's geodesic it is off by about 1 mm at 100 m and 10-15 cm at 1 km
(growing with the square of the range), well below the pipeline's accuracy.
Being linear, it also makes a north-up ENU grid an exact lat/lon rectangle.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Sequence, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Guards for ground intersection. A ray this close to the horizon turns
# centimetres of pitch error into hundreds of metres on the ground, so such
# pixels are rejected rather than returned as if they were reliable.
MIN_GRAZING_ANGLE_DEG = 2.0    # min angle between ray and ground plane
MAX_GROUND_RANGE_M = 2000.0    # max horizontal distance from camera

_WGS84_A = 6378137.0                 # equatorial radius, m
_WGS84_E2 = 6.69437999014e-3         # first eccentricity squared


# ─────────────────────────────────────────────────────────────────────────────
# Orientation
# ─────────────────────────────────────────────────────────────────────────────

def build_rotation_matrix(heading_deg: float,
                          pitch_deg: float,
                          roll_deg: float) -> np.ndarray:
    """
    Build rotation matrix R (world ENU → camera frame) from orientation.

    APPROACH — construct camera axes directly in ENU space:
        1. Compute camera boresight (Z axis) in ENU from heading + pitch
        2. Compute camera right (X axis) in ENU from heading
        3. Apply roll about boresight
        4. Compute camera down (Y axis) = Z × X
        5. Stack as columns → R_cam_to_world (camera→world ENU)
        6. Transpose → R (world→camera)
    """
    H = np.radians(heading_deg)
    P = np.radians(pitch_deg)
    r = np.radians(roll_deg)

    # Step 1: boresight (forward, Z) in ENU
    forward = np.array([
        np.sin(H) * np.cos(P),  # East component
        np.cos(H) * np.cos(P),  # North component
        np.sin(P),              # Up (negative when tilting down)
    ])

    # Step 2: camera right (X) in ENU — perpendicular to heading, in horizontal plane
    right = np.array([
        np.cos(H),  # East
        -np.sin(H), # North
        0.0,        # Up
    ])

    # Step 3: roll about boresight
    if abs(roll_deg) > 1e-6:
        cos_r = np.cos(r)
        sin_r = np.sin(r)
        right = cos_r * right + sin_r * np.cross(forward, right)

    # Step 4: camera down (Y) = Z × X
    down = np.cross(forward, right)

    # Normalize axes
    right = right / np.linalg.norm(right)
    down = down / np.linalg.norm(down)
    forward = forward / np.linalg.norm(forward)

    # Step 5–6: camera→world then transpose
    R_cam_to_world = np.column_stack([right, down, forward])
    return R_cam_to_world.T


# ─────────────────────────────────────────────────────────────────────────────
# Lens model
# ─────────────────────────────────────────────────────────────────────────────

def _dist_coeffs(D) -> Tuple[float, float, float, float, float]:
    """(k1, k2, p1, p2, k3) from a flat or nested D, zero-padded."""
    coeffs = [float(c) for c in np.asarray(D if D is not None else [], dtype=np.float64).ravel()]
    k1, k2, p1, p2, k3 = (coeffs + [0.0] * 5)[:5]
    return k1, k2, p1, p2, k3


def scale_intrinsics(K: np.ndarray, calib_w: int, calib_h: int,
                     image_w: int, image_h: int) -> np.ndarray:
    """Scale K from the calibration's resolution to an image's, if they differ."""
    K = np.asarray(K, dtype=np.float64)
    if calib_w == image_w and calib_h == image_h:
        return K
    sx, sy = image_w / calib_w, image_h / calib_h
    K_scaled = K.copy()
    K_scaled[0, 0] *= sx  # fx
    K_scaled[0, 2] *= sx  # cx
    K_scaled[1, 1] *= sy  # fy
    K_scaled[1, 2] *= sy  # cy
    return K_scaled


def undistort_normalized(u, v, K: np.ndarray, D, iterations: int = 20):
    """
    Undistorted normalized camera coordinates (x, y) — the point on the z=1
    plane — for distorted pixel(s) (u, v). Scalars or arrays.

    Same fixed-point iteration as cv2.undistortPoints (which runs 5 by
    default); the extra iterations here converge further for a single click.
    """
    K = np.asarray(K, dtype=np.float64)
    k1, k2, p1, p2, k3 = _dist_coeffs(D)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    x0 = (np.asarray(u, dtype=np.float64) - cx) / fx
    y0 = (np.asarray(v, dtype=np.float64) - cy) / fy
    x, y = x0, y0
    for _ in range(iterations):
        r2 = x * x + y * y
        icdist = 1.0 / (1.0 + r2 * (k1 + r2 * (k2 + r2 * k3)))
        dx = 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
        dy = p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
        x = (x0 - dx) * icdist
        y = (y0 - dy) * icdist
    if np.ndim(x) == 0:
        return float(x), float(y)
    return x, y


def distort_normalized(x, y, D):
    """Apply the lens distortion to normalized coordinates (cv2.projectPoints' model)."""
    k1, k2, p1, p2, k3 = _dist_coeffs(D)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    r2 = x * x + y * y
    radial = 1.0 + r2 * (k1 + r2 * (k2 + r2 * k3))
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return xd, yd


def undistorted_camera_matrix(K: np.ndarray, D, image_w: int, image_h: int,
                              alpha: float = 1.0) -> np.ndarray:
    """
    The camera matrix of a frame undistorted with
    cv2.undistort(img, K, D, None, cv2.getOptimalNewCameraMatrix(K, D, (w, h), alpha)[0]).

    A numpy port of getOptimalNewCameraMatrix (without the ROI output): it
    undistorts a 9x9 grid of points spanning the image with cv2's 5
    iterations and fits the inscribed (alpha=0) or circumscribed (alpha=1)
    rectangle to the frame.

    SU-WaterCam undistorts the frame its bands are registered to with
    alpha=1 before segmentation (tools/coreg_multiple.py), so a node's flood
    mask is described by this matrix and no distortion, not by K and D.
    """
    n = 9
    gx, gy = np.meshgrid(np.arange(n) * (image_w - 1) / (n - 1),
                         np.arange(n) * (image_h - 1) / (n - 1))
    x, y = undistort_normalized(gx, gy, K, D, iterations=5)
    outer_x0, outer_x1 = x.min(), x.max()
    outer_y0, outer_y1 = y.min(), y.max()
    inner_x0, inner_x1 = x[:, 0].max(), x[:, -1].min()
    inner_y0, inner_y1 = y[0, :].max(), y[-1, :].min()

    fx0 = (image_w - 1) / (inner_x1 - inner_x0)
    fy0 = (image_h - 1) / (inner_y1 - inner_y0)
    cx0, cy0 = -fx0 * inner_x0, -fy0 * inner_y0
    fx1 = (image_w - 1) / (outer_x1 - outer_x0)
    fy1 = (image_h - 1) / (outer_y1 - outer_y0)
    cx1, cy1 = -fx1 * outer_x0, -fy1 * outer_y0

    return np.array([
        [fx0 * (1 - alpha) + fx1 * alpha, 0.0, cx0 * (1 - alpha) + cx1 * alpha],
        [0.0, fy0 * (1 - alpha) + fy1 * alpha, cy0 * (1 - alpha) + cy1 * alpha],
        [0.0, 0.0, 1.0],
    ])


def pixel_ray(K: np.ndarray, u: float, v: float, D=None) -> np.ndarray:
    """
    Unproject pixel (u, v) to a unit ray in camera frame. With D, the pixel
    is undistorted first; without, K alone is used (an undistorted frame).
    """
    if D is None:
        ray = np.linalg.inv(np.asarray(K, dtype=np.float64)) @ np.array([u, v, 1.0])
    else:
        x, y = undistort_normalized(u, v, K, D)
        ray = np.array([x, y, 1.0])
    return ray / np.linalg.norm(ray)


def project_to_pixels(points_cam: np.ndarray, K: np.ndarray, D=None):
    """
    Project camera-frame points (..., 3) to pixels (u, v), with distortion
    when D is given. Points at or behind the camera give NaN.
    """
    K = np.asarray(K, dtype=np.float64)
    z = points_cam[..., 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.where(z > 1e-9, points_cam[..., 0] / z, np.nan)
        y = np.where(z > 1e-9, points_cam[..., 1] / z, np.nan)
    if D is not None:
        x, y = distort_normalized(x, y, D)
    return K[0, 0] * x + K[0, 2], K[1, 1] * y + K[1, 2]


# ─────────────────────────────────────────────────────────────────────────────
# Ground intersection and ENU <-> lat/lon
# ─────────────────────────────────────────────────────────────────────────────

def depression_angle_deg(ray_world: np.ndarray) -> float:
    """Angle of a ray below the horizon (deg); negative above it."""
    norm = float(np.linalg.norm(ray_world))
    return float(np.degrees(np.arcsin(max(-1.0, min(1.0, -ray_world[2] / norm)))))


def intersect_flat(
    ray_world: np.ndarray,
    camera_height_m: float,
    min_grazing_deg: float = MIN_GRAZING_ANGLE_DEG,
    max_range_m: float = MAX_GROUND_RANGE_M,
) -> Optional[Tuple[float, float]]:
    """
    Intersect a ray (in ENU) from camera at (0, 0, camera_height_m)
    with the flat ground plane Z = 0.

    Returns (east_m, north_m) in meters relative to camera, or None if
    the ray does not hit the ground (horizontal or pointing up), is within
    min_grazing_deg of horizontal, or lands farther than max_range_m
    from the camera (the last two are logged).
    """
    if abs(ray_world[2]) < 1e-9:
        return None
    lam = -camera_height_m / ray_world[2]
    if lam < 0:
        return None
    depression_deg = depression_angle_deg(ray_world)
    if depression_deg < min_grazing_deg:
        log.info("Ray rejected: %.2f deg below horizon (< %.1f deg grazing limit)",
                 depression_deg, min_grazing_deg)
        return None
    ground_enu = np.array([0.0, 0.0, camera_height_m], dtype=np.float64) + lam * ray_world
    rng = float(np.hypot(ground_enu[0], ground_enu[1]))
    if rng > max_range_m:
        log.info("Ray rejected: ground range %.0f m exceeds %.0f m limit",
                 rng, max_range_m)
        return None
    return float(ground_enu[0]), float(ground_enu[1])


def local_radii(lat_deg: float) -> Tuple[float, float]:
    """WGS84 (M, N) at a latitude: meridional and prime-vertical radii of curvature, m.

    One degree of latitude is M*pi/180 m; one degree of longitude is N*cos(lat)*pi/180 m.
    """
    s2 = math.sin(math.radians(lat_deg)) ** 2
    w = math.sqrt(1.0 - _WGS84_E2 * s2)
    return _WGS84_A * (1.0 - _WGS84_E2) / w ** 3, _WGS84_A / w


def enu_to_latlon(east_m, north_m, origin_lat_deg: float, origin_lon_deg: float):
    """ENU offset(s) from an origin → (lat, lon), local tangent plane. Scalars or arrays."""
    m_radius, n_radius = local_radii(origin_lat_deg)
    lat = origin_lat_deg + np.degrees(np.asarray(north_m, dtype=np.float64) / m_radius)
    lon = origin_lon_deg + np.degrees(
        np.asarray(east_m, dtype=np.float64) / (n_radius * math.cos(math.radians(origin_lat_deg))))
    if np.ndim(lat) == 0:
        return float(lat), float(lon)
    return lat, lon


def latlon_to_enu(lat_deg, lon_deg, origin_lat_deg: float, origin_lon_deg: float):
    """Inverse of enu_to_latlon: (lat, lon) → (east_m, north_m) from the origin."""
    m_radius, n_radius = local_radii(origin_lat_deg)
    north = np.radians(np.asarray(lat_deg, dtype=np.float64) - origin_lat_deg) * m_radius
    east = (np.radians(np.asarray(lon_deg, dtype=np.float64) - origin_lon_deg)
            * n_radius * math.cos(math.radians(origin_lat_deg)))
    if np.ndim(north) == 0:
        return float(east), float(north)
    return east, north


def pixel_to_world_flat(
    u: float,
    v: float,
    K: np.ndarray,
    R: np.ndarray,
    camera_lat: float,
    camera_lon: float,
    camera_height_m: float,
    D: Optional[Sequence[float]] = None,
) -> Optional[Tuple[float, float]]:
    """
    Convert image pixel (u, v) to (lat, lon) on the flat ground plane.

    R: world ENU → camera (3×3). Camera at ENU origin, height above ground
       = camera_height_m. D: distortion of a raw frame (None = undistorted).
    Returns (lat, lon) or None if no intersection.
    """
    ray_world = R.T @ pixel_ray(K, u, v, D)
    enu = intersect_flat(ray_world, camera_height_m)
    if enu is None:
        return None
    return enu_to_latlon(enu[0], enu[1], camera_lat, camera_lon)
