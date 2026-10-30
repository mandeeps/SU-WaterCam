"""
Terrain: DEM sampling, camera elevation and ray / terrain intersection.

numpy only, except sample_dem_grid(), which reads a GeoTIFF with rasterio
and reprojects with pyproj (the `dem` extra).

Camera and terrain must share one vertical datum. The robust way is
camera_elev_from_dem(): ground elevation under the camera plus the mount
height, which avoids mixing EXIF's ellipsoidal altitude with an
orthometric DEM.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

import numpy as np

from .geometry import enu_to_latlon


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


def ray_intersect_terrain(
    ray_origin_enu: np.ndarray,
    ray_dir_enu: np.ndarray,
    get_elevation: Callable[[float, float], Optional[float]],
    origin_lon: float,
    origin_lat: float,
    camera_elev_m: float,
    step_m: float = 0.5,
    max_range_m: float = 5000.0,
    tol_m: float = 0.2,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Intersect the camera ray with the terrain surface.

    Ray origin is at the camera in local ENU (origin at camera). At distance t
    along a *unit* direction: point = origin + t * ray_dir. For each sample we
    evaluate g(t) = z_ray(t) - terrain_z_enu(lat(t), lon(t)), where
    terrain_z_enu = elev - camera_elev_m. A hit is where g(t) = 0 (ray meets
    the surface).

    The old check (z_ray <= terrain_z + tol on the first coarse step) was wrong:
    near the camera, terrain_z is almost the same for every ray, so the first
    step often satisfied the inequality for every downward ray with the same t
    (= step_m), giving a constant bogus slant range.

    We bracket a sign change of g(t) with a coarse march, then bisect for an
    accurate distance. tol_m is used as vertical tolerance for bisection.
    """
    ray_dir = np.asarray(ray_dir_enu, dtype=np.float64)
    nrm = np.linalg.norm(ray_dir)
    if nrm < 1e-12:
        return None
    ray_dir = ray_dir / nrm
    origin = np.asarray(ray_origin_enu, dtype=np.float64)

    def eval_at(t: float):
        point = origin + float(t) * ray_dir
        east, north = float(point[0]), float(point[1])
        z_ray = float(point[2])
        lat, lon = enu_to_latlon(east, north, origin_lat, origin_lon)
        elev = get_elevation(lon, lat)
        if elev is None:
            return None
        terrain_z_enu = elev - camera_elev_m
        g = z_ray - terrain_z_enu
        return g, lat, lon, elev, point

    def bisect_bracket(t_lo: float, t_hi: float) -> Optional[Tuple[float, float, float, float]]:
        """g(t_lo) > 0 and g(t_hi) <= 0 (ray crossed surface); return hit tuple."""
        r_lo = eval_at(t_lo)
        r_hi = eval_at(t_hi)
        if r_lo is None or r_hi is None:
            return None
        g_lo = r_lo[0]
        g_hi = r_hi[0]
        # Tiny epsilon: float noise at the crossing
        eps = 1e-5
        if not (g_lo > 0 and g_hi <= eps):
            return None
        t_a, t_b = t_lo, t_hi
        ga, gb = g_lo, g_hi
        for _ in range(50):
            if t_b - t_a < 1e-4:
                break
            tm = 0.5 * (t_a + t_b)
            rm = eval_at(tm)
            if rm is None:
                # Rare nodata in bracket; step slightly toward the hi side
                tm = t_a + 0.75 * (t_b - t_a)
                rm = eval_at(tm)
                if rm is None:
                    break
            g_m, lat_m, lon_m, elev_m, pt_m = rm
            if abs(g_m) < tol_m:
                slant = float(np.linalg.norm(pt_m - origin))
                return (lat_m, lon_m, elev_m, slant)
            if g_m > 0:
                t_a, ga = tm, g_m
            else:
                t_b, gb = tm, g_m
        t_hit = 0.5 * (t_a + t_b)
        r_hit = eval_at(t_hit)
        if r_hit is None:
            r_hit = eval_at(t_b)
        if r_hit is None:
            return None
        _, lat, lon, elev, point = r_hit
        slant_range_m = float(np.linalg.norm(point - origin))
        return (lat, lon, elev, slant_range_m)

    def hit_from_t(t_hit: float) -> Optional[Tuple[float, float, float, float]]:
        r = eval_at(t_hit)
        if r is None:
            return None
        _, lat, lon, elev, point = r
        slant_range_m = float(np.linalg.norm(point - origin))
        return (lat, lon, elev, slant_range_m)

    def refine_crossing(t_prev: float, g_prev: float, t_curr: float, g_curr: float):
        """After bisect_bracket fails, linearly interpolate g across [t_prev, t_curr]."""
        denom = g_curr - g_prev
        if abs(denom) < 1e-12:
            return None
        t_hit = t_prev - g_prev * (t_curr - t_prev) / denom
        t_hit = max(t_prev, min(t_curr, t_hit))
        return hit_from_t(t_hit)

    r0 = eval_at(0.0)
    if r0 is None:
        # DEM nodata at camera is common — start along the ray instead of failing.
        t = step_m
        r_first = None
        while t <= max_range_m:
            r_first = eval_at(t)
            if r_first is not None:
                break
            t += step_m
        if r_first is None:
            return None
        g_last, t_last = r_first[0], t
        if g_last > 0:
            t = t_last + step_m
        else:
            # Below local surface at first valid sample — march until ray is above terrain.
            found_above = False
            t = t_last + step_m
            while t <= max_range_m:
                r = eval_at(t)
                if r is None:
                    t += step_m
                    continue
                if r[0] > 0:
                    g_last, t_last = r[0], t
                    found_above = True
                    break
                g_last, t_last = r[0], t
                t += step_m
            if not found_above:
                return None
            t = t_last + step_m
    else:
        g_last, t_last = r0[0], 0.0

        # If the ray starts at/under the local surface, march until we are above it.
        if g_last <= 0:
            t = step_m
            found_above = False
            while t <= max_range_m:
                r = eval_at(t)
                if r is None:
                    t += step_m
                    continue
                if r[0] > 0:
                    g_last, t_last = r[0], t
                    found_above = True
                    break
                g_last, t_last = r[0], t
                t += step_m
            if not found_above:
                return None
            t = t_last + step_m
        else:
            t = step_m

    while t <= max_range_m:
        r = eval_at(t)
        if r is None:
            t += step_m
            continue
        g, lat, lon, elev, point = r
        # Crossing: was above surface, now at or below (g = height above terrain; float-safe).
        if g_last > 0 and g <= 1e-5:
            refined = bisect_bracket(t_last, t)
            if refined is not None:
                return refined
            refined = refine_crossing(t_last, g_last, t, g)
            if refined is not None:
                return refined
            return hit_from_t(t)
        g_last, t_last = g, t
        t += step_m
    return None



