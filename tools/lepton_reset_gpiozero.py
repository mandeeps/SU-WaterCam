#!/usr/bin/env python3
# Flir Lepton breakout board 2.0 reset using GPIO Zero
# Pin 31 (GPIO 6) is used for RESET_L
# Connect to Pin I using the layout specified in the README
# (Flir pin 17 in their documentation)

from gpiozero import DigitalOutputDevice, DigitalInputDevice
from time import sleep

# GPIO 6 corresponds to pin 31 (BCM numbering)
reset_pin = 6

# Set up the pin as an output device, defaulting to HIGH
reset = DigitalOutputDevice(reset_pin, active_high=True, initial_value=True)

# Pull LOW for 1 second to trigger reset
reset.off()
sleep(1.0)

# Return to default HIGH state
reset.on()

# Release the pin and read as input
reset.close()
reset_input = DigitalInputDevice(reset_pin)
print(f"Pin {reset_pin}: {reset_input.value}")
