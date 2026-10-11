"""The registration orientation check flags a turned node and never touches the transform."""
import json
import math

import pytest

from tools import registration_orientation as ro

TRANSFORM = {"transform_type": "AffineTransform", "parameters": [1, 0, 0, 1, -30.0, 4.0],
             "fixed_parameters": [0, 0], "metadata": {"transform_type": "affine"}}
G = 9.81


def quat(axis, deg):
    """Unit quaternion (w, x, y, z) for a rotation of deg about axis."""
    n = math.sqrt(sum(a * a for a in axis))
    s = math.sin(math.radians(deg) / 2)
    return (math.cos(math.radians(deg) / 2),) + tuple(a / n * s for a in axis)


def gravity_tilted(deg):
    """Gravity seen by a sensor tilted by deg about x, from (0, -G, 0)."""
    r = math.radians(deg)
    return (0.0, -G * math.cos(r), G * math.sin(r))


def pose(q=(1.0, 0.0, 0.0, 0.0), g=(0.0, -G, 0.0), mag=3):
    return {"quaternion": q, "gravity": g, "euler": (0.0, 7.0, 85.0),
            "calibration_status": (3, 3, 3, mag)}


@pytest.fixture
def images(tmp_path):
    (tmp_path / ro.CACHE_FILENAME).write_text(json.dumps(TRANSFORM))
    capture = tmp_path / "20261011-120000"
    capture.mkdir()
    return tmp_path, str(capture)


def load(images_dir):
    return json.loads((images_dir / ro.CACHE_FILENAME).read_text())


@pytest.mark.parametrize("value", [None, {}, {"quaternion": (1, 0, 0, 0)},
                                   pose(q=(0, 0, 0, 0)), pose(g=(0.0, 0.0, 0.0)),
                                   pose(q=(None, 0, 0, 0)), pose(g=(1.0, 2.0)),
                                   pose(q=(True, 0, 0, 0))])
def test_unusable_readings(value, images):
    _, capture = images
    assert ro.pose_from_reading(value) is None
    assert ro.check(capture, value)["status"] == "no_imu"


def test_no_cache(tmp_path):
    capture = tmp_path / "20261011-120000"
    capture.mkdir()
    assert ro.check(str(capture), pose())["status"] == "no_cache"


def test_measures():
    assert ro.tilt_deg((0, -G, 0), gravity_tilted(30)) == pytest.approx(30)
    assert ro.rotation_deg(quat((0, 0, 1), 0), quat((0, 0, 1), 40)) == pytest.approx(40)
    # q and -q are the same orientation
    q = quat((1, 2, 3), 25)
    assert ro.rotation_deg(q, tuple(-x for x in q)) == pytest.approx(0, abs=1e-4)
    # the Euler gimbal-lock case: near 90° pitch, a 2° tilt is still 2°
    near_lock = quat((1, 0, 0), 89)
    assert ro.rotation_deg(near_lock, quat((1, 0, 0), 91)) == pytest.approx(2)


def test_first_check_adopts_and_keeps_the_transform(images):
    images_dir, capture = images
    assert ro.check(capture, pose())["status"] == "adopted"
    cache = load(images_dir)
    ref = cache["metadata"]["orientation"]
    assert ref["quaternion"] == [1.0, 0.0, 0.0, 0.0]
    assert ref["mag_calibrated"] is True
    assert "recorded_at" in ref
    assert cache["parameters"] == TRANSFORM["parameters"]
    assert cache["metadata"]["transform_type"] == "affine"


def test_without_adopt_nothing_is_written(images):
    images_dir, capture = images
    assert ro.check(capture, pose(), adopt=False)["status"] == "no_reference"
    assert load(images_dir) == TRANSFORM


@pytest.mark.parametrize("now, status", [
    (pose(q=quat((1, 0, 0), 2), g=gravity_tilted(2)), "ok"),         # small wobble
    (pose(q=quat((1, 0, 0), 6), g=gravity_tilted(6)), "shifted"),    # tilt past 5
    (pose(q=quat((0, -1, 0), 10)), "ok"),                            # turned 10, under 15
    (pose(q=quat((0, -1, 0), 20)), "shifted"),                       # turned 20
    (pose(q=quat((0, -1, 0), 20), mag=1), "ok"),                     # heading not trusted now
])
def test_thresholds(images, now, status):
    _, capture = images
    ro.check(capture, pose())
    assert ro.check(capture, now)["status"] == status


