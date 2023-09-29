#!/usr/bin/env python
# Flir Lepton breakout board 2.0
# Pin 17 on the breakout is RESET_L
# We connect this to pin 31 (GPIO 6) on the Raspberry Pi

from time import sleep
import RPi.GPIO as GPIO

pin = 31

GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin, GPIO.OUT)

# set low for 1 second to trigger reset on breakout board
GPIO.output(pin, GPIO.LOW)
sleep(1.0)

# reset to default state
GPIO.output(pin, GPIO.HIGH)
GPIO.setup(pin, GPIO.IN)

print(f"Pin {pin}: {GPIO.input(pin)}")
