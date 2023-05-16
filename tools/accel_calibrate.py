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

n = 1000
for i in range(n):
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

accel_x = accel_x / n
accel_y = accel_y / n
accel_z = accel_z / n
gyro_x = gyro_x / n
gyro_y = gyro_y / n
gyro_z = gyro_z / n

print(f"Accel X offset: {accel_x: .2f}")
print(f"Accel Y offset: {accel_y: .2f}")
print(f"Accel Z offset: {accel_z: .2f}")
print(f"Gyro X offset: {gyro_x: .2f}")
print(f"Gyro Y offset: {gyro_y: .2f}")
print(f"Gyro Z offset: {gyro_z: .2f}")
