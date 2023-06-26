#!/home/pi/SU-WaterCam/venv/bin/python
# Take a photo and embed GPS/IMU data into EXIF
# Using Raspberry Pi camera, IMU, and GPS data from gpsd

import logging
import time
from datetime import datetime
from os import path
from fractions import Fraction
import piexif
import piexif.helper
import gpsd2
from libxmp import XMPFiles, consts
import bno055_imu # using BNO055 sensor
import take_photo # Camera handler

# two helper functions from https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72
def to_deg(value, loc):
    """convert decimal coordinates into degrees, minutes and seconds tuple
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
    minutes = int(t1)
    sec = round((t1 - min)* 60, 5)
    return (deg, minutes, sec, loc_value)

def change_to_rational(number):
    """convert a number to rational
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return (f.numerator, f.denominator)

def main():
    # setup
    logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s', encoding='utf-8', level=logging.DEBUG)
    DIRNAME = '/home/pi/SU-WaterCam/images/'

    try:
        gpsd2.connect()
    except:
        logging.error("GPS error")

    # data record
    DATA = '/home/pi/SU-WaterCam/data/gps.txt'
    data = open(DATA, 'a')
    last_print = time.monotonic()

    # How often we take a photo
    INTERVAL = 1
    # How many photos to take per run
    LIMIT = 10
    LOOP = 0


    RUNNING = True
    while RUNNING:
        current = time.monotonic()
        # only proceed to recording data if past interval time
        if current - last_print >= INTERVAL:
            last_print = current

            # take a new photo
            image = take_photo.main(DIRNAME)

            # get IMU data
            try:
                imu_values = bno055_imu.get_values()
            except:
                logging.error("IMU Error")
            else: # log IMU data to text file
                imu = [f"\nFile: {image}\n",
                    f"Time: {time.asctime(time.localtime(time.time()))}\n",
                    f"Accelerometer: {imu_values['Accelerometer']}\n",
                    f"Gyro: {imu_values['Gyro']}\n",
                    f"Temperature: {imu_values['Temperature']}\n"]
                for line in imu:
                    data.writelines(line)
                yaw, roll, pitch = imu_values['Euler']

            # Start exif handling
            # load original exif data
            exif_data = piexif.load(image)
            # Add roll/pitch/yaw to UserComment tag if they exist
            if roll:
                user_comment = piexif.helper.UserComment.dump(f"Roll {roll} Pitch {pitch} Yaw {yaw}")
                exif_data["Exif"][piexif.ExifIFD.UserComment] = user_comment

            # get current gps info from gpsd
            gps_data = []
            try:
                packet = gpsd2.get_current()
            except:
                logging.error("No GPS data returned")
                data.write("\nNo GPS fix \n")
            else:
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
                        piexif.GPSIFD.GPSAltitudeRef: 0,
                        piexif.GPSIFD.GPSAltitude: change_to_rational(round(packet.alt)),
                        piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
                        piexif.GPSIFD.GPSLatitude: exiv_lat,
                        piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
                        piexif.GPSIFD.GPSLongitude: exiv_lng,
                        piexif.GPSIFD.GPSTrack: change_to_rational(packet.track)
                }

                # Since we have GPS data, add to Exif
                gps_exif = {"GPS": gps_ifd}
                print(gps_exif)
                # add gps tag to original exif data
                exif_data.update(gps_exif)

            # Finish exif handling
            # convert to byte format for writing into file
            exif_bytes = piexif.dump(exif_data)
            # write to disk
            piexif.insert(exif_bytes, image)

            # Write roll/pitch/yaw to XMP tags for Pix4D
            if roll:
                xmpfile = XMPFiles(file_path=image, open_forupdate=True)
                xmp = xmpfile.get_xmp()
                xmp.set_property(consts.XMP_NS_DC, 'Roll', str(roll))
                xmp.set_property(consts.XMP_NS_DC, 'Pitch', str(pitch))
                xmp.set_property(consts.XMP_NS_DC, 'Yaw', str(yaw))
                xmpfile.put_xmp(xmp)
                xmpfile.close_file()

            LOOP = LOOP + 1
            data.flush()

        if LOOP == LIMIT:
            RUNNING = False

if __name__ == "__main__":
    main()
