#!/usr/bin/env python
# Take two photos with Dorhea IR-Cut camera
# one with NIR filter in place and one without
# Set GPIO HIGH to include NIR in the red band and LOW for normal photo

import logging
from os import path
from datetime import datetime
from picamera2 import Picamera2
from gpiozero import LED

logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s',
    encoding='utf-8', level=logging.DEBUG)

try:
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)
    # picam2.start() -- do not start outside start_and_capture function as this interferes with Flir Lepton! (for some reason I don't understand)
except Exception:
    logging.error("Camera loading error")

def take_photo(filepath, nir):
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(filepath, f'{time}-NIR-{nir}.jpg')
    print(f'taking photo: {image}')

    try:
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception:
        logging.error("Camera failed to capture")
    return image

def main(filepath: str) -> str:
    # Adjust GPIO as appropriate. We are using GPIO 21, pin 40
    pin = LED(21)
    pin.off()
    print(f"Pin state is: {pin.value}")
    basename = take_photo(filepath, "OFF")

    pin.on()
    take_photo(filepath, "ON")

    return basename

    
if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = "/home/pi/SU-WaterCam/images/"

    name = main(filepath)
    print(f"Photo: {name}")
