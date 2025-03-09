#!/usr/bin/env python
# Collect 1000 samples from MPU6050 while it is lying flat and not moving
# Create offsets for each axis on gyro by averaging values
# to get flat readings close to zero
# Offset for accelerometer should set X/Y close to 0 and Z close to 9.8 m/s^2
# Save offsets to imu_offsets.txt

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

num = 1000
for i in range(num):
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

accel_x = accel_x / num
accel_y = accel_y / num
accel_z = (accel_z / num) - 9.81
gyro_x = gyro_x / num
gyro_y = gyro_y / num
gyro_z = gyro_z / num

print(f"Accel X offset: {accel_x: .2f}")
print(f"Accel Y offset: {accel_y: .2f}")
print(f"Accel Z offset: {accel_z: .2f}")
print(f"Gyro X offset: {gyro_x: .2f}")
print(f"Gyro Y offset: {gyro_y: .2f}")
print(f"Gyro Z offset: {gyro_z: .2f}")

with open('/home/pi/SU-WaterCam/data/imu_offsets.txt', 'w') as file:
    file.writelines('\n'.join([f"{accel_x:2f}", f"{accel_y:2f}", f"{accel_z:2f}", f"{gyro_x:2f}", f"{gyro_y:2f}", f"{gyro_z:2f}"]))
