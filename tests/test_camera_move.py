"""Camera-move check from the photos (tools/camera_move.py): synthetic views of one
textured scene turned by known rotations."""
import datetime as dt
import json
import math
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
pytest.importorskip("scipy")

from tools import camera_move as cm          # noqa: E402
from tools import capture_status             # noqa: E402

W, H = 1296, 972                              # the photo; the check reads it at half size
F = 1.14 * W
K = np.array([[F, 0, W / 2], [0, F, H / 2], [0, 0, 1]], float)
NOON = dt.datetime(2026, 11, 2, 12, 0)


@pytest.fixture(scope="module")
def scene():
    """A wide textured scene the views are cut from (blobs at several sizes)."""
    rng = np.random.default_rng(7)
    img = np.zeros((H + 400, W + 400), np.float32)
    for size in (5, 11, 23, 47):
        small = rng.random(((H + 400) // size + 2, (W + 400) // size + 2)).astype(np.float32)
        img += cv2.resize(small, None, fx=size, fy=size, interpolation=cv2.INTER_CUBIC)[:H + 400, :W + 400]
    img = cv2.normalize(img, None, 40, 230, cv2.NORM_MINMAX)
    return cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_GRAY2BGR)


def _view(scene, rx=0.0, ry=0.0, rz=0.0, brightness=1.0):
    """The scene as seen by the camera turned by (rx, ry, rz) degrees."""
    R = cv2.Rodrigues(np.radians([rx, ry, rz]))[0]
    Ks = K.copy(); Ks[0, 2] += 200; Ks[1, 2] += 200     # the scene's centre
    Hm = K @ R @ np.linalg.inv(Ks)
    out = cv2.warpPerspective(scene, Hm, (W, H))
    return np.clip(out.astype(np.float32) * brightness, 0, 255).astype(np.uint8)


@pytest.fixture
def env(tmp_path):
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps({"K": K.tolist(), "D": [[0, 0, 0, 0, 0]], "img_size": [W, H]}))
    return {"root": tmp_path, "state": str(tmp_path / "state"), "cal": str(cal)}


