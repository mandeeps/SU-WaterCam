#!/usr/bin/env python3
# get temp & humidity reading from Adafruit AHT20
from time import sleep
from datetime import datetime
from csv import DictWriter

import board
import adafruit_ahtx0

SENSOR = adafruit_ahtx0.AHTx0(board.I2C())
FILE = '/home/pi/HotWaterCam/data/temp_humidity.csv'

RUN = True

def main():
    while RUN:
        row = {'Time':datetime.now().strftime('%Y%m%d-%H%M%S'),
               'Temp': '%0.1f C' % SENSOR.temperature, 'Humidity': '%0.1f %%' %
               SENSOR.relative_humidity}
        print(row)

        with open(FILE, 'a+', newline='') as out:
            #DictWriter(out, row.keys()).writeheader()
            DictWriter(out, row.keys()).writerow(row)

        sleep(60)

if __name__ == '__main__':
    import sys
    sys.exit(main())
