#!/home/pi/SU-WaterCam/venv/bin/python
# BNO055 IMU
# Based on Adafruit example

import json
import logging
import math
import os
import struct
import time
from pathlib import Path

_REPO_ROOT = Path(os.environ.get("WATERCAM_REPO", str(Path(__file__).resolve().parent.parent)))

# Calibration file produced by tools/bno055_calibration.py.
# Override path with the WATERCAM_CALIB_FILE environment variable if needed.
_CALIB_FILE = Path(os.environ.get(
    "WATERCAM_CALIB_FILE",
    str(_REPO_ROOT / "bno055_calibration.json"),
))

logger = logging.getLogger(__name__)

try:
    import board
except Exception:
    board = None

try:
    import adafruit_bno055
except ImportError:
    print("Error: BNO055 import")
    adafruit_bno055 = None

_sensor = None
last_val = 0xFFFF


def _apply_calibration(sensor) -> bool:
    """
    Load saved BNO055 calibration offsets and write them to the sensor.

    Offsets are read from bno055_calibration.json (produced by
    tools/bno055_calibration.py). The sensor must be
    in CONFIG mode to accept offset writes; this function handles the mode
    switch and restores NDOF mode on exit.

    Returns True if offsets were applied, False if the file is missing or
    loading fails (sensor continues to run uncalibrated in that case).
    """
    if not _CALIB_FILE.exists():
        logger.warning("BNO055: no calibration file at %s — running uncalibrated", _CALIB_FILE)
        return False

    try:
        with open(_CALIB_FILE) as f:
            data = json.load(f)

        raw = bytes(data["offsets_bytes"])
        if len(raw) != 22:
            raise ValueError(f"Expected 22 offset bytes, got {len(raw)}")

        # Byte layout matches BNO055 register block 0x55-0x6A:
        # accel XYZ (6), mag XYZ (6), gyro XYZ (6), accel radius (2), mag radius (2)
        accel   = struct.unpack_from('<hhh', raw,  0)
        mag     = struct.unpack_from('<hhh', raw,  6)
        gyro    = struct.unpack_from('<hhh', raw, 12)
        accel_r = struct.unpack_from('<h',   raw, 18)[0]
        mag_r   = struct.unpack_from('<h',   raw, 20)[0]

        prev_mode = sensor.mode
        sensor.mode = 0x00   # CONFIG_MODE — required to write offsets
        time.sleep(0.025)    # 19 ms transition per datasheet

        sensor.offsets_accelerometer = accel
        sensor.offsets_magnetometer  = mag
        sensor.offsets_gyroscope     = gyro
        sensor.radius_accelerometer  = accel_r
        sensor.radius_magnetometer   = mag_r

        sensor.mode = prev_mode
        time.sleep(0.012)    # 7 ms transition back to fusion mode

        logger.info(
            "BNO055: calibration loaded (node=%s, saved=%s)",
            data.get("node_id", "?"), data.get("timestamp", "?"),
        )
        return True

    except Exception as exc:
        logger.warning("BNO055: failed to load calibration from %s: %s", _CALIB_FILE, exc)
        return False


def _get_sensor():
    global _sensor
    if _sensor is not None:
        return _sensor
    try:
        if board is None or adafruit_bno055 is None:
            return None
        i2c = board.I2C()
        _sensor = adafruit_bno055.BNO055_I2C(i2c)
        _apply_calibration(_sensor)
        # Warm-up: allow fusion to initialize
        try:
            import time as _t
            for _ in range(20):  # ~2s max
                e = getattr(_sensor, 'euler', None)
                if isinstance(e, tuple) and any(v not in (None, 0.0) for v in e):
                    break
                _t.sleep(0.1)
            else:
                e = getattr(_sensor, 'euler', None)
                if not (isinstance(e, tuple) and any(v not in (None, 0.0) for v in e)):
                    logger.warning("BNO055 warmup timed out — fusion not yet initialised")
        except Exception:
            pass
        return _sensor
    except Exception:
        return None