def _capture(env, name, image):
    d = env["root"] / name
    d.mkdir()
    cv2.imwrite(str(d / f"{name}-NIR-OFF.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return str(d)


def _check(env, d, now, **kw):
    return cm.check(d, now=now, state_dir=env["state"], calibration=env["cal"], **kw)


def test_first_photo_becomes_the_reference_then_the_turn_is_measured(env, scene):
    first = _check(env, _capture(env, "20261102-120000", _view(scene)), NOON)
    assert first["basis"] == cm.BASIS_NEW_REFERENCE and first["deg"] == 0.0
    d = _capture(env, "20261103-120000", _view(scene, rx=0.2, ry=0.5))
    r = _check(env, d, NOON + dt.timedelta(days=1))
    assert r["basis"] == cm.BASIS_REFERENCE
    assert r["deg"] == pytest.approx(math.hypot(0.2, 0.5), abs=0.03)
    assert r["inliers"] >= cm.REFERENCE_MIN_INLIERS
    assert capture_status.read(d)["camera_move"] == "reference"
    assert cm.fields_for_capture(d) == {"camera_rotation_hundredths": round(r["deg"] * 100),
                                        "camera_move_inliers": min(255, r["inliers"]),
                                        "camera_move_basis": 0}


def test_once_a_day_from_the_check_hour(env, scene):
    d = _capture(env, "20261102-090000", _view(scene))
    assert _check(env, d, NOON.replace(hour=cm.CHECK_FROM_HOUR - 1)) is None   # too early
    assert _check(env, d, NOON)["basis"] == cm.BASIS_NEW_REFERENCE
    again = _capture(env, "20261102-130000", _view(scene, ry=1))
    assert _check(env, again, NOON.replace(hour=13)) is None                     # done today
    assert cm.fields_for_capture(again) == {}
    assert _check(env, again, NOON + dt.timedelta(days=1))["deg"] == pytest.approx(1.0, abs=0.03)


def test_dark_photo_is_skipped_and_does_not_use_a_try(env, scene):
    d = _capture(env, "20261102-120000", _view(scene, brightness=0.15))
    assert _check(env, d, NOON) is None
    assert capture_status.read(d)["camera_move"] == "too_dark"
    assert cm.fields_for_capture(d) == {}
    assert not os.path.exists(os.path.join(env["state"], "reference.npz"))


def test_unmatched_photo_counts_tries(env, scene):
    _check(env, _capture(env, "20261102-120000", _view(scene)), NOON)
    rng = np.random.default_rng(1)
    noise = cv2.cvtColor(rng.integers(60, 200, (H, W), dtype=np.uint8), cv2.COLOR_GRAY2BGR)
    day2 = NOON + dt.timedelta(days=1)
    for i in range(cm.TRIES_PER_DAY):
        d = _capture(env, f"20261103-12000{i}", noise)
        assert _check(env, d, day2) is None
        assert capture_status.read(d)["camera_move"] == "no_match"
    late = _capture(env, "20261103-150000", _view(scene))
    assert _check(env, late, day2.replace(hour=15)) is None          # tries used up
    assert _check(env, late, day2 + dt.timedelta(days=1))["deg"] < 0.05


def test_running_total_when_the_reference_matches_too_little(env, scene, monkeypatch):
    _check(env, _capture(env, "20261102-120000", _view(scene)), NOON)
    _check(env, _capture(env, "20261103-120000", _view(scene, ry=0.4)), NOON + dt.timedelta(days=1))
    monkeypatch.setattr(cm, "REFERENCE_MIN_INLIERS", 10 ** 6)        # as if the scene had changed
    r = _check(env, _capture(env, "20261104-120000", _view(scene, ry=0.4, rx=0.3)),
               NOON + dt.timedelta(days=2))
    assert r["basis"] == cm.BASIS_CHAIN
    assert r["step_deg"] == pytest.approx(0.3, abs=0.03)
    assert r["deg"] == pytest.approx(0.5, abs=0.04)


def test_reset_adopts_a_new_reference(env, scene):
    _check(env, _capture(env, "20261102-120000", _view(scene)), NOON)
    cm.reset(env["state"])
    r = _check(env, _capture(env, "20261103-120000", _view(scene, ry=2)), NOON + dt.timedelta(days=1))
    assert r["basis"] == cm.BASIS_NEW_REFERENCE and r["deg"] == 0.0


def test_pose_monitor_reset_clears_the_camera_reference(tmp_path, monkeypatch):
    from tools import pose_monitor
    state = tmp_path / "camera_move"
    state.mkdir()
    (state / "reference.npz").write_bytes(b"x")
    monkeypatch.setattr(cm, "STATE_DIR", str(state))
    monkeypatch.setattr(pose_monitor, "STATE_FILE", str(tmp_path / "pose_state.json"))
    pose_monitor.reset(str(tmp_path / "other.json"))      # a given path: a test's, not the unit's
    assert state.exists()
    pose_monitor.reset()
    assert not state.exists()


def test_packet_encodings(tmp_path):
    from tools.lora_handler_concurrent import _encode_fields
    from tools.sensor_changes import changed_fields
    data = {"camera_rotation_hundredths": 1234, "camera_move_inliers": 255, "camera_move_basis": 1}
    assert cm.payload(data) == bytes.fromhex("04d2ff01")
    assert _encode_fields(data)["camera_rotation_hundredths"] == bytes.fromhex("011204d2ff01")
    # describes the day's check, not a change: never in the tracker
    assert changed_fields(data, state_path=str(tmp_path / "sent.json")) == {}


def test_bad_status_gives_no_fields(tmp_path):
    d = tmp_path / "c"
    d.mkdir()
    capture_status.mark(str(d), camera_move="reference", camera_move_result={"basis": 9, "deg": 1, "inliers": 3})
    assert cm.fields_for_capture(str(d)) == {}
    assert cm.fields_for_capture("") == {}
