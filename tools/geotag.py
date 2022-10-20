#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul 21 15:09:12 2022

@author: manu
"""
# Take a photo and record GPS coords to EXIF
# Also log GPS and IMU data to a file
# Based on Adafruit simple gps script and imu script

import time
from datetime import datetime
from os import path
import board
import adafruit_gps
import adafruit_mpu6050
import piexif
import picamera

# setup
i2c = board.I2C()
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69)
gps = adafruit_gps.GPS_GtopI2C(i2c, debug=False)  # Use I2C interface
gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
gps.send_command(b"PMTK220,1000")
DIRNAME = '/home/pi/SU-WaterCam/images/'

# raw data record without formatting
DATA = '/home/pi/SU-WaterCam/data/gps.txt'
data = open(DATA, 'a')
last_print = time.monotonic()

# How often we take a photo
INTERVAL = 10
# How many photos to take per run
LIMIT = 10
loop = 0;

# Helper functions for gps coord conversion
# https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72
from fractions import Fraction
def to_deg(value, loc):
    """convert decimal coordinates into degrees, munutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S",
    "N"] or ["W", "E"]
    return: tuple like (25, 13, 48.343 ,'N')
    """
    if value < 0:
        loc_value = loc[0]
    elif value > 0:
        loc_value = loc[1]
    else:
        loc_value = ""
    abs_value = abs(value)
    deg =  int(abs_value)
    t1 = (abs_value-deg)*60
    min = int(t1)
    sec = round((t1 - min)* 60, 5)
    return (deg, min, sec, loc_value)

def change_to_rational(number):
    """convert a number to rantional
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return (f.numerator, f.denominator)

running = True
while running:
    gps.update() # have to keep gps awake
    current = time.monotonic()
    # only proceed to recording data if past interval time
    if current - last_print >= INTERVAL:
        last_print = current
        
        # log IMU data
        imu = ["\nTime: {} \n".format(time.asctime(time.localtime(time.time()))),
        "Acceleration: X: {0[0]}, Y: {0[1]}, Z: {0[2]} m/s^2 \n".format(mpu.acceleration),
        "Gyro X: {0[0]}, Y: {0[1]}, Z: {0[2]} degrees/s \n".format(mpu.gyro),
        "Temperature: {} C".format(mpu.temperature),"\n"]
        
        for line in imu:
            data.writelines(line)
        
        with picamera.PiCamera() as camera:
            camera.resolution = (2592, 1944) # max res of camera
            time.sleep(1) # Camera has to warm up
            time_val = datetime.now().strftime('%Y%m%d-%H%M%S')
            image = path.join(DIRNAME, f'{time_val}.jpg')
            print('taking photo')
            camera.capture(image)

        # only run if we have a location fix, would otherwise fill 
        # with garbage data
        if gps.has_fix:
            print("{0:.6f}".format(gps.latitude))
            print("{0:.6f}".format(gps.longitude))
            
            # Record to data file
            timestamp = [
              "Fix timestamp UTC: {}/{}/{} {:02}:{:02}:{:02}".format(
              gps.timestamp_utc.tm_mon,  # Grab parts of the time from the
              gps.timestamp_utc.tm_mday, # struct_time object that holds
              gps.timestamp_utc.tm_year, # the fix time.  Note you might
              gps.timestamp_utc.tm_hour, # not get all data like year, day,
              gps.timestamp_utc.tm_min,  # month!
              gps.timestamp_utc.tm_sec
              ), "\n"
            ]

            coords = ["Latitude: {0:.3f} degrees".format(gps.latitude),
              "Longitude: {0:.3f} degrees".format(gps.longitude), "\n"]

            # Not always available so check before recording
            extra = []
            if gps.satellites is not None:
                extra.append("# satellites: {}, ".format(gps.satellites))
            if gps.altitude_m is not None:
                extra.append("Altitude: {} meters, ".format(gps.altitude_m))
            if gps.speed_knots is not None:
                extra.append("Speed: {} knots, ".format(gps.speed_knots))
            if gps.track_angle_deg is not None:
                extra.append("Track angle: {} degrees, ".format(gps.track_angle_deg))
            if gps.horizontal_dilution is not None:
                extra.append("Horizontal dilution: {}, ".format(gps.horizontal_dilution))
            if gps.height_geoid is not None:
                extra.append("Height geoid: {} meters, ".format(gps.height_geoid))
                
            for line in timestamp:
                data.writelines(line)
            for line in coords:
                data.writelines(line)
            for line in extra:
                data.writelines(line)
            
            # Conversion for exif
            lat_deg = to_deg(gps.latitude,['S','N'])
            lng_deg = to_deg(gps.longitude,['W','E'])
            
            exiv_lat = (change_to_rational(lat_deg[0]), 
                        change_to_rational(lat_deg[1]), 
                        change_to_rational(lat_deg[2]))
            
            exiv_lng = (change_to_rational(lng_deg[0]), 
                        change_to_rational(lng_deg[1]), 
                        change_to_rational(lng_deg[2]))
                
            gps_ifd = {
                    piexif.GPSIFD.GPSVersionID: (2,0,0,0),
                    piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
                    piexif.GPSIFD.GPSLatitude: exiv_lat,
                    piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
                    piexif.GPSIFD.GPSLongitude: exiv_lng
            }
            
            gps_exif = {"GPS": gps_ifd}
            print(gps_exif)
            # load current exif data
            exif_data = piexif.load(image) 
            # update with GPS tags            
            exif_data.update(gps_exif)
            exif_bytes = piexif.dump(exif_data)
            # write to disk
            piexif.insert(exif_bytes, image)
        else:
            data.write("\nNo GPS fix \n")    
            sentence = gps.readline()
            if not sentence:
                continue
            print(str(sentence, "ascii").strip())
            data.write(str(sentence, "ascii").strip())
 
        loop = loop + 1
        data.flush()
            
    if(loop == LIMIT):
        running = False