def temperature() -> int:
    global last_val  # pylint: disable=global-statement
    sensor = _get_sensor()
    if sensor is None:
        return 0
    result = sensor.temperature
    if abs(result - last_val) == 128:
        result = sensor.temperature
        if abs(result - last_val) == 128:
            return 0b00111111 & result
    last_val = result
    return result

def get_values() -> dict:
    """Return raw sensor readings. Euler heading is in sensor frame (no mount
    offset or declination applied). Corrections are owned by the Georeferencing
    pipeline via the unit config JSON."""
    sensor = _get_sensor()
    if sensor is None:
        return {}
    return {"Temperature": sensor.temperature,
            "Accelerometer": sensor.acceleration,
            "Magnetic": sensor.magnetic,
            "Gyro": sensor.gyro,
            "Euler": sensor.euler,
            "Quaternion": sensor.quaternion,
            "Linear": sensor.linear_acceleration,
            "Gravity": sensor.gravity}

def get_euler_stable(n: int = 20, interval_s: float = 0.015) -> dict:
    """Return circular-mean heading and mean pitch/roll averaged over n samples.

    Collecting 20 samples at 15 ms intervals adds ~300 ms per call.
    Returns keys: heading_mean, pitch_mean, roll_mean, heading_std, n_samples.
    Returns {} if the sensor is unavailable or all samples are None.
    """
    sensor = _get_sensor()
    if sensor is None:
        return {}

    headings, rolls, pitches = [], [], []
    for _ in range(n):
        e = sensor.euler
        if isinstance(e, tuple) and len(e) == 3 and e[0] is not None:
            headings.append(e[0])
            if e[1] is not None:
                rolls.append(e[1])    # Adafruit euler[1] = roll
            if e[2] is not None:
                pitches.append(e[2])  # Adafruit euler[2] = pitch
        time.sleep(interval_s)

    if not headings:
        return {}

    sin_sum = sum(math.sin(math.radians(h)) for h in headings)
    cos_sum = sum(math.cos(math.radians(h)) for h in headings)
    mean_heading = (math.degrees(math.atan2(sin_sum, cos_sum)) + 360.0) % 360.0

    # Circular std dev (Mardia & Jupp): R is the mean resultant length
    R = math.sqrt(sin_sum ** 2 + cos_sum ** 2) / len(headings)
    R = min(max(R, 1e-9), 1.0 - 1e-9)
    heading_std = math.degrees(math.sqrt(-2.0 * math.log(R)))

    return {
        "heading_mean": round(mean_heading, 2),
        "pitch_mean":   round(sum(pitches) / len(pitches), 2) if pitches else None,
        "roll_mean":    round(sum(rolls)   / len(rolls),   2) if rolls   else None,
        "heading_std":  round(heading_std, 2),
        "n_samples":    len(headings),
    }


def get_orientation():
    sensor = _get_sensor()
    if sensor is None:
        return {}
    # Try to ensure non-zero data if possible
    e = sensor.euler
    if not (isinstance(e, tuple) and any(v not in (None, 0.0) for v in e)):
        try:
            import time as _t
            for _ in range(20):  # ~2s max
                e = sensor.euler
                if isinstance(e, tuple) and any(v not in (None, 0.0) for v in e):
                    break
                _t.sleep(0.1)
            else:
                if not (isinstance(e, tuple) and any(v not in (None, 0.0) for v in e)):
                    logger.warning("BNO055 get_orientation: data still zero after retry")
        except Exception:
            pass
    return {"tilt_roll_yaw": e}


def main():
    sensor = _get_sensor()
    while True:
        time.sleep(2)
        print(f"Temperature: {sensor.temperature} degrees C")
        print(f"Accelerometer (m/s^2): {sensor.acceleration}")
        print(f"Magnetometer (microteslas): {sensor.magnetic}")
        print(f"Gyroscope (rad/sec): {sensor.gyro}")
        print(f"Euler angle: {sensor.euler}")
        print(f"Quaternion: {sensor.quaternion}")
        print(f"Linear acceleration (m/s^2): {sensor.linear_acceleration}")
        print(f"Gravity (m/s^2): {sensor.gravity} \n")

if __name__ == "__main__":
    main()
