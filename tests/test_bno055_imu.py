"""Tests for bno055_imu.get_euler_stable(): circular mean, edge cases."""
import math


class _FakeSensor:
    """Sensor stub whose euler property cycles through a fixed list of readings."""
    def __init__(self, readings):
        self._readings = readings
        self._idx = 0

    @property
    def euler(self):
        val = self._readings[self._idx % len(self._readings)]
        self._idx += 1
        return val


# ── autouse fixture: suppress time.sleep so tests finish instantly ────────────

import pytest


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    import tools.bno055_imu as imu
    monkeypatch.setattr(imu.time, "sleep", lambda s: None)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_returns_empty_when_sensor_unavailable(monkeypatch):
    import tools.bno055_imu as imu
    monkeypatch.setattr(imu, "_get_sensor", lambda: None)
    assert imu.get_euler_stable() == {}


def test_returns_empty_when_all_euler_none(monkeypatch):
    import tools.bno055_imu as imu
    sensor = _FakeSensor([(None, None, None)] * 20)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    assert imu.get_euler_stable() == {}


def test_circular_mean_basic(monkeypatch):
    import tools.bno055_imu as imu
    # Adafruit euler tuple: (heading, roll, pitch)
    sensor = _FakeSensor([(90.0, 1.0, -2.0)] * 20)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    result = imu.get_euler_stable(n=20)
    assert abs(result["heading_mean"] - 90.0) < 0.01
    assert abs(result["roll_mean"] - 1.0) < 0.01     # euler[1] = roll
    assert abs(result["pitch_mean"] - (-2.0)) < 0.01  # euler[2] = pitch
    assert result["n_samples"] == 20


def test_circular_mean_wraps_at_north(monkeypatch):
    """Alternating 359°/1° should average to ~0°, not 180° (arithmetic trap)."""
    import tools.bno055_imu as imu
    readings = [(359.0, 0.0, 0.0), (1.0, 0.0, 0.0)] * 10
    sensor = _FakeSensor(readings)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    result = imu.get_euler_stable(n=20)
    h = result["heading_mean"]
    # 0° and 360° are both valid representations of north
    assert h < 1.0 or h > 359.0, f"Expected near 0°, got {h}°"


def test_low_std_dev_for_identical_readings(monkeypatch):
    import tools.bno055_imu as imu
    sensor = _FakeSensor([(45.0, 0.0, 0.0)] * 20)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    result = imu.get_euler_stable(n=20)
    assert result["heading_std"] < 0.1


def test_higher_std_dev_for_spread_readings(monkeypatch):
    import tools.bno055_imu as imu
    # 60° and 120° alternate — circular std should be noticeably above zero
    readings = [(60.0, 0.0, 0.0), (120.0, 0.0, 0.0)] * 10
    sensor = _FakeSensor(readings)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    result = imu.get_euler_stable(n=20)
    assert result["heading_std"] > 5.0


def test_pitch_roll_none_when_euler_components_none(monkeypatch):
    import tools.bno055_imu as imu
    # heading valid but roll and pitch are None in every sample
    sensor = _FakeSensor([(90.0, None, None)] * 20)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    result = imu.get_euler_stable(n=20)
    assert result["heading_mean"] is not None
    assert result["roll_mean"] is None
    assert result["pitch_mean"] is None


def test_n_samples_reflects_valid_heading_count(monkeypatch):
    """Only readings with a non-None heading contribute to n_samples."""
    import tools.bno055_imu as imu
    readings = [(90.0, 0.0, 0.0)] * 10 + [(None, None, None)] * 10
    sensor = _FakeSensor(readings)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    result = imu.get_euler_stable(n=20)
    assert result["n_samples"] == 10


def test_sleep_called_once_per_sample(monkeypatch):
    import tools.bno055_imu as imu
    sleep_calls = []
    sensor = _FakeSensor([(90.0, 0.0, 0.0)] * 5)
    monkeypatch.setattr(imu, "_get_sensor", lambda: sensor)
    monkeypatch.setattr(imu.time, "sleep", lambda s: sleep_calls.append(s))
    imu.get_euler_stable(n=5, interval_s=0.015)
    assert len(sleep_calls) == 5
    assert all(abs(s - 0.015) < 1e-9 for s in sleep_calls)


# ═════════════════════════════════════════════════════════════════════════
# _get_sensor() warm-up: waits for calibration_status to reconfirm mag,
# not just non-zero euler output.
#
# Regression: writing saved offset registers does not instantly restore
# calibration_status to its saved value -- the BNO055's own fusion algorithm
# re-earns that confidence over a short window of live operation. The old
# warm-up only waited for non-zero euler output, so the very first photo
# captured right after boot could catch the sensor mid-settle and trigger
# add_metadata.py's "magnetometer uncalibrated" warning even with valid,
# fully-calibrated (mag=3) saved offsets -- confirmed happening on UFO010.
# ═════════════════════════════════════════════════════════════════════════

