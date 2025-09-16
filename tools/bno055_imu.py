#!/home/pi/SU-WaterCam/venv/bin/python
# BNO055 IMU
# Based on Adafruit example

import time

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


def _get_sensor():
    global _sensor
    if _sensor is not None:
        return _sensor
    try:
        if board is None or adafruit_bno055 is None:
            return None
        i2c = board.I2C()
        _sensor = adafruit_bno055.BNO055_I2C(i2c)
        # Warm-up: allow fusion to initialize
        try:
            import time as _t
            for _ in range(20):  # ~2s max
                e = getattr(_sensor, 'euler', None)
                if isinstance(e, tuple) and any(v not in (None, 0.0) for v in e):
                    break
                _t.sleep(0.1)
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
        except Exception:
            pass
    return {"tilt_roll_yaw": e}

def offset():
    offset_accel = []
    offset_gyro = []
    OFFSET_PATH = "/home/pi/SU-WaterCam/data/imu_offsets.txt"

    try:
        with open(OFFSET_PATH) as file:
            # first 3 lines are accelerometer offsets, iterate through them
            for i in range(3):
                # read the line, strip out newline char, convert to float
                offset_accel.append(float(file.readline().rstrip()))
            # now do the same for the gyro offset lines
            for i in range(3):
                offset_gyro.append(float(file.readline().rstrip()))
    except IOError:
        print("Offset file does not exist")

def main():
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
