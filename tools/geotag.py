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
import piexif
camera = PiCamera()
i2c = board.I2C()
gps = adafruit_gps.GPS_GtopI2C(i2c, debug=False)  # Use I2C interface
gps.send_command(b"PMTK314,0,1,0,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0")
gps.send_command(b"PMTK220,1000")
DIRNAME = '/home/pi/SU-WaterCam/images/'
last_print = time.monotonic()
interval = 10

# https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72
from fractions import Fraction
def to_deg(value, loc):
    """convert decimal coordinates into degrees, munutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S", "N"] or ["W", "E"]
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

while True:
    gps.update()
    current = time.monotonic()
    if current - last_print >= interval:
        last_print = current
        # take photo every N seconds
        #camera = PiCamera()
        time_val = datetime.now().strftime('%Y%m%d-%H%M%S')
        image = path.join(DIRNAME, f'{time_val}.jpg')
        print('taking photo')
        camera.capture(image)
        # Close camera object to save power
        #camera.close

        # only run if we have a location fix, would otherwise fill with garbage data
        if gps.has_fix:
            print("{0:.6f}".format(gps.latitude))
            print("{0:.6f}".format(gps.longitude))
            
            lat_deg = to_deg(gps.latitude,['S','N'])
            lng_deg = to_deg(gps.longitude,['W','E'])
            exiv_lat = (change_to_rational(lat_deg[0]), change_to_rational(lat_deg[1]), change_to_rational(lat_deg[2]))
            exiv_lng = (change_to_rational(lng_deg[0]), change_to_rational(lng_deg[1]), change_to_rational(lng_deg[2]))
                
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