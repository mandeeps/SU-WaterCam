#!/usr/bin/env python
# Set GPIO to High for 0.3 seconds to trigger WittyPi power switch

from time import sleep
import RPi.GPIO as GPIO

# Select a GPIO that defaults to LOW
# https://roboticsbackend.com/raspberry-pi-gpios-default-state/
# GPIOs up to 8: default state is HIGH, ~3.3v
# GPIOs 9 to 27: default state is LOW, ~0V

pin =  37 # physical pin 37 is GPIO 26

GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin, GPIO.OUT)

# set HIGH for 0.3 seconds
GPIO.output(pin, GPIO.HIGH)
sleep(0.3)

# reset to default LOW state
GPIO.output(pin, GPIO.LOW)

print(f"Pin {pin}: {GPIO.input(pin)}")
