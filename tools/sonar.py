#!/usr/bin/env python3
# RCWL1601 with HCSR04 driver ultrasonic sensor
from time import sleep
from datetime import datetime
from csv import DictWriter

import board
import adafruit_hcsr04

FILE = '/home/pi/HotWaterCam/data/distance.csv'
RUN = True

def main():
    with adafruit_hcsr04.HCSR04(trigger_pin=board.D5, echo_pin=board.D6) as sonar:
        while RUN:
            try:
                dist = sonar.distance
            except RuntimeError:
                print('error, retrying...')
            else:
                print(dist)
                row = {'Time':datetime.now().strftime('%Y%m%d-%H%M%S'),
                       'Distance': dist}
                with open(FILE, 'a+', newline='') as out:
                    DictWriter(out, row.keys()).writeheader()
                    DictWriter(out, row.keys()).writerow(row)
            sleep(30)

if __name__ == '__main__':
    import sys
    sys.exit(main())
