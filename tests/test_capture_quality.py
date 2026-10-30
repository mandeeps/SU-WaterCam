"""Capture-quality fixes: thermal co-registration, a missing Lepton frame, and the
fallback capture directory."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")
sitk = pytest.importorskip("SimpleITK")
pytest.importorskip("pandas")
pytest.importorskip("rasterio")

from tools import capture_status  # noqa: E402
from tools import coreg_multiple as cm  # noqa: E402
from tools import registration_orientation as ro  # noqa: E402

NO_CALIBRATION = "/nonexistent/camera_calibration.json"


# --- the thermal frame is scaled, not wrapped -----------------------------------------------

def test_to_uint8_passes_uint8_through():
    img = np.arange(256, dtype=np.uint8).reshape(16, 16)
    assert cm.to_uint8(img) is img


def test_to_uint8_scales_a_wide_frame_monotonically():
    # tools/capture.c writes maxval = max - min: a span over 255 is uint16
    img = np.tile(np.linspace(0, 1000, 64).astype(np.uint16), (8, 1))
    out = cm.to_uint8(img)
    assert out.dtype == np.uint8
    assert np.all(np.diff(out[0].astype(int)) >= 0)           # wrapping mod 256 would not be
    assert out[0, 0] == 0 and out[0, -1] == 255


def test_to_uint8_ignores_one_hot_pixel():
    img = np.full((50, 50), 300, np.uint16)
    img[0, :] = 400
    img[10, 10] = 60000
    assert cm.to_uint8(img)[25, 25] < 255


def test_to_uint8_flat_frame():
    assert cm.to_uint8(np.full((4, 4), 7000, np.uint16)).std() == 0


def test_preprocessing_keeps_wide_thermal_contrast(monkeypatch):
    monkeypatch.setattr(cm.config, "ENABLE_PREPROCESSING", True)
    img = np.tile(np.linspace(0, 1000, 64).astype(np.uint16), (64, 1))
    out = cm.preprocess_image_for_registration(img, "thermal")
    # A wrapped frame has its brightest and darkest columns scrambled; a scaled one
    # still correlates with the ramp.
    assert np.corrcoef(out[32].astype(float), np.arange(64))[0, 1] > 0.9


# --- judging a fresh solve ------------------------------------------------------------------

def _shift(tx):
    return sitk.TranslationTransform(2, (float(tx), 0.0))


def test_drift_is_the_largest_point_displacement():
    assert cm.transform_drift_px(_shift(0), _shift(30), 100, 80) == pytest.approx(30)


def test_good_solve_near_the_cached_one_is_accepted():
    v = cm.judge_solve(-0.5, _shift(10), _shift(0), 100, 80)
    assert v["accepted"] and v["drift_px"] == 10


def test_metric_is_not_gated_by_default():
    assert cm.judge_solve(-0.002, _shift(0), None, 100, 80)["accepted"]


def test_weak_metric_is_rejected(monkeypatch):
    monkeypatch.setattr(cm.config, "MIN_ACCEPT_METRIC", {cm.config.REGISTRATION_METRIC: -0.10})
    v = cm.judge_solve(-0.02, _shift(0), None, 100, 80)
    assert not v["accepted"] and "metric" in v["reasons"][0]


def test_nan_metric_is_rejected():
    assert not cm.judge_solve(float("nan"), _shift(0), None, 100, 80)["accepted"]


def test_far_solve_is_rejected_even_with_a_good_metric():
    v = cm.judge_solve(-0.9, _shift(40), _shift(0), 100, 80)
    assert not v["accepted"] and "cached transform" in v["reasons"][0]


def test_drift_check_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(cm.config, "MAX_TRANSFORM_DRIFT_PX", None)
    assert cm.judge_solve(-0.9, _shift(400), _shift(0), 100, 80)["accepted"]


# --- the registration itself caches only what it accepts ------------------------------------

def _scene(tmp_path):
    """A capture directory with a textured optical frame and a shifted 16-bit thermal one."""
    rng = np.random.RandomState(3)
    base = cv2.GaussianBlur(rng.rand(96, 96).astype(np.float32), (0, 0), 4)
    base = (255 * (base - base.min()) / (base.max() - base.min())).astype(np.uint8)
    images = tmp_path / "images"
    capture = images / "20261020-120000"
    capture.mkdir(parents=True)
    fixed, moving = str(capture / "a-NIR-OFF.jpg"), str(capture / "lepton.pgm")
    cv2.imwrite(fixed, cv2.cvtColor(base, cv2.COLOR_GRAY2BGR))
    cv2.imwrite(moving, (np.roll(base, 3, axis=1).astype(np.uint16) * 6))
    return images, str(capture), fixed, moving


@pytest.fixture
def small(monkeypatch):
    monkeypatch.setattr(cm.config, "SAVE_TRANSFORM_PARAMETERS", True)
    monkeypatch.setattr(cm.config, "FORCE_RECALCULATE_TRANSFORM", False)
    monkeypatch.setattr(cm.config, "MAX_ITERATIONS", 5)
    monkeypatch.setattr(cm.config, "TRANSFORM_TYPE", "affine")
    monkeypatch.setattr(cm.config, "REGISTRATION_METRIC", "mutual_information")
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(1)


def test_a_rejected_first_solve_is_not_cached(small, tmp_path, monkeypatch):
    images, capture, fixed, moving = _scene(tmp_path)
    monkeypatch.setattr(cm.config, "MIN_ACCEPT_METRIC", {"mutual_information": -1e9})
    cm.mutual_information_registration(fixed, moving, capture, False, None, NO_CALIBRATION)
    assert not (images / cm.config.TRANSFORM_CACHE_FILENAME).exists()
    assert cm.last_solve["accepted"] is False and cm.last_solve["used"] == "rejected_solve"


def test_an_accepted_solve_is_cached_with_its_metric(small, tmp_path, monkeypatch):
    images, capture, fixed, moving = _scene(tmp_path)
    monkeypatch.setattr(cm.config, "MIN_ACCEPT_METRIC", {"mutual_information": 1e9})
    cm.mutual_information_registration(fixed, moving, capture, False, None, NO_CALIBRATION)
    cache = json.loads((images / cm.config.TRANSFORM_CACHE_FILENAME).read_text())
    assert cache["metadata"]["metric_value"] == pytest.approx(cm.last_solve["metric"])
    assert cm.last_solve["accepted"] is True


def test_a_rejected_solve_leaves_the_cache_alone_and_uses_it(small, tmp_path, monkeypatch):
    images, capture, fixed, moving = _scene(tmp_path)
    monkeypatch.setattr(cm.config, "MIN_ACCEPT_METRIC", {"mutual_information": 1e9})
    cm.mutual_information_registration(fixed, moving, capture, False, None, NO_CALIBRATION)
    path = images / cm.config.TRANSFORM_CACHE_FILENAME
    before = path.read_bytes()
    tfm_before = (images / cm.config.TRANSFORM_FILE_FILENAME).read_bytes()

    monkeypatch.setattr(cm.config, "MIN_ACCEPT_METRIC", {"mutual_information": -1e9})
    transform, _, _ = cm.mutual_information_registration(fixed, moving, capture, True, None, NO_CALIBRATION)
    assert path.read_bytes() == before                       # shared cache untouched
    assert (images / cm.config.TRANSFORM_FILE_FILENAME).read_bytes() == tfm_before
    assert cm.last_solve["used"] == "previous_cached"
    assert not cm.last_solve["accepted"]


def test_a_far_solve_is_rejected_for_drift_not_cached(small, tmp_path, monkeypatch):
    images, capture, fixed, moving = _scene(tmp_path)
    seeded = sitk.AffineTransform(2)
    seeded.SetTranslation((60.0, 0.0))                       # far from anything the solve finds
    assert cm.save_transform_parameters(seeded, capture, {"transform_type": "affine", "image_size": [96, 96]})
    before = (images / cm.config.TRANSFORM_CACHE_FILENAME).read_bytes()
    monkeypatch.setattr(cm.config, "MIN_ACCEPT_METRIC", {"mutual_information": 1e9})
    cm.mutual_information_registration(fixed, moving, capture, True, None, NO_CALIBRATION)
    assert (images / cm.config.TRANSFORM_CACHE_FILENAME).read_bytes() == before
    assert "cached transform" in cm.last_solve["reasons"][0]
    assert cm.last_solve["used"] == "previous_cached"


# --- a rejected solve is never "adopted" ----------------------------------------------------

def _pose():
    return {"quaternion": (1.0, 0.0, 0.0, 0.0), "gravity": (0.0, -9.81, 0.0),
            "euler": (0.0, 7.0, 85.0), "calibration_status": (3, 3, 3, 3)}


def _cache(tmp_path):
    images = tmp_path / "images"
    capture = images / "20261020-120000"
    capture.mkdir(parents=True)
    (images / ro.CACHE_FILENAME).write_text(json.dumps(
        {"transform_type": "AffineTransform", "parameters": [1, 0, 0, 1, 0, 0],
         "fixed_parameters": [0, 0], "metadata": {}}))
    return images, capture


def _record_solve(capture, accepted):
    (capture / "coregistration_metadata.json").write_text(json.dumps(
        {"image_info": {"registration_solve": {"accepted": accepted}}}))


def test_rejected_solve_is_not_reported_as_adopted(tmp_path):
    images, capture = _cache(tmp_path)
    _record_solve(capture, False)
    before = (images / ro.CACHE_FILENAME).read_bytes()
    result = ro.check(str(capture), _pose())
    assert result["status"] == "no_reference" and result["solve_rejected"] is True
    assert ro.STATUS_CODES.get(result["status"]) is None     # nothing is sent to the server
    assert (images / ro.CACHE_FILENAME).read_bytes() == before


def test_accepted_solve_is_adopted_as_before(tmp_path):
    images, capture = _cache(tmp_path)
    _record_solve(capture, True)
    assert ro.check(str(capture), _pose())["status"] == "adopted"


# --- undistortion applies to the NIR band too -----------------------------------------------

def _calibration(tmp_path, size=(64, 48)):
    w, h = size
    path = tmp_path / "cal.json"
    path.write_text(json.dumps({"K": [[50, 0, w / 2], [0, 50, h / 2], [0, 0, 1]],
                                "D": [-0.3, 0.1, 0, 0, 0], "img_size": [w, h]}))
    return str(path)


def _nir_pair(tmp_path):
    rng = np.random.RandomState(1)
    off = rng.randint(0, 120, (48, 64, 3), dtype=np.uint8)
    on = np.clip(off.astype(int) + rng.randint(0, 100, (48, 64, 3)), 0, 255).astype(np.uint8)
    p_on, p_off = str(tmp_path / "x-NIR-ON.jpg"), str(tmp_path / "x-NIR-OFF.jpg")
    cv2.imwrite(p_on, on, [cv2.IMWRITE_JPEG_QUALITY, 100])
    cv2.imwrite(p_off, off, [cv2.IMWRITE_JPEG_QUALITY, 100])
    return p_on, p_off


def test_nir_band_is_undistorted_like_the_registered_frame(tmp_path):
    p_on, p_off = _nir_pair(tmp_path)
    cal = _calibration(tmp_path)
    plain = cm.extract_nir_band(p_on, p_off, (64, 48), NO_CALIBRATION)
    band = cm.extract_nir_band(p_on, p_off, (64, 48), cal)
    assert not np.array_equal(plain, band)
    on = cm._undistort_if_calibrated(cv2.imread(p_on), cal)
    off = cm._undistort_if_calibrated(cv2.imread(p_off), cal)
    expected = cv2.subtract(cv2.cvtColor(on, cv2.COLOR_BGR2RGB)[:, :, 0],
                            cv2.cvtColor(off, cv2.COLOR_BGR2RGB)[:, :, 0])
    assert np.array_equal(band, expected)


def test_reported_intrinsics_are_the_new_camera_matrix(tmp_path):
    cal = _calibration(tmp_path)
    k = np.array(json.load(open(cal))["K"], dtype=np.float64)
    d = np.array(json.load(open(cal))["D"], dtype=np.float64).reshape(-1, 1)
    want, _ = cv2.getOptimalNewCameraMatrix(k, d, (64, 48), 1, (64, 48))
    got = cm.undistorted_camera_matrix(cal, 64, 48)
    assert np.allclose(got, want) and not np.allclose(got, k)
    assert cm.undistorted_camera_matrix(NO_CALIBRATION, 64, 48) is None


# --- no thermal frame -----------------------------------------------------------------------

def test_coreg_without_a_thermal_frame_builds_rgb_and_nir_bands(tmp_path):
    import rasterio
    p_on, p_off = _nir_pair(tmp_path)          # *-NIR-ON/OFF.jpg only: no .pgm
    cm.coreg(str(tmp_path), False, NO_CALIBRATION)
    with rasterio.open(tmp_path / cm.config.MODEL_INPUT_TIFF) as src:
        assert src.count == 5
        assert not src.read(4).any()                         # thermal placeholder
        assert src.read(5).any()                             # NIR is real
        assert src.tags()["BAND_ORDER"] == "red,green,blue,thermal,nir"
    meta = json.loads((tmp_path / "coregistration_metadata.json").read_text())
    assert meta["image_info"]["lwir"] is None
    assert capture_status.thermal_missing(str(tmp_path))


def test_a_missing_pgm_still_raises_by_default(tmp_path):
    _nir_pair(tmp_path)
    with pytest.raises(ValueError, match="pgm"):
        cm.validate_input_files(str(tmp_path))


# --- flir: one retry, then say so -----------------------------------------------------------

def _flir_env(tmp_path):
    root = tmp_path / "node"
    (root / "tools").mkdir(parents=True)
    (root / "images").mkdir()
    (root / "capture").write_text("")
    (root / "lepton").write_text("")
    (root / "tools" / "lepton_reset.py").write_text("")
    return root, str(root / "images" / "20261020-120000")


def test_flir_retries_the_capture_once_after_a_failure(tmp_path):
    import subprocess
    from tt_take_photos import flir
    root, directory = _flir_env(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        name = os.path.basename(command[0])
        calls.append(name)
        if name == "capture" and calls.count("capture") == 1:
            raise subprocess.TimeoutExpired(command, 20)
        if name == "capture":
            Path(directory, "IMG_0001.pgm").write_text("P2")
        return type("R", (), {"returncode": 0})()

    with patch("subprocess.run", side_effect=fake_run):
        assert flir.__wrapped__(directory) is True
    assert calls.count("capture") == 2
    assert any(f.startswith("lepton_") and f.endswith(".pgm") for f in os.listdir(directory))
    assert not capture_status.thermal_missing(directory)


def test_flir_marks_the_capture_when_the_retry_fails_too(tmp_path):
    import subprocess
    from tt_take_photos import flir
    root, directory = _flir_env(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(os.path.basename(command[0]))
        raise subprocess.TimeoutExpired(command, 20)

    with patch("subprocess.run", side_effect=fake_run):
        assert flir.__wrapped__(directory) is True
    assert calls.count("capture") == 2 and "lepton" not in calls
    assert capture_status.read(directory) == {"thermal": "missing"}


# --- segmentation without thermal -----------------------------------------------------------

@pytest.fixture
def tm():
    import ticktalk_main
    return ticktalk_main


def _capture_with_tiff(tmp_path, thermal_missing=True):
    from tools.coreg_multiple import config
    (tmp_path / config.MODEL_INPUT_TIFF).write_bytes(b"tiff")
    if thermal_missing:
        capture_status.mark(str(tmp_path), thermal="missing")
    return str(tmp_path)


def _socket_present(monkeypatch):
    monkeypatch.setattr("os.path.exists",
                        lambda p, _real=os.path.exists: True if p.endswith(".sock") else _real(p))


def test_no_thermal_uses_the_rgb_only_model_through_the_daemon(tm, tmp_path, monkeypatch):
    d = _capture_with_tiff(tmp_path)
    _socket_present(monkeypatch)
    with patch("tools.segformer_client.segformer_via_daemon", return_value=True) as daemon, \
         patch("subprocess.run") as run:
        out = tm.segformer.__wrapped__(d, True)
    assert out and out.endswith("color_preserved_5_band_segmentation.png")
    assert daemon.call_args.kwargs["rgb_only"] is True
    run.assert_not_called()
    assert capture_status.read(d)["segmentation"] == "rgb_only"


def test_no_thermal_and_no_rgb_only_model_skips_and_says_so(tm, tmp_path, monkeypatch):
    d = _capture_with_tiff(tmp_path)
    _socket_present(monkeypatch)
    with patch("tools.segformer_client.segformer_via_daemon", return_value=False), \
         patch("subprocess.run") as run:
        assert tm.segformer.__wrapped__(d, True) is None
    run.assert_not_called()                              # the cold-load fallback can't know either
    assert capture_status.read(d)["segmentation"] == "skipped_no_thermal"


def test_no_thermal_and_no_daemon_skips(tm, tmp_path, monkeypatch):
    d = _capture_with_tiff(tmp_path)
    monkeypatch.setattr("os.path.exists",
                        lambda p, _real=os.path.exists: False if p.endswith(".sock") else _real(p))
    with patch("subprocess.run") as run:
        assert tm.segformer.__wrapped__(d, True) is None
    run.assert_not_called()
    assert capture_status.read(d)["segmentation"] == "skipped_no_thermal"


def test_with_thermal_the_daemon_is_not_asked_for_rgb_only(tm, tmp_path, monkeypatch):
    d = _capture_with_tiff(tmp_path, thermal_missing=False)
    _socket_present(monkeypatch)
    with patch("tools.segformer_client.segformer_via_daemon", return_value=True) as daemon:
        tm.segformer.__wrapped__(d, True)
    assert daemon.call_args.kwargs["rgb_only"] is False
    assert "segmentation" not in capture_status.read(d)


def test_no_daemon_skips_without_the_conda_fallback(tm, tmp_path, monkeypatch):
    d = _capture_with_tiff(tmp_path, thermal_missing=False)
    monkeypatch.setattr("os.path.exists",
                        lambda p, _real=os.path.exists: False if p.endswith(".sock") else _real(p))
    with patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
        assert tm.segformer.__wrapped__(d, True) is None
    run.assert_not_called()
    popen.assert_not_called()
    assert capture_status.read(d)["segmentation"] == "skipped_no_daemon"


def test_daemon_failure_skips_without_the_conda_fallback(tm, tmp_path, monkeypatch):
    d = _capture_with_tiff(tmp_path, thermal_missing=False)
    _socket_present(monkeypatch)
    with patch("tools.segformer_client.segformer_via_daemon", return_value=False), \
         patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
        assert tm.segformer.__wrapped__(d, True) is None
    run.assert_not_called()
    popen.assert_not_called()
    assert capture_status.read(d)["segmentation"] == "daemon_failed"


def test_client_sends_rgb_only_only_when_asked(tmp_path):
    import socket
    import threading
    from tools.segformer_client import segformer_via_daemon
    sock_path = str(tmp_path / "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(2)
    seen = []

    def serve():
        for _ in range(2):
            c, _ = srv.accept()
            seen.append(json.loads(c.recv(4096).split(b"\n")[0]))
            c.sendall(b'{"status": "ok"}\n')
            c.close()
    t = threading.Thread(target=serve)
    t.start()
    assert segformer_via_daemon("/a.tiff", "/b.png", sock_path)
    assert segformer_via_daemon("/a.tiff", "/b.png", sock_path, rgb_only=True)
    t.join(5)
    srv.close()
    assert "rgb_only" not in seen[0] and seen[1]["rgb_only"] is True


def test_daemon_refuses_a_model_that_needs_thermal_for_rgb_only(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import segformer_daemon as daemon

    descriptions = ("Red Channel (Optical)", "Green Channel (Optical)", "Blue Channel (Optical)",
                    "Thermal Data (Normalized 0-255)", "NIR Band (NIR-ON minus NIR-OFF)")
    path = tmp_path / "t.tiff"
    with rasterio.open(path, "w", driver="GTiff", height=4, width=6, count=5, dtype="uint8") as dst:
        dst.write(np.zeros((5, 4, 6), np.uint8))
        for i, desc in enumerate(descriptions, 1):
            dst.set_band_description(i, desc)
        dst.update_tags(BAND_ORDER="red,green,blue,thermal,nir")

    class Boom(Exception):
        pass

    class Session:
        def __init__(self, bands):
            self.meta = {"bands": bands}

        def get_inputs(self):
            return [type("I", (), {"name": "x", "shape": [1, 3, 4, 6]})()]

        def get_modelmeta(self):
            return type("M", (), {"custom_metadata_map": self.meta})()

        def run(self, *a, **k):
            raise Boom

    with pytest.raises(ValueError, match="thermal"):
        daemon.run_inference(Session("red,green,blue,thermal,nir"), str(path), str(tmp_path / "o.png"), rgb_only=True)
    with pytest.raises(ValueError, match="thermal"):
        daemon.run_inference(Session("red,green,nir"), str(path), str(tmp_path / "o.png"), rgb_only=True)
    with pytest.raises(Boom):                                  # got past the check: an RGB model
        daemon.run_inference(Session("red,green,blue"), str(path), str(tmp_path / "o.png"), rgb_only=True)


# --- the fallback capture directory ---------------------------------------------------------

def _plain_get_time():
    """get_time without its STREAMify wrapper, which keeps no __wrapped__."""
    import ast
    src = (Path(__file__).resolve().parent.parent / "ticktalk_main.py").read_text()
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "get_time")
    node.decorator_list = []
    namespace = {}
    exec(compile(ast.Module([node], []), "ticktalk_main.get_time", "exec"), namespace)
    return namespace["get_time"]


def test_get_time_fallback_is_fresh_each_time(tmp_path, monkeypatch):
    monkeypatch.setenv("WATERCAM_REPO", str(tmp_path))
    get_time = _plain_get_time()
    import time
    with patch("tools.capture_time.write_capture_time", side_effect=[OSError("disk"), None, OSError("disk"), None]):
        a = get_time(None)
        time.sleep(1.1)
        b = get_time(None)
    assert a != b
    for d in (a, b):
        assert d.endswith("-fallback") and os.path.isdir(d) and os.listdir(d) == []
