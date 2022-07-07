#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul  7 12:51:56 2022

@author: manu
"""


# Log GPS and IMU data to a file for use by exiftool to geotag images
# Based on Adafruit simple gps script and imu script

import time
import board
import adafruit_gps
import adafruit_mpu6050
i2c = board.I2C()
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)
gps = adafruit_gps.GPS_GtopI2C(i2c, debug=False)  # Use I2C interface
gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
gps.send_command(b"PMTK220,1000")

last_print = time.monotonic()

# open gps log file
LOG_FILE = '/home/pi/SU-WaterCam/data/gps.txt'
LOG_MODE = 'a'

with open(LOG_FILE, LOG_MODE) as log:

    while True:
        gps.update()
        current = time.monotonic()
        if current - last_print >= 1.0:
            last_print = current

            # log IMU data
            imu = ["Time:{} \n".format(time.asctime(time.localtime(time.time()))),
            "Acceleration: X:{0[0]}, Y:{0[1]}, Z:{0[2]} m/s^2 \n".format(mpu.acceleration),
                  "Gyro X:{0[0]}, Y:{0[1]}, Z:{0[2]} degrees/s \n".format(mpu.gyro),
                 "Temperature: {} C".format(mpu.temperature),"\n"]

            
            # save imu data first
            #log.write("{}".format(time.asctime(time.localtime(time.time()))))
            #log.writelines('\n')
            for line in imu:
                log.writelines(line)
           
            sentence = gps.readline()
            if not sentence:
                continue
            print(str(sentence, "ascii").strip())
            log.write(str(sentence, "ascii").strip())
            log.flush()
            
    
            
        
#        if not gps.has_fix:
#            # Try again if we don't have a fix yet.
#            print("No GPS fix yet")
#            # write imu data, close file
#            for line in data:
#                log.writelines(line)
#            log.close()
#            continue
#        # We have a fix! (gps.has_fix is true)
#        # Print out details about the fix like location, date, etc.
#        print("=" * 40)  # Print a separator line.
#        timestamp = [
#            "Fix timestamp: {}/{}/{} {:02}:{:02}:{:02}".format(
#                gps.timestamp_utc.tm_mon,  # Grab parts of the time from the
#                gps.timestamp_utc.tm_mday,  # struct_time object that holds
#                gps.timestamp_utc.tm_year,  # the fix time.  Note you might
#                gps.timestamp_utc.tm_hour,  # not get all data like year, day,
#                gps.timestamp_utc.tm_min,  # month!
#                gps.timestamp_utc.tm_sec
#            ), "\n"
#        ]
#        print(timestamp)
#        data.append(timestamp)
#        
#        coords = ["Latitude: {0:.6f} degrees".format(gps.latitude),
#        "Longitude: {0:.6f} degrees".format(gps.longitude),
#        "Precise Latitude: {:2.}{:2.4f} degrees".format(
#                gps.latitude_degrees, gps.latitude_minutes
#            ),
#        "Precise Longitude: {:2.}{:2.4f} degrees".format(
#            gps.longitude_degrees, gps.longitude_minutes
#        ), "\n"]
#        print(coords)
#        data.append(coords)
#        
#        print("Fix quality: {}".format(gps.fix_quality))
#        # Some attributes beyond latitude, longitude and timestamp are optional
#        # and might not be present.  Check if they're None before trying to use!
#        if gps.satellites is not None:
#            print("# satellites: {}".format(gps.satellites))
#        if gps.altitude_m is not None:
#            print("Altitude: {} meters".format(gps.altitude_m))
#        if gps.speed_knots is not None:
#            print("Speed: {} knots".format(gps.speed_knots))
#        if gps.track_angle_deg is not None:
#            print("Track angle: {} degrees".format(gps.track_angle_deg))
#        if gps.horizontal_dilution is not None:
#            print("Horizontal dilution: {}".format(gps.horizontal_dilution))
#        if gps.height_geoid is not None:
#            print("Height geoid: {} meters".format(gps.height_geoid))
#        
#        # write imu data, close file
#        for line in data:
#            log.writelines(line)
#        log.close()
