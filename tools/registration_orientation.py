#!/usr/bin/env python
"""Flag captures taken after the node has turned since its registration was cached.

coreg() solves the optical-to-thermal transform once, caches it in
images/registration_transform.json and reuses it on every later capture. This
module never re-solves it, for two reasons:

- One solve is noisy: repeated solves of one scene spread over about 101 px, which
  is why the cache is seeded from the medoid of several (seed_registration_cache.py).
  Re-solving automatically could swap a good transform for an outlier.
- Turning the whole node barely changes the transform. Both cameras are bolted to
  the same housing; only parallax shifts one against the other, and that is small
  at the distances the node looks at.

So a large turn is a reason for someone to look, not proof the alignment is off.
This records the IMU pose the cached transform belongs to and, on each capture,
writes registration_check.json into the capture directory saying whether the node
has turned past a threshold since then.

How a turn is measured. Nodes are mounted near 90° of pitch, where Euler angles
hit gimbal lock (a small tilt swings heading and roll a long way), so the angles
are not compared one by one. Instead:

- tilt: the angle between the two gravity vectors. Accelerometer-based, steady.
- rotation: the angle of the rotation between the two quaternions, heading
  included. Heading comes from the magnetometer, which is meaningless until the
  BNO055 is calibrated (nodes without bno055_calibration.json read heading 0), so
  rotation only counts when the magnetometer was calibrated (status 2 or 3) at
  both readings.

The reference lives in the cache's metadata under "orientation". A cache without
one adopts the current pose: after a fresh solve, after a re-seed (which writes a
new cache), or for a cache from before this check. Re-seeding therefore clears a
flag, and so does

    python -m tools.registration_orientation --reset

(run from ~/SU-WaterCam) once the alignment has been checked by eye and found good.
With no argument it prints the check without changing anything.
"""

import argparse
import datetime
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

from tools.config_io import ConfigUnreadable, locked, read_json, write_json

#: Must match coreg_multiple.config.TRANSFORM_CACHE_FILENAME. Kept as a constant
#: so the check doesn't import OpenCV, SimpleITK and rasterio just to find a file.
CACHE_FILENAME = "registration_transform.json"
CAPTURE_FLAG_FILENAME = "registration_check.json"
DEFAULT_TILT_THRESHOLD_DEG = 5.0
DEFAULT_ROTATION_THRESHOLD_DEG = 15.0
#: calibration_status is (sys, gyro, accel, mag), each 0-3.
MAG_CALIBRATED = 2


def cache_path(capture_dir: str) -> Optional[str]:
    """The cache coreg() would use for capture_dir: its parent's first, then its own."""
    normed = os.path.normpath(capture_dir)
    for d in (os.path.dirname(normed) or normed, capture_dir):
        path = os.path.join(d, CACHE_FILENAME)
        if os.path.exists(path):
            return path
    return None


def _vector(v: Any, n: int) -> Optional[List[float]]:
    if not isinstance(v, (list, tuple)) or len(v) != n:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return None
    norm = math.sqrt(sum(x * x for x in v))
    if norm < 1e-6:
        return None
    return [float(x) for x in v]


def pose_from_reading(reading: Any) -> Optional[Dict[str, Any]]:
    """The parts of a bno055_imu.get_pose() reading kept as a reference, or None."""
    if not isinstance(reading, dict):
        return None
    q = _vector(reading.get("quaternion"), 4)
    g = _vector(reading.get("gravity"), 3)
    if q is None or g is None:
        return None
    calib = reading.get("calibration_status")
    mag_ok = (isinstance(calib, (list, tuple)) and len(calib) == 4
              and isinstance(calib[3], int) and calib[3] >= MAG_CALIBRATED)
    euler = _vector(reading.get("euler"), 3)
    return {"quaternion": q, "gravity": g, "euler_deg": euler, "mag_calibrated": mag_ok}


def _stored_pose(d: Any) -> Optional[Dict[str, Any]]:
    """A reference as _store() wrote it, or None if missing or damaged."""
    if not isinstance(d, dict):
        return None
    if _vector(d.get("quaternion"), 4) is None or _vector(d.get("gravity"), 3) is None:
        return None
    return dict(d, mag_calibrated=bool(d.get("mag_calibrated")))


def tilt_deg(g1: List[float], g2: List[float]) -> float:
    """Angle between two gravity vectors."""
    dot = sum(a * b for a, b in zip(g1, g2))
    n = math.sqrt(sum(a * a for a in g1)) * math.sqrt(sum(b * b for b in g2))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot / n))))


def rotation_deg(q1: List[float], q2: List[float]) -> float:
    """Angle of the rotation taking one orientation to the other, 0-180."""
    dot = abs(sum(a * b for a, b in zip(q1, q2)))
    n = math.sqrt(sum(a * a for a in q1)) * math.sqrt(sum(b * b for b in q2))
    return math.degrees(2.0 * math.acos(max(-1.0, min(1.0, dot / n))))


