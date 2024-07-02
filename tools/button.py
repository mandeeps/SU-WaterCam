#!/usr/bin/env python3

import subprocess
from RPi import GPIO

pin = 40

GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

def down(pin):
    print(f"Button DOWN on pin {pin}")
    subprocess.call("../take_two_photos.sh")

def up(pin):
    print(f"Button UP on pin {pin}")

GPIO.add_event_detect(pin, GPIO.RISING, callback=lambda x: down(pin))
#GPIO.add_event_detect(pin, GPIO.FALLING, callback=lambda x: up(pin))

message = input("Enter to quit \n")

GPIO.cleanup()
