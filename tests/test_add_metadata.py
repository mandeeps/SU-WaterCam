"""Tests for add_metadata.add_metadata() EXIF/XMP tagging pipeline."""
from unittest.mock import MagicMock

import pytest

# Skip entire module when piexif or Pillow are absent (not installed in this env)
pytest.importorskip("piexif", reason="piexif required for EXIF/XMP tests")
pytest.importorskip("PIL", reason="Pillow required to create a test JPEG")

import piexif
import piexif.helper
from PIL import Image


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_jpeg(tmp_path):
    """Minimal valid JPEG with empty EXIF sections."""
    img = Image.new("RGB", (4, 4))
    path = str(tmp_path / "test.jpg")
    exif_bytes = piexif.dump({"0th": {}, "Exif": {}, "GPS": {}, "1st": {}})
    img.save(path, "JPEG", exif=exif_bytes)
    return path


@pytest.fixture(autouse=True)
def _patch_deps(tmp_path, monkeypatch):
    """Redirect the metadata log, stub GPS, device_id, and IMU for every test."""
    import tools.add_metadata as am
    import tools.bno055_imu as imu

    # Point log file at a writable tmp location
    monkeypatch.setattr(am, "_METADATA_LOG", str(tmp_path / "metadata_log.txt"))

    # GPS: no packet, no formatted data (avoid writing to DATA on GPS errors)
    monkeypatch.setattr(am, "get_location_with_retry", lambda: (None, None),
                        raising=False)
    monkeypatch.setattr(am, "get_loc", lambda: [], raising=False)

    # Device ID: empty by default (overridden per-test as needed)
    monkeypatch.setattr(am, "_read_device_id", lambda: "")

    # IMU: sensor unavailable by default
    monkeypatch.setattr(imu, "get_values", lambda: {})
    monkeypatch.setattr(imu, "get_euler_stable", lambda **kw: {})
    monkeypatch.setattr(imu, "_get_sensor", lambda: None)
    monkeypatch.setattr(imu, "get_pose", lambda: {})


@pytest.fixture
def xmp_capture(monkeypatch):
    """Intercept XMPFiles/XMPMeta and return the mock xmp object for inspection."""
    import tools.add_metadata as am

    xmp_instance = MagicMock()
    xmpfiles_instance = MagicMock()
    xmpfiles_instance.get_xmp.return_value = xmp_instance

    monkeypatch.setattr(am, "XMPFiles", MagicMock(return_value=xmpfiles_instance))
    monkeypatch.setattr(am, "XMPMeta", MagicMock())
    return xmp_instance


def _xmp_props(xmp_instance) -> dict:
    """Return {prop_name: value} from all set_property calls on xmp_instance."""
    return {c.args[1]: c.args[2] for c in xmp_instance.set_property.call_args_list}


def _enable_imu(monkeypatch, heading=70.0, roll=2.0, pitch=-1.0, std=0.3,
                sys_cal=3, gyro=3, accel=2, mag=3):
    """Configure IMU mocks to return a complete, stable orientation."""
    import tools.bno055_imu as imu
    monkeypatch.setattr(imu, "get_values", lambda: {"Temperature": 25})
    monkeypatch.setattr(imu, "get_euler_stable", lambda **kw: {
        "heading_mean": heading, "roll_mean": roll, "pitch_mean": pitch,
        "heading_std": std, "n_samples": 20,
    })
    mock_sensor = MagicMock()
    mock_sensor.calibration_status = (sys_cal, gyro, accel, mag)
    monkeypatch.setattr(imu, "_get_sensor", lambda: mock_sensor)


# ── XMP orientation ───────────────────────────────────────────────────────────

