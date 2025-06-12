#!/usr/bin/env python3
# get temp & humidity reading from Adafruit AHT20
import board

try:
    import adafruit_ahtx0
except ImportError:
    print("Error: AHT20 import")
except:
    print("Error: AHT20 hardware")

SENSOR = adafruit_ahtx0.AHTx0(board.I2C())

def record_csv():
    from time import sleep
    from datetime import datetime
    from csv import DictWriter
    FILE = '/home/pi/SU-WaterCam/data/temp_humidity.csv'
    
    while True:
        row = {'Time':datetime.now().strftime('%Y%m%d-%H%M%S'),
               'Temp': '%0.1f C' % SENSOR.temperature, 'Humidity': '%0.1f %%' %
               SENSOR.relative_humidity}
        print(row)

        with open(FILE, 'a+', newline='') as out:
            #DictWriter(out, row.keys()).writeheader()
            DictWriter(out, row.keys()).writerow(row)

        sleep(60)

def get_aht20():
    data = {"temperature_celsius": float("%0.1f" % SENSOR.temperature),
            "relative_humidity": int(float("%0.1f" % SENSOR.relative_humidity))}
    return data

if __name__ == '__main__':
    import sys
    sys.exit(record_csv())
