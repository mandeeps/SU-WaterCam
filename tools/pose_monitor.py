#!/home/pi/SU-WaterCam/venv/bin/python
"""Has the camera been knocked, removed, or tilted enough to spoil georeferencing?

Every capture compares the IMU's orientation with a reference saved once the unit
is mounted: the tilt between the gravity vectors (accelerometer, steady even near
90 deg pitch) and, when the magnetometer is fully calibrated at both readings,
the rotation between the quaternions. Levels (`pose_level`, sent as 01 0E with
the tilt as 01 0F):

    0    ok
    1    georeferencing out of date: tilt >= GEOREF_TILT_DEG (or rotation >=
         GEOREF_ROTATION_DEG) for GEOREF_CAPTURES captures in a row. About 2 m
         of ground error at 20 m per degree of pitch, 9 m at 40 m (3 m mount).
    2    knocked: tilt >= KNOCK_TILT_DEG or rotation >= KNOCK_ROTATION_DEG for
         KNOCK_CAPTURES captures in a row; a GPS fix that wake.
    3    removed or being handled: tilt >= REMOVED_TILT_DEG, the samples within
         one capture spread HANDLING_SPREAD_DEG or more, or GPS says it moved;
         at once, and a GPS fix that wake and the next GPS_WAKES_AFTER_REMOVED - 1.
    255  no reference yet: one is adopted after REFERENCE_CAPTURES captures in a
         row within REFERENCE_STABLE_DEG of each other.

On 006's mount (2026 field season) consecutive readings differed by at most
0.25 deg pitch and 0.6 deg roll 95 % of the time, and about 1 % jumped 8-15 deg
for one reading (fusion glitches): hence the captures-in-a-row rule.

Levels stay until the reference is reset (downlink 19 9A, or --reset here) after
the georeferencing has been checked or redone. State: data/pose_state.json.
"""
import json
import math
import os
import sys
import time
from typing import Dict, List, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tools import config_io                                      # noqa: E402
from tools.registration_orientation import rotation_deg, tilt_deg  # noqa: E402

STATE_FILE = os.path.join(_REPO, "data", "pose_state.json")

GEOREF_TILT_DEG, GEOREF_ROTATION_DEG, GEOREF_CAPTURES = 1.0, 3.0, 3
# 10/25, raised from 5/15 (2026-10-28): 005 drifted 8 deg tilt and 11 deg rotation
# on the bench, and a knock means a GPS fix at once
KNOCK_TILT_DEG, KNOCK_ROTATION_DEG, KNOCK_CAPTURES = 10.0, 25.0, 2
REMOVED_TILT_DEG = 20.0
HANDLING_SPREAD_DEG = 3.0
REFERENCE_CAPTURES, REFERENCE_STABLE_DEG = 3, 0.5
GPS_WAKES_AFTER_REMOVED = 3
SAMPLES, SAMPLE_GAP_S = 5, 0.2
MAG_FULL = 3

OK, GEOREF, KNOCKED, REMOVED, NO_REFERENCE = 0, 1, 2, 3, 255

_last = {}      # capture id -> result, so the LoRa and IP paths share one reading