class TestXMPOrientation:
    def test_yaw_roll_pitch_written(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert props["Yaw"] == "70.0"
        assert props["Roll"] == "2.0"
        assert props["Pitch"] == "-1.0"

    def test_heading_raw_sensor_frame_tag(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        assert _xmp_props(xmp_capture).get("HeadingRawSensorFrame") == "true"

    def test_heading_std_dev_written(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch, std=0.42)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        assert _xmp_props(xmp_capture).get("HeadingStdDev") == "0.42"

    def test_calib_quality_written(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch, sys_cal=3, gyro=3, accel=2, mag=3)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert props["CalibSys"] == "3"
        assert props["CalibGyro"] == "3"
        assert props["CalibAccel"] == "2"
        assert props["CalibMag"] == "3"

    def test_orientation_absent_when_imu_unavailable(self, tmp_jpeg, xmp_capture):
        # _patch_deps leaves IMU returning {} — no Yaw/Roll/Pitch should appear
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert "Yaw" not in props
        assert "Roll" not in props
        assert "Pitch" not in props

    def test_orientation_absent_when_pitch_is_none(self, tmp_jpeg, xmp_capture,
                                                    monkeypatch):
        """Guard must block XMP write when any component is None."""
        import tools.bno055_imu as imu
        monkeypatch.setattr(imu, "get_values", lambda: {"Temperature": 25})
        # pitch_mean is None — the all-or-nothing guard should prevent the write
        monkeypatch.setattr(imu, "get_euler_stable", lambda **kw: {
            "heading_mean": 70.0, "roll_mean": 2.0, "pitch_mean": None,
            "heading_std": 0.3, "n_samples": 15,
        })
        mock_sensor = MagicMock()
        mock_sensor.calibration_status = (3, 3, 2, 3)
        monkeypatch.setattr(imu, "_get_sensor", lambda: mock_sensor)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert "Yaw" not in props
        assert "Roll" not in props
        assert "Pitch" not in props


# ── EXIF UserComment ──────────────────────────────────────────────────────────

class TestEXIFUserComment:
    def test_user_comment_contains_all_fields(self, tmp_jpeg, xmp_capture,
                                               monkeypatch):
        _enable_imu(monkeypatch, heading=70.0, roll=2.0, pitch=-1.0, std=0.3)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        exif = piexif.load(tmp_jpeg)
        raw = exif["Exif"].get(piexif.ExifIFD.UserComment)
        assert raw is not None, "UserComment tag missing from EXIF"
        comment = piexif.helper.UserComment.load(raw)
        assert "Yaw 70.0" in comment
        assert "Roll 2.0" in comment
        assert "Pitch -1.0" in comment
        assert "HeadingStd 0.3" in comment

    def test_user_comment_absent_when_no_imu(self, tmp_jpeg, xmp_capture):
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        exif = piexif.load(tmp_jpeg)
        assert piexif.ExifIFD.UserComment not in exif.get("Exif", {})


# ── Device ID ────────────────────────────────────────────────────────────────

class TestDeviceID:
    def test_device_id_written_to_xmp(self, tmp_jpeg, xmp_capture, monkeypatch):
        import tools.add_metadata as am
        monkeypatch.setattr(am, "_read_device_id", lambda: "UFO-006")
        am.add_metadata(tmp_jpeg)
        assert _xmp_props(xmp_capture).get("DeviceID") == "UFO-006"

    def test_device_id_written_to_exif_body_serial(self, tmp_jpeg, xmp_capture,
                                                     monkeypatch):
        import tools.add_metadata as am
        monkeypatch.setattr(am, "_read_device_id", lambda: "UFO-006")
        am.add_metadata(tmp_jpeg)
        exif = piexif.load(tmp_jpeg)
        raw = exif["Exif"].get(piexif.ExifIFD.BodySerialNumber)
        assert raw == b"UFO-006"

    def test_device_id_omitted_when_empty(self, tmp_jpeg, xmp_capture):
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        assert "DeviceID" not in _xmp_props(xmp_capture)


# ── quaternion, gravity and read time ────────────────────────────────────────

def _enable_pose(monkeypatch, q=(0.7, 0.1, -0.2, 0.68), g=(0.5, -9.7, 1.2)):
    import tools.bno055_imu as imu
    monkeypatch.setattr(imu, "get_pose", lambda: {
        "quaternion": q, "gravity": g, "euler": (70.0, 2.0, -1.0),
        "calibration_status": (3, 3, 2, 3)})


class TestPose:
    def test_quaternion_gravity_and_time_in_xmp(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch)
        _enable_pose(monkeypatch)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert [props[f"Quaternion{a}"] for a in "WXYZ"] == ["0.70000", "0.10000", "-0.20000", "0.68000"]
        assert [props[f"Gravity{a}"] for a in "XYZ"] == ["0.5000", "-9.7000", "1.2000"]
        assert props["PoseReadTime"][:2] == "20" and ("+" in props["PoseReadTime"] or "-" in props["PoseReadTime"][10:])
        assert props["CalibMag"] == "3"

    def test_user_comment_carries_them_after_the_old_fields(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch, mag=2)
        _enable_pose(monkeypatch)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        raw = piexif.load(tmp_jpeg)["Exif"][piexif.ExifIFD.UserComment]
        comment = piexif.helper.UserComment.load(raw)
        assert comment.startswith("Roll 2.0 Pitch -1.0 Yaw 70.0 HeadingStd 0.3")
        assert "CalibMag 2" in comment
        assert "Quaternion 0.70000,0.10000,-0.20000,0.68000" in comment
        assert "Gravity 0.5000,-9.7000,1.2000" in comment
        assert "PoseTime " in comment

    def test_no_pose_when_the_sensor_gives_none(self, tmp_jpeg, xmp_capture, monkeypatch):
        import tools.bno055_imu as imu
        _enable_imu(monkeypatch)
        monkeypatch.setattr(imu, "get_pose", lambda: {})
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert "QuaternionW" not in props and "GravityX" not in props and "PoseReadTime" not in props
        assert props["Yaw"] == "70.0"                      # the Euler angles are unaffected

    def test_incomplete_pose_is_not_written(self, tmp_jpeg, xmp_capture, monkeypatch):
        _enable_imu(monkeypatch)
        _enable_pose(monkeypatch, q=(0.7, None, 0.0, 0.0))
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        props = _xmp_props(xmp_capture)
        assert "QuaternionW" not in props and props["GravityX"] == "0.5000"


# ── GPS: live, cached, and never GPSTrack ────────────────────────────────────

class _Packet:
    lat, lon, alt, mode, track = 43.05, -76.15, 120.4, 3, 271.0


def _gps(monkeypatch, location, packet, source, age=None):
    import tools.add_metadata as am
    monkeypatch.setattr(am, "get_location_with_retry", lambda: (location, packet))
    monkeypatch.setattr(am, "last_fix", {"source": source, "age_s": age})


def _gps_ifd(path):
    return piexif.load(path)["GPS"]


class TestGPS:
    def test_live_fix_has_no_track(self, tmp_jpeg, xmp_capture, monkeypatch):
        _gps(monkeypatch, {"gps_lat": 43.05}, _Packet(), "live", 0.0)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        gps = _gps_ifd(tmp_jpeg)
        assert piexif.GPSIFD.GPSTrack not in gps and piexif.GPSIFD.GPSImgDirection not in gps
        assert piexif.GPSIFD.GPSLatitude in gps and gps[piexif.GPSIFD.GPSAltitude] == (120, 1)
        assert gps[piexif.GPSIFD.GPSProcessingMethod].endswith(b"GPS")
        assert _xmp_props(xmp_capture)["GPSFixSource"] == "live"

    def test_cached_fix_is_written_and_marked(self, tmp_jpeg, xmp_capture, monkeypatch):
        loc = {"gps_lat": 43.5, "gps_lon": -76.5, "gps_alt": 120.4}
        _gps(monkeypatch, loc, None, "cached", 7300.4)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        gps = _gps_ifd(tmp_jpeg)
        assert gps[piexif.GPSIFD.GPSProcessingMethod] == b"ASCII\x00\x00\x00CACHED"
        assert gps[piexif.GPSIFD.GPSLatitudeRef] == b"N" and gps[piexif.GPSIFD.GPSLongitudeRef] == b"W"
        assert gps[piexif.GPSIFD.GPSAltitude] == (120, 1) and piexif.GPSIFD.GPSTrack not in gps
        lat = gps[piexif.GPSIFD.GPSLatitude]
        assert lat[0] == (43, 1) and lat[1] == (30, 1)
        props = _xmp_props(xmp_capture)
        assert props["GPSFixSource"] == "cached" and props["GPSFixAgeSeconds"] == "7300"
        comment = piexif.helper.UserComment.load(piexif.load(tmp_jpeg)["Exif"][piexif.ExifIFD.UserComment])
        assert "GPSFix CACHED AgeS 7300" in comment

    def test_cached_fix_without_altitude(self, tmp_jpeg, xmp_capture, monkeypatch):
        _gps(monkeypatch, {"gps_lat": 43.05, "gps_lon": -76.15, "gps_alt": None}, None, "cached", 60.0)
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        assert piexif.GPSIFD.GPSAltitude not in _gps_ifd(tmp_jpeg)

    def test_no_fix_writes_no_gps(self, tmp_jpeg, xmp_capture, monkeypatch):
        _gps(monkeypatch, {}, None, "none")
        import tools.add_metadata as am
        am.add_metadata(tmp_jpeg)
        assert _gps_ifd(tmp_jpeg) == {}
        assert "GPSFixSource" not in _xmp_props(xmp_capture)
