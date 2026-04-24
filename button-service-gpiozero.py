#!/home/pi/SU-WaterCam/venv/bin/python3

# This version uses GPIO Zero instead of the RPi.GPIO library

# Simple script to run as a daemon (with SystemD or other init)
# and trigger the take_two_photos.sh script upon a button press
# (we could just run those processes directly...)

import os
import subprocess
import sys
from pathlib import Path
from gpiozero import Button
from signal import pause

_REPO_ROOT = Path(os.environ.get("WATERCAM_REPO", str(Path(__file__).resolve().parent)))

def single_press(button):
    print(f"Button DOWN on pin {button.pin}")
    subprocess.call([sys.executable, str(_REPO_ROOT / "tools" / "take_nir_photos.py")])

# Using GPIO 5 because it is HIGH by default and we connect it to ground
# by pushing the button in. Already using GPIO 6 for the Lepton reset function 
# Adjust button GPIO as needed
button = Button(5)
button.when_released = single_press # Call on release 
pause()
