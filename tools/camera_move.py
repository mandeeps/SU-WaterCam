#!/home/pi/SU-WaterCam/venv/bin/python
"""Has the camera turned since its reference photo? Measured from the photos.

The pose monitor (tools/pose_monitor.py) watches the IMU, which is quick to see a
knock but drifts: on 006 (25 Oct - 12 Nov 2026) its gravity reading wandered
2.2-2.9 deg while its photos showed the camera turned at most 0.18 deg. Photos
don't leave the node in normal operation, so the node compares them itself, once
a day, and sends the angle.

How. The capture's NIR-OFF photo is undistorted with the lens calibration
(config/camera_calibration.json), reduced to a quarter, and its SIFT features are
matched (FLANN) against two earlier photos:

- the reference: the first photo checked after the reference was cleared (at
  install, or when "Accept current orientation", downlink 19 9A, resets the pose
  monitor). Its angle is the answer while enough features match, but leaves,
  light and water change the scene: on 006 the matches fell from ~850 to 30-40
  in three weeks;
- the previous day's checked photo, which still matches well. Each day's step
  is added to a running total, which a good reference match resets.

Each match is fitted as a pure rotation: a homography with RANSAC picks the
matching points, then the three rotation angles are fitted to them (robust least
squares). On 006's 18 daily photos this was within 0.03 deg of an offline fit
with five times the features, and took 1.0 s on a Pi 4 (1.4 s on one core).

When. The first capture of the day from CHECK_FROM_HOUR (local time) whose photo is
lit (mean grey >= LIT_MIN_MEAN), at most TRIES_PER_DAY tries a day; a capture
that isn't checked sends nothing.

Sent to the server as field code 01 12, 4 bytes: rotation since the reference
in 0.01 deg (u16), matched points behind it (u8, capped at 255) and the basis
(u8): 0 the reference photo, 1 the running total, 2 a new reference adopted
(rotation 0). Both transports, only from the capture that was checked.

State: data/camera_move/ (reference.npz, previous.npz, state.json). The result
also goes into the capture's capture_status.json (camera_move).

    python -m tools.camera_move <capture dir>...   check these (from ~/SU-WaterCam)
    python -m tools.camera_move --reset            adopt a new reference next time
    python -m tools.camera_move --show             print the state
"""
import datetime as _dt
import glob
import json
import math
import os
import shutil
import sys
import time
from typing import Any, Dict, Optional, Tuple

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tools import capture_status, config_io                     # noqa: E402

STATE_DIR = os.path.join(_REPO, "data", "camera_move")
CALIBRATION_PATH = os.path.join(_REPO, "config", "camera_calibration.json")

CHECK_FROM_HOUR = 11          # local time; midday light, the hours the benchmark used
TRIES_PER_DAY = 3
LIT_MIN_MEAN = 60.0           # mean grey (of 255) of the photo at 1/8 size (brightness())
SCALE = 0.25                  # of the full photo
N_FEATURES = 4000
RATIO = 0.8                   # Lowe's ratio test
RANSAC_PX = 2.0
MIN_INLIERS = 20              # below this a match gives no angle
REFERENCE_MIN_INLIERS = 50    # below this the running total is reported instead

BASIS_REFERENCE, BASIS_CHAIN, BASIS_NEW_REFERENCE = 0, 1, 2
_BASIS_NAME = {BASIS_REFERENCE: "reference", BASIS_CHAIN: "chain", BASIS_NEW_REFERENCE: "new_reference"}


# ---------------------------------------------------------------- image work

def _intrinsics(w: int, h: int, path: str = CALIBRATION_PATH) -> Tuple[np.ndarray, np.ndarray]:
    """(K, D) for a w x h frame: the calibration scaled to it, or a nominal pinhole."""
    try:
        with open(path) as f:
            cal = json.load(f)
        K = np.array(cal["K"], float)
        D = np.array(cal["D"], float).ravel()
        cw, ch = (cal.get("img_size") or [w, h])[:2]
        if abs(w / cw - h / ch) < 1e-3:
            K = K.copy()
            K[0, 0] *= w / cw; K[0, 2] *= w / cw
            K[1, 1] *= h / ch; K[1, 2] *= h / ch
            return K, D
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        pass
    f = 1.14 * w                  # OV5647 / Pi camera v1, 53.5 deg horizontal
    return np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]], float), np.zeros(5)


