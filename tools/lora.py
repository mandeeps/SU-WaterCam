#!/usr/bin/env python3
# example from adafruit
# https://forums.adafruit.com/viewtopic.php?p=856892
# https://learn.adafruit.com/lora-and-lorawan-radio-for-raspberry-pi/raspberry-pi-wiring

import time
import busio
from digitalio import DigitalInOut, Direction, Pull
import board

# Import the RFM9x radio module.
import adafruit_rfm9x

# Configure RFM9x LoRa Radio
#CS = DigitalInOut(board.CE0)
#RESET = DigitalInOut(board.D7)

CS = DigitalInOut(board.CE1)
RESET = DigitalInOut(board.D25)
spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
 
while True:
    # Attempt to set up the RFM9x Module
    try:
        rfm9x = adafruit_rfm9x.RFM9x(spi, CS, RESET, 915.0)
        print('RFM9x: Detected')
    except RuntimeError as error:
        print('RFM9x Error: ', error)
 
    time.sleep(1)
