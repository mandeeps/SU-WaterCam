"""Calibrating the BNO055 in parts (--stages): gyro and accel indoors, the
magnetometer later outdoors, without bench-learned magnetometer offsets ever
reaching the saved file or the sensor."""
import json
import sys
import types

import pytest

try:
    import smbus2  # noqa: F401
except ImportError:                       # the tool imports it at module level
    sys.modules["smbus2"] = types.SimpleNamespace(SMBus=lambda *a, **k: None)

import tools.bno055_calibration as cal

ACC, MAG, GYR = slice(0, 6), slice(6, 12), slice(12, 18)
ACC_R, MAG_R = slice(18, 20), slice(20, 22)


def _block(acc, mag, gyr, acc_r, mag_r):
    return bytes([acc] * 6 + [mag] * 6 + [gyr] * 6 + [acc_r] * 2 + [mag_r] * 2)


class FakeIMU:
    def __init__(self, chip: bytes, status=None):
        self.chip = chip
        self.written = None
        self.status = status or {"system": 3, "gyro": 3, "accel": 2, "mag": 3}

    def calibration_status(self):
        return dict(self.status)

    def read_offsets(self):
        return self.chip

    def write_offsets(self, offsets):
        self.written = offsets
        self.chip = offsets


def test_merge_takes_each_part_from_the_right_source():
    a = _block(1, 1, 1, 1, 1)
    b = _block(2, 2, 2, 2, 2)
    out = cal.merge_offsets(a, b, {"gyro"}, {"accel"})
    assert out[GYR] == a[GYR]
    assert out[ACC] == b[ACC] and out[ACC_R] == b[ACC_R]
    assert out[MAG] == bytes(6) and out[MAG_R] == bytes(2)      # neither: zeros


def test_old_files_count_as_fully_calibrated():
    assert cal.calibrated_parts({"offsets_bytes": [0] * 22}) == set(cal.STAGES)
    assert cal.calibrated_parts({"components": {"gyro": {"x": 1}, "accel": None, "mag": None}}) == {"gyro"}


def test_lab_then_outdoor_calibration(tmp_path):
    path = str(tmp_path / "bno055_calibration.json")
    # Indoors: gyro + accel. The chip has also learned some (bench) mag offsets.
    lab = FakeIMU(_block(10, 99, 30, 40, 98))
    cal.save_calibration(lab, path, "UFO-006", -11.5, ("gyro", "accel"))
    data = json.load(open(path))
    saved = bytes(data["offsets_bytes"])
    assert saved[GYR] == bytes([30] * 6) and saved[ACC] == bytes([10] * 6) and saved[ACC_R] == bytes([40] * 2)
    assert saved[MAG] == bytes(6) and saved[MAG_R] == bytes(2), "bench magnetometer offsets must not be saved"
    assert data["components"]["mag"] is None and data["components"]["gyro"]["status"] == 3

    # Outdoors: mag only. The chip's gyro/accel drifted; the file's must be kept.
    field = FakeIMU(_block(11, 55, 31, 41, 56))
    cal.save_calibration(field, path, "UFO-006", -11.5, ("mag",))
    data = json.load(open(path))
    saved = bytes(data["offsets_bytes"])
    assert saved[MAG] == bytes([55] * 6) and saved[MAG_R] == bytes([56] * 2)
    assert saved[GYR] == bytes([30] * 6) and saved[ACC] == bytes([10] * 6)
    assert all(data["components"][p] for p in cal.STAGES)
    assert data["components"]["gyro"] != data["components"]["mag"]   # earlier timestamp kept


def test_loading_a_partial_file_leaves_the_chips_magnetometer(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"node_id": "n", "timestamp": "2026-10-17T12:00:00",
                                "components": {"gyro": {"a": 1}, "accel": {"a": 1}, "mag": None},
                                "offsets_bytes": list(_block(10, 0, 30, 40, 0))}))
    imu = FakeIMU(_block(1, 77, 3, 4, 78))
    cal.load_calibration(imu, str(path))
    assert imu.written[MAG] == bytes([77] * 6) and imu.written[MAG_R] == bytes([78] * 2)
    assert imu.written[GYR] == bytes([30] * 6) and imu.written[ACC] == bytes([10] * 6)


def test_loading_an_old_full_file_writes_everything(tmp_path):
    path = tmp_path / "c.json"
    full = _block(10, 20, 30, 40, 50)
    path.write_text(json.dumps({"node_id": "n", "timestamp": "2026-10-17T12:00:00",
                                "offsets_bytes": list(full)}))
    imu = FakeIMU(_block(1, 2, 3, 4, 5))
    cal.load_calibration(imu, str(path))
    assert imu.written == full


class _AdafruitLike:
    mode = 0x0C

    def __init__(self):
        self.set = {}

    def __setattr__(self, k, v):
        if k != "set" and k != "mode":
            self.set[k] = v
        object.__setattr__(self, k, v)


def test_boot_loader_skips_an_uncalibrated_magnetometer(tmp_path, monkeypatch):
    import tools.bno055_imu as imu
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"node_id": "n", "timestamp": "2026-10-17T12:00:00",
                                "components": {"gyro": {"a": 1}, "accel": {"a": 1}, "mag": None},
                                "offsets_bytes": list(_block(10, 0, 30, 40, 0))}))
    monkeypatch.setattr(imu, "_CALIB_FILE", path)
    monkeypatch.setattr(imu.time, "sleep", lambda s: None)
    sensor = _AdafruitLike()
    assert imu._apply_calibration(sensor) is True
    assert "offsets_magnetometer" not in sensor.set and "radius_magnetometer" not in sensor.set
    assert {"offsets_accelerometer", "radius_accelerometer", "offsets_gyroscope"} <= set(sensor.set)


def test_boot_loader_writes_all_parts_of_an_old_file(tmp_path, monkeypatch):
    import tools.bno055_imu as imu
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"node_id": "n", "timestamp": "2026-10-17T12:00:00",
                                "offsets_bytes": list(_block(10, 20, 30, 40, 50))}))
    monkeypatch.setattr(imu, "_CALIB_FILE", path)
    monkeypatch.setattr(imu.time, "sleep", lambda s: None)
    sensor = _AdafruitLike()
    assert imu._apply_calibration(sensor) is True
    assert {"offsets_magnetometer", "radius_magnetometer", "offsets_accelerometer", "offsets_gyroscope"} <= set(sensor.set)
