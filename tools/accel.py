#!/usr/bin/env python
# Adafruit MPU-6050 example
# Apply offsets for sensor in flat orientation, Z axis up/down
import time
import board
import adafruit_mpu6050

i2c = board.I2C() # uses board.SCL and board.SDA
# Default i2c address changed to work alongside WittyPi
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)
offset_accel = []
offset_gyro = []
datapath = "/home/pi/SU-WaterCam/data/accel_data.txt"
filepath = "/home/pi/SU-WaterCam/data/imu_offsets.txt"
with open(filepath) as file:
    # first 3 lines are accelerometer offsets
    for i in range(3):
        # read line, strip newline char,convert to float
        offset_accel.append(float(file.readline().rstrip()))
    # gyro offset lines
    for i in range(3):
        offset_gyro.append(float(file.readline().rstrip()))

while True:
    # subtract the offset values from what the IMU measures
    accel = [x - y for x, y in zip(mpu.acceleration, offset_accel)]
    gyro = [x - y for x, y in zip(mpu.gyro, offset_gyro)]
    accel_record = "Acceleration: X: {}, Y: {}, Z: {} m/s^2".format(*accel)
    gyro_record = "Gyro X: {}, Y: {}, Z: {} degrees/s".format(*gyro)
    temp_record = "Temperature: %.2f C"%mpu.temperature

    print(accel_record)
    print(gyro_record)
    print(temp_record)
    print("")
    
    with open(datapath, 'a') as data:
        data.writelines('\n'.join([accel_record, gyro_record, temp_record, '\n']))
    
    time.sleep(0.33)
