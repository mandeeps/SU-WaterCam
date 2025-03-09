#!/usr/bin/env python

# Collect IMU data, return if called from geotag script, save to text file otherwise
# Apply offsets for sensor in flat orientation, Z axis up/down

import time
import board
import adafruit_mpu6050
from math import atan2, pi, sqrt

def get_values() -> dict:
    return {"Temperature":sensor.temperature, "Accelerometer":sensor.acceleration,
            "Gyro":sensor.gyro}

i2c = board.I2C() # uses board.SCL and board.SDA
# Default i2c address changed to work alongside WittyPi
sensor = adafruit_mpu6050.MPU6050(i2c, 0x69)
offset_accel = []
offset_gyro = []
data_file = "/home/pi/SU-WaterCam/data/imu_data.txt"
offset_file = "/home/pi/SU-WaterCam/data/imu_offsets.txt"
with open(offset_file) as file:
    # first 3 lines are accelerometer offsets
    for i in range(3):
        # read line, strip newline char,convert to float
        offset_accel.append(float(file.readline().rstrip()))
    # gyro offset lines
    for i in range(3):
        offset_gyro.append(float(file.readline().rstrip()))

def main():
    while True:
        # subtract the offset values from what the IMU measures
        accel = [x - y for x, y in zip(sensor.acceleration, offset_accel)]
        gyro = [x - y for x, y in zip(sensor.gyro, offset_gyro)]
        accel_record = "Acceleration: X: {}, Y: {}, Z: {} m/s^2".format(*accel)
        gyro_record = "Gyro X: {}, Y: {}, Z: {} degrees/s".format(*gyro)
        temp_record = "Temperature: %.2f C"%sensor.temperature

        print(accel_record)
        print(gyro_record)
        print(temp_record)
        print("")
        # Attempt to calculate Roll/Pitch/Yaw values
        accelX = accel[0]
        accelY = accel[1]
        accelZ = accel[2]
        print(f"Accel values X: {accelX} Y: {accelY} Z: {accelZ}")
            
        # atan2 returns radians, multiply by 180/pi to convert from radians to degrees
        #pitch = atan2(accelX, sqrt(accelY*accelY + accelZ*accelZ)) * (180/pi)
        pitch = round(atan2(accelX, accelZ) * (180/pi)) # 360 degree range
        #roll = atan2(accelY, sqrt(accelX*accelX + accelZ*accelZ)) * (180/pi)
        roll = round(atan2(accelY, accelZ) * (180/pi))

        # Yaw estimation - needs to be replaced with sensor fusion approach
        yaw = round(atan2(accelY, accelX) * (180/pi)) 

        if roll < 0:
            roll = roll + 360
        if pitch < 0:
           pitch = pitch + 360
        if yaw < 0:
            yaw = yaw + 360
        print(f"Roll {roll} Pitch {pitch} Yaw {yaw}") 
        
        with open(data_file, 'a') as data:
            data.writelines('\n'.join([accel_record, gyro_record, temp_record, '\n']))
        
        time.sleep(2)

if __name__ == __main__:
    main()
