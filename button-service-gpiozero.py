#!/usr/bin/env python3

# This version uses GPIO Zero instead of the RPi.GPIO library

# Simple script to run as a daemon (with SystemD or other init)
# and trigger the take_two_photos.sh script upon a button press
# (we could just run those processes directly...)

import subprocess
from gpiozero import Button
from signal import pause

def single_press(button):
    print(f"Button DOWN on pin {button.pin}")
    subprocess.call("/home/pi/SU-WaterCam/take_two_photos.sh")

# Adjust button as needed
button = Button(6)
button.when_released = single_press
pause()
