#!/home/pi/SU-WaterCam/venv/bin/python
# Use picamera2 to take a photo
# Assumes use of 5MP camera

import logging
from os import path
from datetime import datetime
from picamera2 import Picamera2

logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s',
    encoding='utf-8', level=logging.DEBUG)

try:
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)
    # picam2.start() -- do not start outside start_and_capture function as this interferes with Flir Lepton
except Exception:
    logging.error("Camera loading error")

def main(filepath: str) -> str:
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(filepath, f'{time}.jpg')
    print(f'taking photo: {image}')
    try:
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception:
        logging.error("Camera failed to capture")

    return image

if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = "/home/pi/SU-WaterCam/images/"

    name = main(filepath)
    print(f"Photo: {name}")
