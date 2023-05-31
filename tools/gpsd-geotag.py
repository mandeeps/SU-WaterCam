#!/usr/bin/env python3

# Take a photo and embed GPS/IMU data to EXIF
# Log GPS and IMU data to a file
# Using Raspberry Pi camera, MPU6050 IMU, and GPS data from gpsd

import time
from datetime import datetime
from os import path
import board
import adafruit_mpu6050
import piexif
import picamera
import gpsd2
from fractions import Fraction

# setup
i2c = board.I2C()
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69) # must set IMU to 0x69 because of WittyPi
gpsd2.connect()
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

# helper functions from https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72
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

running = True
while running:
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

        # get current gps info from gpsd
        packet = gpsd2.get_current()
        if packet.mode >= 2:
            gps_data = [f"GPS Time UTC: {packet.time_utc()},
                f"GPS Time Local: {packet.time_local()}",
                f"Latitude: {packet.lat} degrees",
                f"Longitude: {packet.lon} degrees",
                f"Satellites: {packet.sats}", 
                f"Error: {packet.error}",
                f"Precision: {packet.position_precision()}", 
                f"Map URL: {packet.map_url()}",
                f"Device: {gpsd.device()}"]

            if packet.mode >= 3:
                data.append(f"Altitude: {packet.alt}")

            # save to csv file
            for line in gps_data:
                data.writelines(line)

            # Conversion for exif use
            lat_deg = to_deg(packet.lat,['S','N'])
            lng_deg = to_deg(packet.lon,['W','E'])
            
            exiv_lat = (change_to_rational(lat_deg[0]), 
                        change_to_rational(lat_deg[1]), 
                        change_to_rational(lat_deg[2]))
            
            exiv_lng = (change_to_rational(lng_deg[0]), 
                        change_to_rational(lng_deg[1]), 
                        change_to_rational(lng_deg[2]))
                
            gps_ifd = {
                    piexif.GPSIFD.GPSVersionID: (2,0,0,0),
                    piexif.GPSIFD.GPSAltitudeRef: 0,
                    piexif.GPSIFD.GPSAltitude: change_to_rational(round(packet.alt)),
                    piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
                    piexif.GPSIFD.GPSLatitude: exiv_lat,
                    piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
                    piexif.GPSIFD.GPSLongitude: exiv_lng
            }
            
            gps_exif = {"GPS": gps_ifd}
            print(gps_exif)
            # load original exif data
            exif_data = piexif.load(image)
            # add gps tag to original exif data
            exif_data.update(gps_exif)
            # convert to byte format for writing into file
            exif_bytes = piexif.dump(exif_data)
            # write to disk
            piexif.insert(exif_bytes, image)
            # verify exif data
            print(piexif.load(image))
        else:
            data.write("\nNo GPS fix \n")    
 
        loop = loop + 1
        data.flush()
            
    if(loop == LIMIT):
        running = False
