"""
Orthorectify a fixed camera's flood mask onto a north-up ground grid.

The camera's pose doesn't change between captures, so the expensive part —
working out which image pixel each patch of ground appears at — is done
once, in build_ground_lookup(), and every later mask is drawn on the map
with an array lookup (GroundLookup.render()).

The lookup runs forward: each ground cell's centre is projected into the
camera (no ray marching), then kept only if it lands inside the frame,
is within max_range_m, is seen at least min_grazing_deg below the horizon,
and (with a DEM) isn't hidden behind higher terrain. Water areas of any
shape, holes included, come out right, and pixels that don't reach the
ground simply have no cell.

The grid is in metres east/north of the camera. geometry.enu_to_latlon is
linear, so the grid is an exact lat/lon rectangle (GroundLookup.bounds()),
which a web map can show as an image overlay.

Frames: a node's mask is computed on a frame undistorted with
geometry.undistorted_camera_matrix (alpha=1), so pass that matrix and
D=None. Pass the calibrated K and D for a raw photo.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .geometry import MIN_GRAZING_ANGLE_DEG, enu_to_latlon, project_to_pixels

#: GroundLookup.render() cell values.
UNSEEN, DRY, WATER = 0, 1, 2

#: Default largest distance from the camera drawn on the map, m. At a few
#: metres' mount height the grazing limit cuts in well before this.
DEFAULT_MAX_RANGE_M = 200.0


@dataclass
class GroundLookup:
    """Which frame pixel (u, v) each ground cell shows; NaN where none."""

    origin_lat: float          # camera position the grid is centred on
    origin_lon: float
    res_m: float               # cell size, m
    east0: float               # ENU of the centre of cell [0, 0] (row 0 is the north edge)
    north0: float
    u: np.ndarray              # (rows, cols) float32 frame pixel x; NaN = not seen
    v: np.ndarray              # (rows, cols) float32 frame pixel y
    frame_w: int               # size of the frame u, v refer to
    frame_h: int
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple:
        return self.u.shape

    def seen(self) -> np.ndarray:
        return np.isfinite(self.u)

    def bounds(self) -> tuple:
        """(south, west, north, east) of the grid's outer cell edges, degrees."""
        rows, cols = self.u.shape
        half = self.res_m / 2
        south, west = enu_to_latlon(self.east0 - half, self.north0 - (rows - 1) * self.res_m - half,
                                    self.origin_lat, self.origin_lon)
        north, east = enu_to_latlon(self.east0 + (cols - 1) * self.res_m + half, self.north0 + half,
                                    self.origin_lat, self.origin_lon)
        return south, west, north, east

    def render(self, mask: np.ndarray) -> np.ndarray:
        """
        Draw a mask onto the grid: uint8 (rows, cols) of UNSEEN, DRY, WATER.

        mask: 2-D array, non-zero = water, covering the whole frame at any
        resolution of the same aspect ratio (a LoRa bitmap or the full mask).
        """
        mask = np.asarray(mask)
        if mask.ndim != 2:
            raise ValueError(f"mask must be 2-D, got shape {mask.shape}")
        mh, mw = mask.shape
        if abs(mw / mh - self.frame_w / self.frame_h) > 0.02:
            raise ValueError(
                f"mask {mw}x{mh} doesn't have the frame's aspect ratio "
                f"({self.frame_w}x{self.frame_h})")
        out = np.zeros(self.u.shape, dtype=np.uint8)
        seen = self.seen()
        # Pixel i spans [i - 0.5, i + 0.5) in frame coordinates (OpenCV's convention).
        cols = np.clip(np.floor((self.u[seen] + 0.5) * mw / self.frame_w).astype(np.int64), 0, mw - 1)
        rows = np.clip(np.floor((self.v[seen] + 0.5) * mh / self.frame_h).astype(np.int64), 0, mh - 1)
        out[seen] = np.where(mask[rows, cols] != 0, WATER, DRY)
        return out

    def area_m2(self, rendered: np.ndarray, value: int = WATER) -> float:
        """Ground area of the cells of one value in a render() result, m²."""
        return float(np.count_nonzero(rendered == value)) * self.res_m ** 2

    # ── Persistence (the API caches one lookup per device and pose) ─────────

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        np.savez_compressed(
            buf, u=self.u, v=self.v,
            scalars=np.array([self.origin_lat, self.origin_lon, self.res_m,
                              self.east0, self.north0, self.frame_w, self.frame_h]),
            meta=np.frombuffer(json.dumps(self.meta).encode(), dtype=np.uint8))
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> "GroundLookup":
        with np.load(io.BytesIO(data)) as z:
            lat, lon, res, e0, n0, w, h = z["scalars"].tolist()
            return cls(lat, lon, res, e0, n0, z["u"], z["v"], int(w), int(h),
                       json.loads(z["meta"].tobytes().decode()))


