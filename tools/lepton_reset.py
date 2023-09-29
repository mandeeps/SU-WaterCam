#!/usr/bin/env python
# Flir Lepton breakout board 2.0
# Pin 17 on the breakout is RESET_L
# We connect this to pin 37 on the Raspberry Pi

from time import sleep
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BOARD)
GPIO.setup(37, GPIO.OUT)

# Pin 37 default is low, so set high and then low to issue reset
GPIO.output(37, GPIO.HIGH)
sleep(1.0)
GPIO.output(37, GPIO.LOW)
sleep(1.0)

# reset to default state
GPIO.output(37, GPIO.HIGH)
print(f"Pin 37: {GPIO.input(37)}")
