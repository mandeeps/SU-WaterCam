#!/usr/bin/env python
# Adafruit MPU-6050 example
# Apply offsets for sensor in flat orientation, Z axis up/down

import time
import board
import adafruit_mpu6050

i2c = board.I2C() # uses board.SCL and board.SDA
# Default i2c address changed to work alongside WittyPi
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)
offset_accel = (-0.49, 0.17, -8.82) # (-0.13, -0.05, -8.81) # based on measurements taken 5/16/23 
offset_gyro = (0.07, 0.03, 0.01)

while True:
    accel = [sum(x) for x in zip(mpu.acceleration, offset_accel)]
    gyro = [sum(x) for x in zip(mpu.gyro, offset_gyro)]

    print("Acceleration: X: {}, Y: {}, Z: {} m/s^2".format(*accel))
    print("Gyro X: {}, Y: {}, Z: {} degrees/s".format(*gyro))
    print("Temperature: %.2f C"%mpu.temperature)
    print("")
    time.sleep(0.33)