def build_ground_lookup(
    K: np.ndarray,
    D,
    frame_w: int,
    frame_h: int,
    R: np.ndarray,
    camera_lat: float,
    camera_lon: float,
    mount_height_m: float,
    dem_sampler: Optional[Callable[[np.ndarray, np.ndarray], np.ndarray]] = None,
    res_m: float = 0.5,
    max_range_m: float = DEFAULT_MAX_RANGE_M,
    min_grazing_deg: float = MIN_GRAZING_ANGLE_DEG,
    occlusion_tol_m: float = 0.1,
) -> GroundLookup:
    """
    Work out which frame pixel each ground cell around the camera shows.

    K, D: intrinsics of the frame (frame_w x frame_h) the masks are in; D=None
        for an undistorted frame.
    R: world ENU → camera (geometry.build_rotation_matrix).
    mount_height_m: camera height above the ground under it.
    dem_sampler: optional (lat, lon) arrays → elevation arrays, NaN where
        unknown (e.g. functools.partial(terrain.sample_dem_grid, path)).
        Without it the ground is a flat plane mount_height_m below the camera.
        With it the camera is at DEM(camera) + mount_height_m, and cells
        hidden behind higher ground are left out.
    """
    n_half = int(np.ceil(max_range_m / res_m))
    offsets = np.arange(-n_half, n_half + 1) * res_m
    east, north = np.meshgrid(offsets, offsets[::-1])      # row 0 = north
    dist = np.hypot(east, north)

    if dem_sampler is not None:
        lat, lon = enu_to_latlon(east, north, camera_lat, camera_lon)
        ground = np.asarray(dem_sampler(lat, lon), dtype=np.float64)
        cam_ground = float(np.asarray(dem_sampler(np.array([camera_lat]), np.array([camera_lon])))[0])
        if not np.isfinite(cam_ground):
            raise ValueError("The DEM has no elevation at the camera position; "
                             "use a DEM that covers the site.")
        terrain = ground - (cam_ground + mount_height_m)   # height relative to the camera
    else:
        terrain = np.full(east.shape, -float(mount_height_m))

    with np.errstate(invalid="ignore"):
        depression = np.degrees(np.arctan2(-terrain, dist))
        cand = (dist > 0) & (dist <= max_range_m) & np.isfinite(terrain) & (depression >= min_grazing_deg)

    pts = np.stack([east[cand], north[cand], terrain[cand]], axis=-1) @ np.asarray(R, dtype=np.float64).T
    u, v = project_to_pixels(pts, K, D)
    with np.errstate(invalid="ignore"):
        in_frame = (u >= -0.5) & (u < frame_w - 0.5) & (v >= -0.5) & (v < frame_h - 0.5)

    idx = np.flatnonzero(cand)[in_frame]
    u, v = u[in_frame], v[in_frame]

    if dem_sampler is not None and idx.size:
        keep = ~_occluded(idx, east, north, terrain, dist, res_m, n_half, occlusion_tol_m)
        idx, u, v = idx[keep], u[keep], v[keep]

    u_grid = np.full(east.shape, np.nan, dtype=np.float32)
    v_grid = np.full(east.shape, np.nan, dtype=np.float32)
    u_grid.flat[idx] = u
    v_grid.flat[idx] = v

    # Crop to the seen cells plus one cell of margin.
    if idx.size:
        rows, cols = np.unravel_index(idx, east.shape)
        r0, r1 = max(rows.min() - 1, 0), min(rows.max() + 2, east.shape[0])
        c0, c1 = max(cols.min() - 1, 0), min(cols.max() + 2, east.shape[1])
    else:
        r0 = r1 = c0 = c1 = 0
    return GroundLookup(
        origin_lat=float(camera_lat), origin_lon=float(camera_lon), res_m=float(res_m),
        east0=float(offsets[c0]) if c1 > c0 else 0.0,
        north0=float(offsets[::-1][r0]) if r1 > r0 else 0.0,
        u=u_grid[r0:r1, c0:c1], v=v_grid[r0:r1, c0:c1],
        frame_w=int(frame_w), frame_h=int(frame_h),
        meta={"terrain": "dem" if dem_sampler is not None else "flat",
              "mount_height_m": float(mount_height_m),
              "max_range_m": float(max_range_m),
              "min_grazing_deg": float(min_grazing_deg)},
    )


def _occluded(idx, east, north, terrain, dist, res_m, n_half, tol_m) -> np.ndarray:
    """
    True for cells whose line of sight from the camera passes below the
    terrain. Samples the line every res_m of horizontal distance, stopping
    a cell short of the target so a slope doesn't hide itself.
    """
    e, n, z, d = east.flat[idx], north.flat[idx], terrain.flat[idx], dist.flat[idx]
    rows_total, cols_total = east.shape
    occluded = np.zeros(idx.size, dtype=bool)
    active = np.arange(idx.size)
    k = 1
    while active.size:
        s = k * res_m
        active = active[(d[active] - s > res_m) & ~occluded[active]]
        if not active.size:
            break
        f = s / d[active]
        col = np.rint(f * e[active] / res_m).astype(np.int64) + n_half
        row = n_half - np.rint(f * n[active] / res_m).astype(np.int64)
        ok = (row >= 0) & (row < rows_total) & (col >= 0) & (col < cols_total)
        ground = np.full(active.size, np.nan)
        ground[ok] = terrain[row[ok], col[ok]]
        with np.errstate(invalid="ignore"):
            occluded[active[ground > f * z[active] + tol_m]] = True
        k += 1
    return occluded
