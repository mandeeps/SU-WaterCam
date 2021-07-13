#!/usr/bin/env python3
# get temp & humidity reading from Adafruit AHT20
from time import sleep
from datetime import datetime
import board
import adafruit_ahtx0
import pandas as pd
import atexit

sensor = adafruit_ahtx0.AHTx0(board.I2C())
df = pd.DataFrame()

@atexit.register
def exit():
    df.to_csv('temp-humidity.csv')
    print('Saved data to temp-humidity.csv, goodbye')

run = True
while run:
    row = [{'Time':datetime.now().strftime('%Y%m%d-%H%M%S'), 'Temp': '%0.1f C' % sensor.temperature, 'Humidity': '%0.1f %%' % sensor.relative_humidity}]
    print("Temperature: %0.1f C" % sensor.temperature)
    print("Humidity: %0.1f %%" % sensor.relative_humidity)
    df = df.append(row)
    sleep(60)