def photo_of(dirname: str) -> Optional[str]:
    """The capture's NIR-OFF photo (the filter in: an ordinary colour photo)."""
    found = sorted(glob.glob(os.path.join(dirname, "*-NIR-OFF.jpg")))
    return found[0] if found else None


def brightness(photo: str) -> Optional[float]:
    """Mean grey of the photo decoded at 1/8 size, for the lit test; None if unreadable.

    About 0.03 s on a Pi 4 against about 0.4 s for features(). On 011's photos it was
    within 1 grey level of the mean of the undistorted, reduced photo.
    """
    import cv2
    grey = cv2.imread(photo, cv2.IMREAD_REDUCED_GRAYSCALE_8)
    return None if grey is None else float(grey.mean())


def features(photo: str, calibration: str = CALIBRATION_PATH) -> Optional[Dict[str, np.ndarray]]:
    """SIFT features of the undistorted, reduced photo, or None when it can't be read.

    The JPEG is decoded at half size (cheap) and reduced to SCALE after undistorting.
    "mean" is the reduced photo's mean grey (kept in the saved features; the lit test uses brightness()).
    """
    import cv2
    im = cv2.imread(photo, cv2.IMREAD_REDUCED_COLOR_2)
    if im is None:
        return None
    h, w = im.shape[:2]
    K, D = _intrinsics(w, h, calibration)
    Kn, _ = cv2.getOptimalNewCameraMatrix(K, D, (w, h), alpha=0)
    und = cv2.undistort(im, K, D, None, Kn)
    s = SCALE / 0.5
    grey = cv2.cvtColor(cv2.resize(und, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    mean = float(grey.mean())
    grey = cv2.createCLAHE(2.0, (8, 8)).apply(grey)
    kps, desc = cv2.SIFT_create(nfeatures=N_FEATURES, contrastThreshold=0.01).detectAndCompute(grey, None)
    Ks = Kn.copy()
    Ks[:2] *= s
    pts = np.float32([k.pt for k in kps]).reshape(-1, 2)
    # SIFT descriptors are whole numbers 0-255 stored as floats: keep them as bytes
    desc = np.zeros((0, 128), np.uint8) if desc is None else np.clip(np.rint(desc), 0, 255).astype(np.uint8)
    return {"pts": pts, "desc": desc, "K": Ks, "mean": np.float64(mean)}


def match(a: Dict[str, np.ndarray], b: Dict[str, np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    """Matched point pairs (a's, b's) that pass the ratio test."""
    import cv2
    if len(a["desc"]) < 2 or len(b["desc"]) < 2:
        return np.zeros((0, 2), np.float32), np.zeros((0, 2), np.float32)
    flann = cv2.FlannBasedMatcher(dict(algorithm=1, trees=4), dict(checks=48))
    pairs = flann.knnMatch(a["desc"].astype(np.float32), b["desc"].astype(np.float32), k=2)
    good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < RATIO * n.distance]
    return (a["pts"][[m.queryIdx for m in good]].reshape(-1, 2),
            b["pts"][[m.trainIdx for m in good]].reshape(-1, 2))


def rotation(pa: np.ndarray, pb: np.ndarray, K: np.ndarray) -> Optional[Dict[str, Any]]:
    """Rotation taking a's view to b's, fitted to the matches, or None.

    {"rotvec": [x, y, z] (radians), "deg", "inliers", "p90_px"}.
    """
    import cv2
    from scipy.optimize import least_squares
    if len(pa) < MIN_INLIERS:
        return None
    H, mask = cv2.findHomography(pa, pb, cv2.USAC_MAGSAC, RANSAC_PX)
    if H is None or mask is None or int(mask.sum()) < MIN_INLIERS:
        return None
    inl = mask.ravel().astype(bool)
    Ki = np.linalg.inv(K)
    U, _, Vt = np.linalg.svd(Ki @ H @ K)
    R0 = U @ Vt
    if np.linalg.det(R0) < 0:
        R0 = -R0
    a, b = pa[inl].reshape(-1, 1, 2).astype(np.float64), pb[inl].astype(np.float64)

    def resid(v):
        Hr = K @ cv2.Rodrigues(v)[0] @ Ki
        return (cv2.perspectiveTransform(a, Hr).reshape(-1, 2) - b).ravel()

    fit = least_squares(resid, cv2.Rodrigues(R0)[0].ravel(), loss="soft_l1", f_scale=1.0)
    v = fit.x
    return {"rotvec": [float(x) for x in v], "deg": math.degrees(float(np.linalg.norm(v))),
            "inliers": int(inl.sum()),
            "p90_px": float(np.percentile(np.abs(fit.fun.reshape(-1, 2)).max(1), 90))}


def _compose(step: list, total: list) -> list:
    """Rotation vector of step after total."""
    import cv2
    R = cv2.Rodrigues(np.array(step, float))[0] @ cv2.Rodrigues(np.array(total, float))[0]
    return [float(x) for x in cv2.Rodrigues(R)[0].ravel()]


# ---------------------------------------------------------------- state

def _paths(state_dir: str) -> Dict[str, str]:
    return {k: os.path.join(state_dir, f) for k, f in
            (("state", "state.json"), ("reference", "reference.npz"), ("previous", "previous.npz"))}


def _load_state(state_dir: str) -> Dict[str, Any]:
    try:
        with open(_paths(state_dir)["state"]) as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state_dir: str, state: Dict[str, Any]) -> None:
    os.makedirs(state_dir, exist_ok=True)
    config_io.write_json(_paths(state_dir)["state"], state, mode=0o664)


def _load_features(path: str) -> Optional[Dict[str, np.ndarray]]:
    try:
        with np.load(path) as z:
            return {k: z[k] for k in ("pts", "desc", "K", "mean")}
    except (OSError, ValueError, KeyError):
        return None


def _save_features(path: str, f: Dict[str, np.ndarray]) -> None:
    tmp = path + ".tmp.npz"
    np.savez(tmp, **f)
    os.replace(tmp, path)


def reset(state_dir: Optional[str] = None) -> None:
    """Forget the reference: the next checked photo becomes the new one."""
    state_dir = state_dir or STATE_DIR
    if os.path.isdir(state_dir):
        shutil.rmtree(state_dir, ignore_errors=True)


# ---------------------------------------------------------------- the check

def due(state: Dict[str, Any], now: _dt.datetime) -> bool:
    """Is a check due at local time `now`: from CHECK_FROM_HOUR, once a day, few tries?"""
    if now.hour < CHECK_FROM_HOUR:
        return False
    today = now.date().isoformat()
    if state.get("last_check_date") == today:
        return False
    tries = state.get("tries") or {}
    return int(tries.get(today, 0)) < TRIES_PER_DAY


def check(dirname: str, now: Optional[_dt.datetime] = None, state_dir: Optional[str] = None,
          calibration: str = CALIBRATION_PATH, force: bool = False) -> Optional[Dict[str, Any]]:
    """Check the capture in dirname if a check is due; record and return the result.

    None when no check was due or possible (no photo, too dark, too few matches);
    otherwise {"basis", "deg", "inliers", ...}. Never raises on a bad photo.
    """
    state_dir = state_dir or STATE_DIR
    now = now or _dt.datetime.now()
    state = _load_state(state_dir)
    if not force and not due(state, now):
        return None
    photo = photo_of(dirname)
    if photo is None:
        return None
    t0 = time.monotonic()
    lit = brightness(photo)
    if lit is None:
        return None
    if lit < LIT_MIN_MEAN:            # before the features: a dark photo costs a 1/8-size decode only
        capture_status.mark(dirname, camera_move="too_dark", camera_move_mean=round(lit, 1))
        return None
    cur = features(photo, calibration)
    if cur is None:
        return None

    today = now.date().isoformat()
    tries = {today: int((state.get("tries") or {}).get(today, 0)) + 1}   # only today's count is kept
    paths = _paths(state_dir)
    capture = os.path.basename(os.path.normpath(dirname))
    ref = _load_features(paths["reference"])
    os.makedirs(state_dir, exist_ok=True)

    if ref is None:
        _save_features(paths["reference"], cur)
        _save_features(paths["previous"], cur)
        result = {"basis": BASIS_NEW_REFERENCE, "deg": 0.0, "rotvec": [0.0, 0.0, 0.0],
                  "inliers": min(255, len(cur["pts"])), "reference": capture}
        _save_state(state_dir, {"reference": capture, "reference_date": today, "previous": capture,
                                "total_rotvec": [0.0, 0.0, 0.0], "last_check_date": today, "tries": tries})
    else:
        prev = _load_features(paths["previous"])
        to_ref = rotation(*match(ref, cur), ref["K"])
        step = rotation(*match(prev, cur), ref["K"]) if prev is not None and \
            state.get("previous") != state.get("reference") else None
        total = state.get("total_rotvec") or [0.0, 0.0, 0.0]
        if to_ref is not None and to_ref["inliers"] >= REFERENCE_MIN_INLIERS:
            basis, rotvec, inliers = BASIS_REFERENCE, to_ref["rotvec"], to_ref["inliers"]
        elif step is not None:
            basis, rotvec, inliers = BASIS_CHAIN, _compose(step["rotvec"], total), step["inliers"]
        elif to_ref is not None:          # previous was the reference: this is the same match
            basis, rotvec, inliers = BASIS_CHAIN, to_ref["rotvec"], to_ref["inliers"]
        else:
            state["tries"] = tries
            _save_state(state_dir, state)
            capture_status.mark(dirname, camera_move="no_match")
            return None
        _save_features(paths["previous"], cur)
        result = {"basis": basis, "deg": math.degrees(float(np.linalg.norm(rotvec))),
                  "rotvec": [float(x) for x in rotvec], "inliers": int(inliers),
                  "reference": state.get("reference"),
                  "reference_deg": None if to_ref is None else round(to_ref["deg"], 3),
                  "reference_inliers": None if to_ref is None else to_ref["inliers"],
                  "step_deg": None if step is None else round(step["deg"], 3),
                  "step_inliers": None if step is None else step["inliers"]}
        state.update(previous=capture, total_rotvec=result["rotvec"], last_check_date=today, tries=tries)
        _save_state(state_dir, state)

    result["deg"] = round(result["deg"], 3)
    result["seconds"] = round(time.monotonic() - t0, 2)
    capture_status.mark(dirname, camera_move=_BASIS_NAME[result["basis"]], camera_move_result=result)
    return result


def fields_for_capture(dirname: str) -> Dict[str, int]:
    """Packet fields for the capture in dirname: empty unless it was checked.

    camera_rotation_hundredths (0.01 deg, u16), camera_move_inliers (u8),
    camera_move_basis (0 reference, 1 running total, 2 new reference); field code 01 12.
    """
    if not dirname:
        return {}
    r = capture_status.read(dirname).get("camera_move_result")
    if not isinstance(r, dict) or r.get("basis") not in _BASIS_NAME:
        return {}
    try:
        return {"camera_rotation_hundredths": max(0, min(65535, int(round(float(r["deg"]) * 100)))),
                "camera_move_inliers": max(0, min(255, int(r["inliers"]))),
                "camera_move_basis": int(r["basis"])}
    except (KeyError, TypeError, ValueError):
        return {}


def payload(data: Dict[str, Any]) -> bytes:
    """The 4-byte value of field 01 12 from the packet fields."""
    import struct
    return struct.pack(">HBB", int(data["camera_rotation_hundredths"]) & 0xFFFF,
                       int(data["camera_move_inliers"]) & 0xFF, int(data["camera_move_basis"]) & 0xFF)


def main(argv) -> int:
    if argv[1:2] == ["--reset"]:
        reset()
        print("Camera reference cleared: the next checked photo becomes the reference.")
        return 0
    if argv[1:2] == ["--show"]:
        print(json.dumps(_load_state(STATE_DIR), indent=2))
        return 0
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    for d in argv[1:]:
        r = check(d, force=True)
        print(d, json.dumps(r) if r else "no result (no photo, too dark or too few matches)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
