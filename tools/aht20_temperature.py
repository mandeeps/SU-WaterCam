#!/usr/bin/env python3
# get temp & humidity reading from Adafruit AHT20
import os
from pathlib import Path

_REPO_ROOT = Path(os.environ.get("WATERCAM_REPO", str(Path(__file__).resolve().parent.parent)))

try:
    import board
except Exception:
    board = None

try:
    import adafruit_ahtx0
except ImportError:
    adafruit_ahtx0 = None

_sensor = None


def _get_sensor():
    global _sensor
    if _sensor is not None:
        return _sensor
    if board is None or adafruit_ahtx0 is None:
        return None
    try:
        _sensor = adafruit_ahtx0.AHTx0(board.I2C())
        return _sensor
    except Exception:
        return None


def record_csv():
    from time import sleep
    from datetime import datetime
    from csv import DictWriter
    FILE = str(_REPO_ROOT / "data" / "temp_humidity.csv")

    while True:
        sensor = _get_sensor()
        if sensor is None:
            print("AHT20 unavailable")
            sleep(60)
            continue
        temp_c = round(float(sensor.temperature), 1)
        hum_pct = round(float(sensor.relative_humidity), 1)
        ts = datetime.now().strftime('%Y%m%d-%H%M%S')
        print(f"Time: {ts}  Temp: {temp_c} C  Humidity: {hum_pct} %")
        row = {'Time': ts, 'Temp': temp_c, 'Humidity': hum_pct}

        with open(FILE, 'a+', newline='') as out:
            DictWriter(out, row.keys()).writerow(row)

        sleep(60)

def get_aht20():
    sensor = _get_sensor()
    if sensor is None:
        return {}
    try:
        return {"temperature_celsius": float("%0.1f" % sensor.temperature),
                "relative_humidity": int(float("%0.1f" % sensor.relative_humidity))}
    except Exception:
        return {}

if __name__ == '__main__':
    import sys
    sys.exit(record_csv())