def test_turn_is_ignored_when_the_reference_had_no_magnetometer(images):
    _, capture = images
    ro.check(capture, pose(mag=0))
    result = ro.check(capture, pose(q=quat((0, -1, 0), 40)))
    assert result["status"] == "ok"
    assert result["heading_reliable"] is False
    assert "not counted" in ro.describe(result)


def test_thresholds_are_configurable(images):
    _, capture = images
    ro.check(capture, pose())
    now = pose(q=quat((1, 0, 0), 3), g=gravity_tilted(3))
    assert ro.check(capture, now, tilt_threshold_deg=2.0)["status"] == "shifted"


def test_a_new_cache_without_a_reference_is_adopted_again(images):
    """Re-seeding or a fresh solve writes a cache with no reference; that clears a flag."""
    images_dir, capture = images
    ro.check(capture, pose())
    (images_dir / ro.CACHE_FILENAME).write_text(json.dumps(TRANSFORM))
    turned = pose(q=quat((1, 0, 0), 30), g=gravity_tilted(30))
    assert ro.check(capture, turned)["status"] == "adopted"
    assert ro.check(capture, turned)["status"] == "ok"


def test_reset_replaces_the_reference(images):
    images_dir, capture = images
    ro.check(capture, pose())
    tilted = pose(q=quat((1, 0, 0), 8), g=gravity_tilted(8))
    assert ro.check(capture, tilted)["status"] == "shifted"
    assert ro.reset(capture, tilted)["status"] == "reset"
    assert ro.check(capture, tilted)["status"] == "ok"
    assert load(images_dir)["parameters"] == TRANSFORM["parameters"]


def test_damaged_reference_is_replaced(images):
    images_dir, capture = images
    cache = dict(TRANSFORM, metadata={"transform_type": "affine", "orientation": {"gravity": "x"}})
    (images_dir / ro.CACHE_FILENAME).write_text(json.dumps(cache))
    assert ro.check(capture, pose())["status"] == "adopted"


def test_cache_in_the_capture_directory_is_used_when_the_parent_has_none(tmp_path):
    capture = tmp_path / "20261011-120000"
    capture.mkdir()
    (capture / ro.CACHE_FILENAME).write_text(json.dumps(TRANSFORM))
    assert ro.cache_path(str(capture)) == str(capture / ro.CACHE_FILENAME)


def test_capture_flag_file(images):
    _, capture = images
    ro.check(capture, pose())
    result = ro.check(capture, pose(q=quat((1, 0, 0), 9), g=gravity_tilted(9)))
    ro.write_capture_flag(capture, result)
    with open(f"{capture}/{ro.CAPTURE_FLAG_FILENAME}") as f:
        saved = json.load(f)
    assert saved["status"] == "shifted"
    assert saved["change_deg"]["tilt"] == pytest.approx(9, abs=0.01)
    assert "turned" in ro.describe(result)


def test_cache_filename_matches_coreg():
    pytest.importorskip("SimpleITK")
    pytest.importorskip("pandas")
    from tools.coreg_multiple import config
    assert ro.CACHE_FILENAME == config.TRANSFORM_CACHE_FILENAME


# --- sending the status: channel 01 08 ---------------------------------------

@pytest.mark.parametrize("status, code", [("ok", 0), ("shifted", 1), ("adopted", 2),
                                          ("no_cache", 3), ("no_imu", 4)])
def test_status_code_for_capture(tmp_path, status, code):
    ro.write_capture_flag(str(tmp_path), {"status": status})
    assert ro.status_code_for_capture(str(tmp_path)) == code


@pytest.mark.parametrize("content", [None, "not json", '["a list"]', '{"status": "no_reference"}'])
def test_no_status_code_without_a_usable_check(tmp_path, content):
    if content is not None:
        (tmp_path / ro.CAPTURE_FLAG_FILENAME).write_text(content)
    assert ro.status_code_for_capture(str(tmp_path)) is None


def test_status_is_encoded_after_pi_throttled():
    from tools.lora_handler_concurrent import _encode_compressed_packet
    packet = _encode_compressed_packet({"timestamp": 1790727396, "battery_percent": 44,
                                        "pi_throttled": 0x50005, "registration_status": 1})
    assert packet[-6:] == bytes([0x01, 0x07, 0x55, 0x01, 0x08, 0x01])
    assert _encode_compressed_packet({"battery_percent": 44, "registration_status": None}) \
        == bytes([0x02, 0x01, 44])


@pytest.mark.parametrize("capture_dir", [None, ""])
def test_no_status_code_without_a_capture_directory(capture_dir):
    assert ro.status_code_for_capture(capture_dir) is None
