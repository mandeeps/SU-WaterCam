"""
Shared georeferencing core: pixel → ray → flat ground intersection → lat/lon.

Used by georeference_tool, camera_calibration, georeference3d, and tests
so that a single implementation guarantees consistent accuracy.

Conventions: ENU world (X=East, Y=North, Z=Up); camera (X=right, Y=down, Z=forward).
Camera is at (0, 0, camera_height_m) in ENU; ground plane Z = 0.
"""

from __future__ import annotations

from ufonet_geo.geometry import (  # noqa: F401  (re-exported; the implementations live in the package)
    MAX_GROUND_RANGE_M,
    MIN_GRAZING_ANGLE_DEG,
    intersect_flat,
    pixel_ray,
    pixel_to_world_flat,
)
from ufonet_geo.terrain import camera_elev_from_dem  # noqa: F401
