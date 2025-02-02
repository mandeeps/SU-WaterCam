#!/usr/bin/env python3

# This version uses GPIO Zero instead of the RPi.GPIO library

# Simple script to run as a daemon (with SystemD or other init)
# and trigger a subprocess to take photos
# The button can be connected to the Raspberry Pi pin #29 (GPIO 5) and ground.

import subprocess
from gpiozero import Button
from signal import pause

def single_press(button):
    print(f"Button DOWN on pin {button.pin}")
    subprocess.call("/home/pi/SU-WaterCam/tools/take_nir_photos.py")

# Using GPIO 5 because it is HIGH by default and we connect it to ground
# by pushing the button in. Already using GPIO 6 for the Lepton reset function 
# Adjust button GPIO as needed
button = Button(5)
button.when_released = single_press # Call on release 
pause()
