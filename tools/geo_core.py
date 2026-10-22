"""
Shared georeferencing core: pixel → ray → flat ground intersection → lat/lon.

Used by georeference_tool, camera_calibration, georeference3d, and tests
so that a single implementation guarantees consistent accuracy.

Conventions: ENU world (X=East, Y=North, Z=Up); camera (X=right, Y=down, Z=forward).
Camera is at (0, 0, camera_height_m) in ENU; ground plane Z = 0.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

# Guards for flat-ground intersection. A ray this close to the horizon turns
# centimetres of pitch error into hundreds of metres on the ground, so such
# pixels are rejected rather than returned as if they were reliable.
MIN_GRAZING_ANGLE_DEG = 2.0    # min angle between ray and ground plane
MAX_GROUND_RANGE_M = 2000.0    # max horizontal distance from camera


def pixel_ray(K: np.ndarray, u: float, v: float) -> np.ndarray:
    """
    Unproject pixel (u, v) to a unit ray in camera frame.
    K: 3×3 camera matrix. Returns 3-vector (unit length).
    """
    K_inv = np.linalg.inv(K)
    ray = K_inv @ np.array([u, v, 1.0], dtype=np.float64)
    return ray / np.linalg.norm(ray)


def intersect_flat(
    ray_world: np.ndarray,
    camera_height_m: float,
) -> Optional[Tuple[float, float]]:
    """
    Intersect a unit ray (in ENU) from camera at (0, 0, camera_height_m)
    with the flat ground plane Z = 0.

    Returns (east_m, north_m) in meters relative to camera, or None if
    the ray does not hit the ground (horizontal or pointing up), is within
    MIN_GRAZING_ANGLE_DEG of horizontal, or lands farther than
    MAX_GROUND_RANGE_M from the camera (the last two are logged).
    """
    if abs(ray_world[2]) < 1e-9:
        return None
    lam = -camera_height_m / ray_world[2]
    if lam < 0:
        return None
    norm = float(np.linalg.norm(ray_world))
    depression_deg = float(np.degrees(np.arcsin(min(1.0, abs(ray_world[2]) / norm))))   # unit or not
    if depression_deg < MIN_GRAZING_ANGLE_DEG:
        log.info("Ray rejected: %.2f deg below horizon (< %.1f deg grazing limit)",
                    depression_deg, MIN_GRAZING_ANGLE_DEG)
        return None
    ground_enu = np.array([0.0, 0.0, camera_height_m], dtype=np.float64) + lam * ray_world
    rng = float(np.hypot(ground_enu[0], ground_enu[1]))
    if rng > MAX_GROUND_RANGE_M:
        log.info("Ray rejected: ground range %.0f m exceeds %.0f m limit",
                    rng, MAX_GROUND_RANGE_M)
        return None
    return float(ground_enu[0]), float(ground_enu[1])


def pixel_to_world_flat(
    u: float,
    v: float,
    K: np.ndarray,
    R: np.ndarray,
    camera_lat: float,
    camera_lon: float,
    camera_height_m: float,
) -> Optional[Tuple[float, float]]:
    """
    Convert image pixel (u, v) to (lat, lon) on the flat ground plane.

    R: world ENU → camera (3×3). Camera at ENU origin, height above ground
       = camera_height_m. Returns (lat, lon) or None if no intersection.
    """
    ray_cam = pixel_ray(K, u, v)
    ray_world = R.T @ ray_cam
    enu = intersect_flat(ray_world, camera_height_m)
    if enu is None:
        return None
    east_m, north_m = enu
    try:
        from pyproj import Proj
    except ImportError:
        raise ImportError("pip install pyproj")
    proj = Proj(proj="aeqd", lat_0=camera_lat, lon_0=camera_lon, datum="WGS84")
    lon_g, lat_g = proj(east_m, north_m, inverse=True)
    return (float(lat_g), float(lon_g))


def camera_elev_from_dem(
    get_elevation: Callable[[float, float], Optional[float]],
    cam_lat: float,
    cam_lon: float,
    mount_height_m: float,
) -> float:
    """
    Compute camera elevation in the same vertical datum as the DEM/terrain.

    Use this when ray-casting against a DEM so camera and terrain share one
    vertical datum. EXIF altitude is often ellipsoidal/noisy; DEM is usually
    orthometric. Mixing them causes large horizontal errors.

    get_elevation(lon, lat) -> float | None  (e.g. from load_dem or LAS raster)
    mount_height_m: height of camera above ground (meters).

    Returns: ground_elev + mount_height_m. Raises if DEM has no data at camera.
    """
    ground_elev = get_elevation(cam_lon, cam_lat)
    if ground_elev is None:
        raise ValueError(
            "DEM/terrain has no elevation at camera position "
            f"({cam_lat:.6f}, {cam_lon:.6f}). Use a DEM that covers the site."
        )
    return float(ground_elev) + float(mount_height_m)
