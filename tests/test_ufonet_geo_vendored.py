"""The vendored ufonet_geo copy (tools/ufonet_geo/) is unedited, and the shims use it."""
import hashlib
import sys
from pathlib import Path

import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

VENDORED = TOOLS / "ufonet_geo"


def _recorded():
    lines = (VENDORED / "VENDORED_VERSION").read_text().split("\n")
    commit = lines[0].split()
    assert commit[0] == "commit" and len(commit[1]) == 40
    return {name: digest for digest, name in (line.split() for line in lines[1:] if line.strip())}


def test_every_vendored_file_matches_its_recorded_hash():
    recorded = _recorded()
    assert set(recorded) == {p.name for p in VENDORED.glob("*.py")}
    for name, digest in recorded.items():
        assert hashlib.sha256((VENDORED / name).read_bytes()).hexdigest() == digest, (
            f"tools/ufonet_geo/{name} differs from the Georeferencing commit in VENDORED_VERSION; "
            "change the package there and refresh the copy (tools/ufonet_geo/README.md)")


def test_shims_re_export_the_package():
    import camera_geometry
    import geo_core
    from ufonet_geo import geometry

    assert camera_geometry.build_rotation_matrix is geometry.build_rotation_matrix
    assert geo_core.pixel_to_world_flat is geometry.pixel_to_world_flat


def test_flat_ground_point_straight_ahead():
    from geo_core import pixel_to_world_flat
    from camera_geometry import build_rotation_matrix
    from ufonet_geo.geometry import latlon_to_enu

    lat, lon = 40.7128, -74.0060          # placeholder
    K = np.array([[1000.0, 0, 640.0], [0, 1000.0, 480.0], [0, 0, 1]])
    R = build_rotation_matrix(0.0, -45.0, 0.0)
    hit = pixel_to_world_flat(640.0, 480.0, K, R, lat, lon, 2.0)
    east, north = latlon_to_enu(*hit, lat, lon)
    assert east == pytest.approx(0.0, abs=1e-6)
    assert north == pytest.approx(2.0, abs=1e-6)     # 45 deg down from 2 m: 2 m ahead
