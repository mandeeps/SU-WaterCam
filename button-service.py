#!/usr/bin/env python3
# Simple script to run as a daemon (with SystemD or other init)
# and trigger the take_two_photos.sh script upon a button press
# (we could just run those processes directly...)
# Button must be wired to correct GPIO pin, with a pulldown resistor

import atexit
import subprocess
from RPi import GPIO

pin = 40

GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

def down(pin):
    print(f"Button DOWN on pin {pin}")
    subprocess.call("/home/pi/SU-WaterCam/take_two_photos.sh")

def up(pin):
    print(f"Button UP on pin {pin}")

GPIO.add_event_detect(pin, GPIO.RISING, callback=lambda x: down(pin))

#GPIO.add_event_detect(pin, GPIO.FALLING, callback=lambda x: up(pin))

# for interactive use
# message = input("Enter to quit \n")

def exit_handler():
    print("Button service exiting")
    GPIO.cleanup()

atexit.register(exit_handler)
