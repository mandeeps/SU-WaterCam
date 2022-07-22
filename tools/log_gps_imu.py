#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul  7 12:51:56 2022

@author: manu
"""

# Log GPS and IMU data to a CSV file for use by exiftool to geotag images
# Based on Adafruit simple gps script and imu script
# https://exiftool.org/geotag.html#CSVFormat

import time
import board
import adafruit_gps
import adafruit_mpu6050
import csv

i2c = board.I2C()
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)
gps = adafruit_gps.GPS_GtopI2C(i2c, debug=False)  # Use I2C interface
gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
gps.send_command(b"PMTK220,1000")

last_print = time.monotonic()

# open gps log file
LOG_FILE = '/home/pi/SU-WaterCam/data/track.log'
LOG_MODE = 'a'
# raw data record without formatting
DATA = '/home/pi/SU-WaterCam/data/gps.txt'

log = open(LOG_FILE, LOG_MODE)
data = open(DATA, LOG_MODE)

write = csv.writer(log)
    
# Last 4 from IMU, first several for GPS geotagging by exiftool
fields = ['GPSDateTime', 'GPSLatitude', 'GPSLongitude', 'GPSAltitude',
          'GPSTrack', 'IMU Time', 'IMU Accel', 'IMU Gyro', 'IMU Temp']
write.writerow(fields)

while True:
    gps.update()
    current = time.monotonic()
    if current - last_print >= 5.0:
        last_print = current

        # log IMU data
        imu = ["\nTime:{} \n".format(time.asctime(time.localtime(time.time()))),
        "Acceleration: X:{0[0]}, Y:{0[1]}, Z:{0[2]} m/s^2 \n".format(mpu.acceleration),
        "Gyro X:{0[0]}, Y:{0[1]}, Z:{0[2]} degrees/s \n".format(mpu.gyro),
        "Temperature: {} C".format(mpu.temperature),"\n"]
        
        for line in imu:
            data.writelines(line)
        
        # GPS track.log file is only written to when we have GPS data
        if gps.has_fix:
            timestamp = [
              "Fix timestamp: {}/{}/{} {:02}:{:02}:{:02}".format(
              gps.timestamp_utc.tm_mon,  # Grab parts of the time from the
              gps.timestamp_utc.tm_mday,  # struct_time object that holds
              gps.timestamp_utc.tm_year,  # the fix time.  Note you might
              gps.timestamp_utc.tm_hour,  # not get all data like year, day,
              gps.timestamp_utc.tm_min,  # month!
              gps.timestamp_utc.tm_sec
              ), "\n"
            ]

            coords = ["Latitude: {0:.3f} degrees".format(gps.latitude),
              "Longitude: {0:.3f} degrees".format(gps.longitude),
              #"Precise Latitude: {:2.}{:2.4f} degrees".format(
              #gps.latitude_degrees, gps.latitude_minutes
              #),
              #"Precise Longitude: {:2.}{:2.4f} degrees".format(
              #gps.longitude_degrees, gps.longitude_minutes
              #), 
              "\n"]

            extra = []
            if gps.satellites is not None:
                extra.append("# satellites: {}".format(gps.satellites))
            if gps.altitude_m is not None:
                extra.append("Altitude: {} meters".format(gps.altitude_m))
            if gps.speed_knots is not None:
                extra.append("Speed: {} knots".format(gps.speed_knots))
            if gps.track_angle_deg is not None:
                extra.append("Track angle: {} degrees".format(gps.track_angle_deg))
            if gps.horizontal_dilution is not None:
                extra.append("Horizontal dilution: {}".format(gps.horizontal_dilution))
            if gps.height_geoid is not None:
                extra.append("Height geoid: {} meters".format(gps.height_geoid))
                
            for line in timestamp:
                data.writelines(line)
            for line in coords:
                data.writelines(line)
            for line in extra:
                data.writelines(line)

            # time in required format
            exiftime = "{}/{}/{} {:02}:{:02}:{:02}".format(
              gps.timestamp_utc.tm_year,
              gps.timestamp_utc.tm_mon,
              gps.timestamp_utc.tm_mday,
              gps.timestamp_utc.tm_hour,
              gps.timestamp_utc.tm_min,
              gps.timestamp_utc.tm_sec)

            row = [exiftime, gps.latitude, gps.longitude, 
                   gps.altitude_m, gps.track_angle_deg, 
                   time.asctime(time.localtime(time.time())),
                   mpu.acceleration, mpu.gyro, mpu.temperature
                ]
            write.writerow(row)

        else:
            data.write("\nNo GPS fix \n")    
            sentence = gps.readline()
            if not sentence:
                continue
            print(str(sentence, "ascii").strip())
            data.write(str(sentence, "ascii").strip())
 
        data.flush()