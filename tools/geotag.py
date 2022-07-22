#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul 21 15:09:12 2022

@author: manu
"""


# Take a photo and record GPS coords to EXIF
import time
from datetime import datetime
from os import path
import board
from picamera import PiCamera
import adafruit_gps
from exif import Image
camera = PiCamera()

i2c = board.I2C()
gps = adafruit_gps.GPS_GtopI2C(i2c, debug=False)  # Use I2C interface
gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
gps.send_command(b"PMTK220,1000")


DIRNAME = '/home/pi/SU-WaterCam/images/'

last_print = time.monotonic()
while True:

    gps.update()
    current = time.monotonic()
    if current - last_print >= 5.0:
        last_print = current
        # take photo every 5 seconds
        time_val = datetime.now().strftime('%Y%m%d-%H%M%S')
        image = path.join(DIRNAME, f'{time_val}.jpg')
        camera.capture(image)


        # only run if we have a location fix, would otherwise fill with garbage data
        if gps.has_fix:
            
            
            # open image 
            with open(image, 'rb') as image_file:
                current_image = Image(image_file)
                current_image.gps_latitude = "{:2.}{:2.4f}".format(gps.latitude_degrees, gps.latitude_minutes) #"{0:.3f}".format(gps.latitude)
                current_image.gps_longitude = "{:2.}{:2.4f}".format(gps.longitude_degrees, gps.longitude_minutes) # "{0:.3f}".format(gps.longitude)
                
            # write modified image
            with open(image, 'wb') as write_new:
                write_new.write(current_image.get_file())