def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _store(path: str, data: Dict[str, Any], pose: Dict[str, Any], when: str) -> None:
    data.setdefault("metadata", {})["orientation"] = dict(pose, recorded_at=when)
    write_json(path, data, mode=0o644)


def check(capture_dir: str, reading: Any,
          tilt_threshold_deg: float = DEFAULT_TILT_THRESHOLD_DEG,
          rotation_threshold_deg: float = DEFAULT_ROTATION_THRESHOLD_DEG,
          adopt: bool = True) -> Dict[str, Any]:
    """Compare the current pose with the one the cached transform belongs to.

    status is one of:
      ok        within the thresholds
      shifted   tilted by tilt_threshold_deg or more, or (magnetometer calibrated at
                both readings) rotated by rotation_threshold_deg or more
      adopted   the cache had no reference, so this reading became it
      no_reference  the cache has no reference and adopt is False
      no_cache  there is no cached transform yet
      no_imu    the reading is unusable
    """
    pose = pose_from_reading(reading)
    result: Dict[str, Any] = {
        "status": None,
        "checked_at": _now(),
        "current": pose,
        "thresholds_deg": {"tilt": tilt_threshold_deg, "rotation": rotation_threshold_deg},
    }
    if pose is None:
        result["status"] = "no_imu"
        return result
    path = cache_path(capture_dir)
    result["cache"] = path
    if path is None:
        result["status"] = "no_cache"
        return result

    with locked(path):
        data = read_json(path)
        ref = _stored_pose(data.get("metadata", {}).get("orientation"))
        if ref is None:
            if not adopt:
                result["status"] = "no_reference"
                return result
            _store(path, data, pose, result["checked_at"])
            result["status"] = "adopted"
            return result

    result["reference"] = ref
    tilt = round(tilt_deg(ref["gravity"], pose["gravity"]), 2)
    rotation = round(rotation_deg(ref["quaternion"], pose["quaternion"]), 2)
    heading_reliable = ref["mag_calibrated"] and pose["mag_calibrated"]
    result["change_deg"] = {"tilt": tilt, "rotation": rotation}
    result["heading_reliable"] = heading_reliable
    shifted = tilt >= tilt_threshold_deg or (heading_reliable and rotation >= rotation_threshold_deg)
    result["status"] = "shifted" if shifted else "ok"
    return result


def reset(capture_dir: str, reading: Any) -> Dict[str, Any]:
    """Make the current pose the reference, after the alignment was checked."""
    pose = pose_from_reading(reading)
    if pose is None:
        return {"status": "no_imu"}
    path = cache_path(capture_dir)
    if path is None:
        return {"status": "no_cache"}
    with locked(path):
        _store(path, read_json(path), pose, _now())
    return {"status": "reset", "cache": path, "reference": pose}


def write_capture_flag(capture_dir: str, result: Dict[str, Any]) -> None:
    """Record the check next to the capture it applies to."""
    with open(os.path.join(capture_dir, CAPTURE_FLAG_FILENAME), "w") as f:
        json.dump(result, f, indent=2)


def describe(result: Dict[str, Any]) -> str:
    status = result.get("status")
    if status in ("shifted", "ok"):
        c = result["change_deg"]
        t = result["thresholds_deg"]
        rot = (f"rotated {c['rotation']}°" if result["heading_reliable"]
               else f"rotated {c['rotation']}° (heading unreliable, not counted)")
        if status == "ok":
            return f"Registration orientation OK: tilted {c['tilt']}°, {rot} since the reference"
        return (f"⚠️ Node has turned since the registration transform was cached: "
                f"tilted {c['tilt']}°, {rot} (thresholds {t['tilt']}° tilt, "
                f"{t['rotation']}° rotation). The cached transform is still used: check "
                f"the alignment, then re-seed or reset (tools/registration_orientation.py).")
    if status == "adopted":
        return "Registration orientation reference recorded for the cached transform"
    return f"Registration orientation check: {status}"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--images", default=os.path.join(
        os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam"), "images"),
        help="directory holding registration_transform.json (default: the repo's images/)")
    p.add_argument("--reset", action="store_true",
                   help="make the current pose the reference")
    a = p.parse_args(argv)

    from tools.bno055_imu import get_pose
    # cache_path() looks in the parent of what it is given, so hand it a child path.
    probe = os.path.join(a.images, "_check")
    try:
        if a.reset:
            result = reset(probe, get_pose())
        else:
            result = check(probe, get_pose(), adopt=False)
    except (OSError, ConfigUnreadable) as e:
        print(f"ERROR: {e}")
        return 1
    print(json.dumps(result, indent=2))
    if not a.reset:
        print(describe(result))
    return 0 if result["status"] in ("ok", "reset", "no_reference") else 1


if __name__ == "__main__":
    sys.exit(main())
