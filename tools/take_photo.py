#!/home/pi/SU-WaterCam/venv/bin/python
# Use picamera2 to take a photo

from picamera2 import Picamera2
import logging
from os import path
from datetime import datetime

try:
    camera = Picamera2()
    config = camera.create_still_configuration()
    camera.configure(config)
except:
    logging.error("Camera loading error")

def main(filepath):
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(filepath, f'{time}.jpg')
    print('taking photo')
    try:
        camera.start_and_capture_file(image,show_preview=False)
    except:
        logging.error("Camera failed to capture")
    
    return image

if __name__ == '__main__':
    import sys
    main(sys.argv[1])
