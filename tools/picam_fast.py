#!/usr/bin/env python
import time
from datetime import datetime
from picamera2 import Picamera2

i = 0
start = time.time()
picam2 = Picamera2()

capture_config = picam2.create_still_configuration(main={"format": 'RGB888', "size": (2592, 1944)})
picam2.configure(capture_config)
picam2.start()

open_camera_time = time.time()
print("open time:" + str(open_camera_time -start))

while True:
    last_photo_time = time.time()
    picam2.capture_array("main")
    date = datetime.now().strftime('%Y%m%d-%H%M%S')
    picam2.capture_file(f"photo_{i}_{date}.jpg")
    photo_time = time.time()
    print("picture {} take time: {}".format(i, photo_time - last_photo_time))

    i += 1
