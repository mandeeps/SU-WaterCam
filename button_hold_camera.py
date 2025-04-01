#!/home/pi/SU-WaterCam/venv/bin/python3

# alternate button-service script for use on 32bit Pi Zero
# This version is meant to be used with a multispectral cameras-only unit
# for manually collecting images and radiometric data. It is faster than
# spawning another Python process and starting PiCamera2 every time photos are
# taken. This is helpful on the Pi Zero

import subprocess
from gpiozero import Button
from signal import pause
from os import path, makedirs, chdir
from datetime import datetime
from picamera2 import Picamera2
from gpiozero import LED

filepath = "/home/pi/SU-WaterCam/images/"

try:
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)
    # picam2.start() -- do not start outside start_and_capture function as this interferes with Flir Lepton! (for some reason I don't understand)
except Exception:
    print("Camera loading error")

def take_photo(directory: str, nir: str) -> str:
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(directory, f'{time}-NIR-{nir}.jpg')
    print(f'taking photo: {image}')

    try:
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception:
        print("Camera failed to capture")

    # get IMU and GPS data and save into image EXIF and XMP
    #add_metadata.add_metadata(image)
    # no IMU or GPS on the Pi Zero units currently

    return image

def flir(directory):
    # Flir Lepton 3.5 capture and lepton binaries for image and radiometery
    chdir(directory)
    try:
        subprocess.run(["/home/pi/SU-WaterCam/capture"], check=True)
    except:
        print("Check Lepton state - capture failed")
    try:
        subprocess.run(["/home/pi/SU-WaterCam/lepton"], check=True)
    except:
        print("Check Lepton state - radiometery failed")

def photos(filepath: str) -> str:
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

def single_press(button):
    print(f"Button DOWN on pin {button.pin}")
    # take photos: optical and NIR
    nir_off_name, directory = photos(filepath)

    print(f"Photo path: {directory}")
    # take FLIR photo and get temperature data from Lepton
    flir(directory)

# Using GPIO 5 because it is HIGH by default and we connect it to ground
# by pushing the button in. Already using GPIO 6 for the Lepton reset function 
# Adjust button GPIO as needed
button = Button(5)
button.when_released = single_press # Call on release 
pause()
