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