def pixel_to_gps_terrain(
    ray_cam: np.ndarray,
    R: np.ndarray,
    camera_lat: float,
    camera_lon: float,
    camera_elev_m: float,
    get_elevation: Callable[[float, float], Optional[float]],
    step_m: float = 0.5,
    max_range_m: float = 5000.0,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Intersect a unit camera-frame ray (from geometry.pixel_ray) with the
    terrain → (lat, lon, elevation, slant_range_m), or None.

    R: rotation matrix world ENU -> camera (geometry.build_rotation_matrix).
    camera_elev_m: camera elevation in the terrain's vertical datum.
    """
    ray_world = R.T @ np.asarray(ray_cam, dtype=np.float64)
    return ray_intersect_terrain(
        np.zeros(3), ray_world, get_elevation, camera_lon, camera_lat,
        camera_elev_m, step_m=step_m, max_range_m=max_range_m,
    )


def sample_dem_grid(dem_path: str, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """
    Nearest-cell DEM elevations at arrays of WGS84 lat/lon (any matching
    shape); NaN outside the DEM or on nodata. Reads only the window that
    covers the points, so a large DEM is fine.
    """
    import rasterio
    from pyproj import Transformer
    from rasterio.windows import Window

    lat = np.asarray(lat, dtype=np.float64)
    lon = np.asarray(lon, dtype=np.float64)
    out = np.full(lat.shape, np.nan)
    with rasterio.open(dem_path) as src:
        if src.crs is not None and src.crs.to_epsg() != 4326:
            tf = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            x, y = tf.transform(lon.ravel(), lat.ravel())
        else:
            x, y = lon.ravel(), lat.ravel()
        inv = ~src.transform
        cols = inv.a * np.asarray(x) + inv.b * np.asarray(y) + inv.c
        rows = inv.d * np.asarray(x) + inv.e * np.asarray(y) + inv.f
        rows = np.floor(rows).astype(np.int64)
        cols = np.floor(cols).astype(np.int64)
        inside = (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
        if not inside.any():
            return out
        r0, r1 = rows[inside].min(), rows[inside].max()
        c0, c1 = cols[inside].min(), cols[inside].max()
        data = src.read(1, window=Window(c0, r0, c1 - c0 + 1, r1 - r0 + 1),
                        masked=True).astype(np.float64).filled(np.nan)
    flat = out.ravel()
    flat[inside] = data[rows[inside] - r0, cols[inside] - c0]
    return flat.reshape(lat.shape)
