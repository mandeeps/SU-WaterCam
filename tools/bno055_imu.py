#!/home/pi/SU-WaterCam/venv/bin/python
# BNO055 IMU
# Based on Adafruit example

import time
import logging
import board
import adafruit_bno055

i2c = board.I2C()
sensor = adafruit_bno055.BNO055_I2C(i2c)
last_val = 0xFFFF

def temperature() -> int:
    global last_val  # pylint: disable=global-statement
    result = sensor.temperature
    if abs(result - last_val) == 128:
        result = sensor.temperature
        if abs(result - last_val) == 128:
            return 0b00111111 & result
    last_val = result
    return result

def get_values() -> dict:
    return {"Temperature":sensor.temperature, "Accelerometer":sensor.acceleration,
        "Magnetic":sensor.magnetic, "Gyro":sensor.gyro, "Euler":sensor.euler,
        "Quaternion":sensor.quaternion, "Linear":sensor.linear_acceleration,
        "Gravity":sensor.gravity}

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
        logging.error("Offset file does not exist")

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