def _load(path: str) -> Dict:
    try:
        with open(path) as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(state: Dict, path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        config_io.write_json(path, state, mode=0o664)
    except OSError as e:
        print(f"⚠️ pose state not saved: {e}")


def read_samples(n: int = SAMPLES, gap_s: float = SAMPLE_GAP_S) -> List[Dict]:
    """A few IMU poses about a second apart; [] without an IMU."""
    from tools.bno055_imu import get_pose
    out = []
    for i in range(n):
        p = get_pose()
        if not (p.get("gravity") and p.get("quaternion")):
            if not out:
                return []           # no IMU (or it isn't answering): don't wait for more
            continue
        out.append(p)
        if i < n - 1:
            time.sleep(gap_s)
    return out


def _mean_gravity(samples: List[Dict]) -> List[float]:
    return [sum(s["gravity"][i] for s in samples) / len(samples) for i in range(3)]


def _mag_full(pose: Dict) -> bool:
    c = pose.get("calibration_status")
    return bool(c) and len(c) == 4 and c[3] >= MAG_FULL


def assess(samples: List[Dict], state: Dict, gps_moved: bool = False) -> Dict:
    """Update `state` for one capture's samples and return the result (pure, testable)."""
    if not samples:
        return {"level": state.get("level", NO_REFERENCE), "tilt": None, "rotation": None, "note": "no IMU"}
    g = _mean_gravity(samples)
    q = samples[len(samples) // 2]["quaternion"]
    # the largest difference between any two samples of this capture
    spread = max((tilt_deg(a["gravity"], b["gravity"]) for i, a in enumerate(samples)
                  for b in samples[i + 1:]), default=0.0)
    mag = all(_mag_full(s) for s in samples)
    ref = state.get("reference")

    if not ref:
        cands = state.setdefault("candidates", [])
        cands.append({"gravity": g, "quaternion": list(q), "mag_full": mag})
        cands[:] = cands[-REFERENCE_CAPTURES:]
        if spread < HANDLING_SPREAD_DEG and len(cands) == REFERENCE_CAPTURES and \
                max(tilt_deg(cands[0]["gravity"], c["gravity"]) for c in cands) < REFERENCE_STABLE_DEG:
            state["reference"] = dict(cands[-1], set_at=time.time())
            state.pop("candidates", None)
            state.update(level=OK, georef_run=0, knock_run=0)
            return {"level": OK, "tilt": 0.0, "rotation": 0.0, "note": "reference set"}
        state["level"] = NO_REFERENCE
        return {"level": NO_REFERENCE, "tilt": None, "rotation": None, "note": "no reference yet"}

    tilt = tilt_deg(ref["gravity"], g)
    rotation = rotation_deg(ref["quaternion"], q) if (mag and ref.get("mag_full")) else None
    level = state.get("level", OK)
    if level == NO_REFERENCE:
        level = OK
    note = ""

    if tilt >= REMOVED_TILT_DEG or spread >= HANDLING_SPREAD_DEG or gps_moved:
        new = REMOVED
        note = ("moved (GPS)" if gps_moved else
                f"being handled (samples spread {spread:.1f} deg)" if spread >= HANDLING_SPREAD_DEG else
                f"tilted {tilt:.1f} deg")
    else:
        knocked = tilt >= KNOCK_TILT_DEG or (rotation is not None and rotation >= KNOCK_ROTATION_DEG)
        off = tilt >= GEOREF_TILT_DEG or (rotation is not None and rotation >= GEOREF_ROTATION_DEG)
        state["knock_run"] = state.get("knock_run", 0) + 1 if knocked else 0
        state["georef_run"] = state.get("georef_run", 0) + 1 if off else 0
        if state["knock_run"] >= KNOCK_CAPTURES:
            new = KNOCKED
        elif state["georef_run"] >= GEOREF_CAPTURES:
            new = GEOREF
        else:
            new = OK
    if new > level:          # levels stay until the reference is reset
        if new >= KNOCKED:
            state["gps_wakes"] = GPS_WAKES_AFTER_REMOVED if new == REMOVED else 1
        level = new
    state["level"] = level
    state["last"] = {"tilt": round(tilt, 2), "rotation": None if rotation is None else round(rotation, 2),
                     "spread": round(spread, 2), "at": time.time()}
    return {"level": level, "tilt": tilt, "rotation": rotation, "note": note}


def check(capture_id: str, gps_moved: bool = False, path: Optional[str] = None,
          samples: Optional[List[Dict]] = None) -> Dict:
    """This capture's result; read once per capture, whichever uplink asks first."""
    if capture_id in _last:
        return _last[capture_id]
    path = path or STATE_FILE
    try:
        samples = read_samples() if samples is None else samples
    except Exception as e:      # the IMU is optional
        print(f"⚠️ pose check: IMU unreadable ({e})")
        samples = []
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with config_io.locked(path):
        state = _load(path)
        before = state.get("level", NO_REFERENCE)
        result = assess(samples, state, gps_moved)
        _save(state, path)
    if result["level"] != before and result["level"] in (GEOREF, KNOCKED, REMOVED):
        print(f"📐 Pose level {before} -> {result['level']}: {result['note'] or 'tilt %.1f deg' % result['tilt']}")
        if result["level"] >= KNOCKED:
            try:
                from tools import gps_window
                gps_window.start_if_due()
            except Exception as e:
                print(f"⚠️ GPS fix after the knock not started: {e}")
    _last.clear()
    _last[capture_id] = result
    return result


def take_gps_wake(path: Optional[str] = None) -> bool:
    """Use one of the GPS fixes a knock or removal asked for; True if there was one."""
    path = path or STATE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with config_io.locked(path):
        state = _load(path)
        left = int(state.get("gps_wakes", 0) or 0)
        if left <= 0:
            return False
        state["gps_wakes"] = left - 1
        _save(state, path)
    return True


def reset(path: Optional[str] = None) -> None:
    """Forget the reference; the next stable captures become the new one."""
    path = path or STATE_FILE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with config_io.locked(path):
        state = _load(path)
        _save({"level": NO_REFERENCE, "reset_at": time.time(),
               "previous": {k: state.get(k) for k in ("level", "last")}}, path)
    _last.clear()


def fields(result: Dict) -> Dict:
    """Packet fields: pose_level (01 0E) and pose_tilt_tenths (01 0F, 0.1 deg)."""
    out = {"pose_level": int(result.get("level", NO_REFERENCE))}
    if result.get("tilt") is not None:
        out["pose_tilt_tenths"] = min(65535, int(round(result["tilt"] * 10)))
    return out


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Camera pose monitor")
    p.add_argument("--reset", action="store_true", help="forget the reference (after re-georeferencing)")
    p.add_argument("--show", action="store_true")
    a = p.parse_args()
    if a.reset:
        reset()
        print("reference cleared; the next stable captures set a new one")
    print(json.dumps(_load(STATE_FILE), indent=2))
