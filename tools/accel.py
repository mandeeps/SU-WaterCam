#!/usr/bin/env python
# Adafruit MPU-6050 example

import time
import board
import adafruit_mpu6050

i2c = board.I2C()  # uses board.SCL and board.SDA
# Default i2c address changed to work alongside WittyPi
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)

while True:
    print("Acceleration: X:%.2f, Y: %.2f, Z: %.2f m/s^2"%(mpu.acceleration))
    print("Gyro X:%.2f, Y: %.2f, Z: %.2f degrees/s"%(mpu.gyro))
    print("Temperature: %.2f C"%mpu.temperature)
    print("")
    time.sleep(0.33)
