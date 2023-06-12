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
import piexif.helper
import picamera
import gpsd2
from fractions import Fraction
from math import atan2, pi, sqrt

# setup
i2c = board.I2C()
mpu = adafruit_mpu6050.MPU6050(i2c, 0x69) # must set IMU to 0x69 because of WittyPi
gpsd2.connect()
DIRNAME = '/home/pi/SU-WaterCam/images/'

offset_accel = []
offset_gyro = []
offset_path = "/home/pi/SU-WaterCam/data/imu_offsets.txt"
with open(offset_path) as file:
    # first 3 lines are accelerometer offsets
    for i in range(3):
        # read line, strip newline char,convert to float
        offset_accel.append(float(file.readline().rstrip()))
    # gyro offset lines
    for i in range(3):
        offset_gyro.append(float(file.readline().rstrip()))

# data record
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

        # take a new photo
        with picamera.PiCamera() as camera:
            camera.resolution = (2592, 1944) # max res of camera
            time.sleep(1) # Camera has to warm up
            time_val = datetime.now().strftime('%Y%m%d-%H%M%S')
            image = path.join(DIRNAME, f'{time_val}.jpg')
            print('taking photo')
            camera.capture(image)

        # subtract the offset values from what the IMU measures
        accel = [x - y for x, y in zip(mpu.acceleration, offset_accel)]
        gyro = [x - y for x, y in zip(mpu.gyro, offset_gyro)]
        accel_record = "Acceleration: X: {}, Y: {}, Z: {} m/s^2 \n".format(*accel)
        gyro_record = "Gyro X: {}, Y: {}, Z: {} degrees/s \n".format(*gyro)
        temp_record = f"Temperature: {mpu.temperature:.2f} C \n"

        # log IMU data to text file
        imu = [f"\nFile: {image}\n", "Time: {} \n".format(time.asctime(time.localtime(time.time()))),
        accel_record, gyro_record, temp_record]

        for line in imu:
            data.writelines(line)
        
        # Attempt to calculate Roll/Pitch/Yaw values
        X = accel[0]
        Y = accel[1]
        Z = accel[2]
        if Z > 0:
            sign = 1
        else:
            sign = -1
        miu = 0.001
        roll = atan2(Y, sign * sqrt(Z*Z + miu*X*X))
        pitch = atan2(X, sqrt(Y*Y + Z*Z)) * 180/pi;
        yaw = 0 # TODO add yaw later!

        # Start exif handling
        # load original exif data
        exif_data = piexif.load(image)
        # Add roll/pitch/yaw to UserComment tag
        user_comment = piexif.helper.UserComment.dump(f"Roll {roll} Pitch {pitch} Yaw {yaw}")
        exif_data["Exif"][piexif.ExifIFD.UserComment] = user_comment
 
        # get current gps info from gpsd
        packet = gpsd2.get_current()
        if packet.mode >= 2:
            gps_data = [
                f"GPS Time UTC: {packet.time}\n",
                f"GPS Time Local: {time.asctime(time.localtime(time.time()))}\n",
                f"Latitude: {packet.lat} degrees\n",
                f"Longitude: {packet.lon} degrees\n",
                f"Track: {packet.track}\n",
                f"Satellites: {packet.sats}\n", 
                f"Error: {packet.error}\n",
                f"Precision: {packet.position_precision()}\n",
                f"Map URL: {packet.map_url()}\n",
                f"Device: {gpsd2.device()}\n"]

            if packet.mode >= 3:
                gps_data.append(f"Altitude: {packet.alt}\n")

            # save to text file
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
                    piexif.GPSIFD.GPSTimeStamp: packet.time,
                    piexif.GPSIFD.GPSAltitudeRef: 0,
                    piexif.GPSIFD.GPSAltitude: change_to_rational(round(packet.alt)),
                    piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
                    piexif.GPSIFD.GPSLatitude: exiv_lat,
                    piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
                    piexif.GPSIFD.GPSLongitude: exiv_lng,
                    piexif.GPSIFD.GPSTrack: packet.track,
            }
            
            # Since we have GPS data, add to Exif
            gps_exif = {"GPS": gps_ifd}
            print(gps_exif)
            # load original exif data
            #exif_data = piexif.load(image)
            
            # add gps tag to original exif data
            exif_data.update(gps_exif)
            # Add roll/pitch/yaw to UserComment tag
            #user_comment = piexif.helper.UserComment.dump(f"Roll {roll} Pitch {pitch} Yaw {yaw}")
            #exif_data["Exif"][piexif.ExifIFD.UserComment] = user_comment
            # convert to byte format for writing into file
            #exif_bytes = piexif.dump(exif_data)
            # write to disk
            #piexif.insert(exif_bytes, image)
        else:
            data.write("\nNo GPS fix \n")    
         
        # Finish exif handling
        # convert to byte format for writing into file
        exif_bytes = piexif.dump(exif_data)
        # write to disk
        piexif.insert(exif_bytes, image)
 
        loop = loop + 1
        data.flush()
            
    if(loop == LIMIT):
        running = False
