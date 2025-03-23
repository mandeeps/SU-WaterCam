#!/usr/bin/env python
# Take two photos with Dorhea IR-Cut Camera
# One with NIR filter in place and one without
# Set GPIO HIGH to include NIR in the red band and LOW for normal photo
# Call add_metadata to get info from IMU and GPS
# Run lepton and capture binaries to save data from Flir in same directory

import logging
from os import path, makedirs, chdir
from datetime import datetime
from picamera2 import Picamera2
from gpiozero import LED
import add_metadata
import subprocess

logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s',
    encoding='utf-8', level=logging.DEBUG)

try:
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)
    # picam2.start() -- do not start outside start_and_capture function as this interferes with Flir Lepton! (for some reason I don't understand)
except Exception:
    logging.error("Camera loading error")

def take_photo(directory: str, nir: str) -> str:
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(directory, f'{time}-NIR-{nir}.jpg')
    print(f'taking photo: {image}')

    try:
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception:
        logging.error("Camera failed to capture")

    # get IMU and GPS data and save into image EXIF and XMP
    add_metadata.add_metadata(image)
    return image

def flir(directory):
    # Flir Lepton 3.5 capture and lepton binaries for image and radiometery
    chdir(directory)
    try:
        subprocess.run(["/home/pi/SU-WaterCam/capture"], check=True)
    except:
        logging.error("Check Lepton state - capture failed")
    try:
        subprocess.run(["/home/pi/SU-WaterCam/lepton"], check=True)
    except:
        logging.error("Check Lepton state - radiometery failed")

def main(filepath: str) -> str:
    date = datetime.now().strftime('%Y%m%d-%H%M')
    directory = path.join(filepath, date)
    if not path.exists(directory):
        makedirs(directory)

    # Adjust GPIO as appropriate. We are using GPIO 21, pin 40
    pin = LED(21)
    pin.off()
    print(f"Pin state is: {pin.value}")

    basename = take_photo(directory, "OFF")

    pin.on()
    print(f"Pin state is: {pin.value}")
    take_photo(directory, "ON")

    return basename, directory


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = "/home/pi/SU-WaterCam/images/"

    # take photos: optical and NIR
    nir_off_name, directory = main(filepath)

    print(f"Photo path: {directory}")
    # take FLIR photo and get temperature data from Lepton
    flir(directory)