class _FakeFusionSensor:
    """Sensor stub for _get_sensor()'s warm-up loop.

    euler becomes non-zero after `euler_settle_calls` reads, and
    calibration_status's mag value climbs to `final_mag` after
    `mag_settle_calls` reads -- simulating the sensor needing a few
    live-operation ticks before either settles, independently of each other.
    """
    def __init__(self, euler_settle_calls=0, mag_settle_calls=0, final_mag=3):
        self._reads = 0
        self._euler_settle_calls = euler_settle_calls
        self._mag_settle_calls = mag_settle_calls
        self._final_mag = final_mag
        self.mode = 0x0C  # NDOF, arbitrary non-zero "previous mode"
        self.offsets_accelerometer = None
        self.offsets_magnetometer = None
        self.offsets_gyroscope = None
        self.radius_accelerometer = None
        self.radius_magnetometer = None

    @property
    def euler(self):
        self._reads += 1
        settled = self._reads > self._euler_settle_calls
        return (90.0, 0.0, 0.0) if settled else (0.0, 0.0, 0.0)

    @property
    def calibration_status(self):
        settled = self._reads > self._mag_settle_calls
        mag = self._final_mag if settled else 0
        return (0, 3, 3, mag)


class _FakeClock:
    """Deterministic time.time()/time.sleep() so warm-up tests run instantly."""
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def _write_valid_calib_file(path):
    import json
    path.write_text(json.dumps({
        "node_id": "test",
        "timestamp": "2026-01-01T00:00:00",
        "offsets_bytes": [0] * 22,
    }))


@pytest.fixture(autouse=True)
def _reset_sensor_singleton():
    import tools.bno055_imu as imu
    imu._sensor = None
    yield
    imu._sensor = None


def _patch_hardware(monkeypatch, imu, fake_sensor):
    monkeypatch.setattr(imu.board, "I2C", lambda: object())
    monkeypatch.setattr(imu.adafruit_bno055, "BNO055_I2C", lambda i2c: fake_sensor, raising=False)


def test_get_sensor_waits_for_mag_reconfirmation_when_offsets_loaded(tmp_path, monkeypatch):
    import tools.bno055_imu as imu
    _write_valid_calib_file(tmp_path / "calib.json")
    monkeypatch.setattr(imu, "_CALIB_FILE", tmp_path / "calib.json")
    clock = _FakeClock()
    monkeypatch.setattr(imu.time, "time", clock.time)
    monkeypatch.setattr(imu.time, "sleep", clock.sleep)

    # mag only reaches 3 after a few reads -- warm-up must wait for it.
    sensor = _FakeFusionSensor(euler_settle_calls=3, mag_settle_calls=3, final_mag=3)
    _patch_hardware(monkeypatch, imu, sensor)

    result = imu._get_sensor()

    assert result is sensor
    assert sensor.calibration_status[3] == 3


def test_get_sensor_warns_when_mag_never_reconfirms(tmp_path, monkeypatch, caplog):
    import logging
    import tools.bno055_imu as imu
    _write_valid_calib_file(tmp_path / "calib.json")
    monkeypatch.setattr(imu, "_CALIB_FILE", tmp_path / "calib.json")
    clock = _FakeClock()
    monkeypatch.setattr(imu.time, "time", clock.time)
    monkeypatch.setattr(imu.time, "sleep", clock.sleep)

    # euler settles quickly (fusion is fine), but mag never reaches >= 2
    # within the 5s warm-up window.
    sensor = _FakeFusionSensor(euler_settle_calls=0, mag_settle_calls=10_000, final_mag=0)
    _patch_hardware(monkeypatch, imu, sensor)

    with caplog.at_level(logging.WARNING):
        imu._get_sensor()

    assert any("magnetometer calibration status has not reconfirmed" in r.message
               for r in caplog.records)


def test_get_sensor_no_calibration_file_uses_short_timeout_and_old_warning(tmp_path, monkeypatch, caplog):
    import logging
    import tools.bno055_imu as imu
    monkeypatch.setattr(imu, "_CALIB_FILE", tmp_path / "does-not-exist.json")
    clock = _FakeClock()
    monkeypatch.setattr(imu.time, "time", clock.time)
    monkeypatch.setattr(imu.time, "sleep", clock.sleep)

    # No calibration was loaded, and fusion never settles here either --
    # warm-up should time out at 2s, not 5s, on the original
    # "fusion not yet initialised" warning.
    sensor = _FakeFusionSensor(euler_settle_calls=10_000, mag_settle_calls=10_000, final_mag=0)
    _patch_hardware(monkeypatch, imu, sensor)

    with caplog.at_level(logging.WARNING):
        imu._get_sensor()

    assert clock.now < 3.0  # short (2s) timeout, not the 5s calibrated-path one
    assert any("fusion not yet initialised" in r.message for r in caplog.records)
