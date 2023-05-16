#!/usr/bin/env python
# Collect 500 samples from MPU6050 while it is lying flat and not moving
# Create offsets for each axis on accelerometer and gyro by averaging values for each
# to get flat readings close to zero

import time
import board
import adafruit_mpu6050

i2c = board.I2C()  # uses board.SCL and board.SDA
# Default i2c address changed to work alongside WittyPi
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)

accel_x = 0
accel_y = 0
accel_z = 0
gyro_x = 0
gyro_y = 0
gyro_z = 0

for i in range(1000):
    accel = mpu.acceleration
    accel_x += accel[0]
    accel_y += accel[1]
    accel_z += accel[2]
    print(f"Acceleration: X: {accel[0]:.2f}, Y: {accel[1]:.2f}, Z: {accel[2]:.2f} m/s^2")
    
    gyro = mpu.gyro
    gyro_x += gyro[0]
    gyro_y += gyro[1]
    gyro_z += gyro[2]
    print("Gyro X:%.2f, Y: %.2f, Z: %.2f degrees/s"%(gyro))
    print("Temperature: %.2f C"%mpu.temperature)
    print("")
    time.sleep(0.33)

accel_x = accel_x / 500
accel_y = accel_y / 500
accel_z = accel_z / 500
gyro_x = gyro_x / 500
gyro_y = gyro_y / 500
gyro_z = gyro_z / 500

print(f"Accel X offset: {accel_x: .2f}")
print(f"Accel Y offset: {accel_y: .2f}")
print(f"Accel Z offset: {accel_z: .2f}")
print(f"Gyro X offset: {gyro_x: .2f}")
print(f"Gyro Y offset: {gyro_y: .2f}")
print(f"Gyro Z offset: {gyro_z: .2f}")
