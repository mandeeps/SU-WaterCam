#!/home/pi/SU-WaterCam/venv/bin/python
# BNO085 IMU
# Based on Adafruit example

import os
import time
from pathlib import Path
import board

_REPO_ROOT = Path(os.environ.get("WATERCAM_REPO", str(Path(__file__).resolve().parent.parent)))
import busio
import adafruit_bno08x
from adafruit_bno08x.i2c import BNO08X_I2C

i2c = busio.I2C(board.SCL, board.SDA, frequency=800_000) 
bno = BNO08X_I2C(i2c)

bno.enable_feature(adafruit_bno08x.BNO_REPORT_ACCELEROMETER)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_GYROSCOPE)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_MAGNETOMETER)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_ROTATION_VECTOR)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_LINEAR_ACCELERATION)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_ROTATION_VECTOR)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_GEOMAGNETIC_ROTATION_VECTOR)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_GAME_ROTATION_VECTOR)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_STEP_COUNTER)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_STABILITY_CLASSIFIER)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_ACTIVITY_CLASSIFIER)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_SHAKE_DETECTOR)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_RAW_ACCELEROMETER)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_RAW_GYROSCOPE)
bno.enable_feature(adafruit_bno08x.BNO_REPORT_RAW_MAGNETOMETER)

last_val = 0xFFFF

def temperature() -> int:
    global last_val  # pylint: disable=global-statement
    result = bno.temperature
    if abs(result - last_val) == 128:
        result = bno.temperature
        if abs(result - last_val) == 128:
            return 0b00111111 & result
    last_val = result
    return result

def get_values() -> dict:
    return {"Temperature":bno.temperature, "Accelerometer":bno.acceleration,
        "Magnetic":bno.magnetic, "Gyro":bno.gyro, "Euler":bno.euler,
        "Quaternion":bno.quaternion, "Linear":bno.linear_acceleration,
        "Gravity":bno.gravity}

def offset():
    offset_accel = []
    offset_gyro = []
    OFFSET_PATH = str(_REPO_ROOT / "data" / "imu_offsets.txt")

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
        #print(f"Temperature: {bno.temperature} degrees C")
        print(f"Accelerometer (m/s^2): {bno.acceleration}")
        print(f"Magnetometer (microteslas): {bno.magnetic}")
        print(f"Gyroscope (rad/sec): {bno.gyro}")
#        print(f"Euler angle: {bno.euler}")
        print(f"Quaternion: {bno.quaternion}")
        x,y,z = bno.linear_acceleration

        print(f"Linear acceleration (m/s^2): {x, y, z}")

#       print(f"Gravity (m/s^2): {bno.gravity} \n")
        status = bno.stability_classification
        print(f"Stability: {status}")

        time.sleep(0.5)
        if bno.shake:
            print("Shake")

if __name__ == "__main__":
    main()
