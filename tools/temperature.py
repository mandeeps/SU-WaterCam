#!/usr/bin/env python3
# get temp & humidity reading from Adafruit AHT20
from time import sleep
from datetime import datetime
from csv import DictWriter

import board
import adafruit_ahtx0

SENSOR = adafruit_ahtx0.AHTx0(board.I2C())
FILE = '/home/pi/HotWaterCam/tools/temps.csv'

RUN = True
while RUN:
    ROW = {'Time':datetime.now().strftime('%Y%m%d-%H%M%S'),
        'Temp': '%0.1f C' % SENSOR.temperature, 'Humidity': '%0.1f %%' %
        SENSOR.relative_humidity}
    print(ROW)

    with open(FILE, 'a+', newline='') as out:
        DictWriter(out, ROW.keys()).writeheader()
        DictWriter(out, ROW.keys()).writerow(ROW)

    sleep(60)
