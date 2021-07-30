#!/usr/bin/env python3
# get temp & humidity reading from Adafruit AHT20
from time import sleep
from datetime import datetime
import csv

import board
import adafruit_ahtx0

SENSOR = adafruit_ahtx0.AHTx0(board.I2C())

RUN = True
while RUN:
    row = {'Time':datetime.now().strftime('%Y%m%d-%H%M%S'),
       'Temp': '%0.1f C' % SENSOR.temperature, 'Humidity': '%0.1f %%' %
       SENSOR.relative_humidity}
    print(row)

    with open('temperatures.csv', 'a', newline='') as out:
        writer = csv.DictWriter(out, row.keys())
        writer.writeheader()
        writer.writerow(row)

    sleep(60)
