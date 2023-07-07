#!/usr/bin/env python3
# Use subprocess to run lepton and capture binaries sequentially
# Records image and temperature data from Flir Lepton

from shutil import copy
from os import path, mkdir, remove
import subprocess # to call external apps
from datetime import datetime
import pytz

DIRNAME = '/home/pi/SU-WaterCam/'
# User configurable values
TIMEZONE = pytz.timezone('US/Eastern') # Set correct timezone here

def main():
    # Local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M%S')

    # create new directory for data from this run
    folder = path.join(DIRNAME, f'data/lepton-{time_val}')
    mkdir(folder)

    # copy lepton binary into newly created directory to save data there
    source = path.join(DIRNAME, 'lepton')
    lepton = path.join(folder, 'lepton')
    print(lepton)
    copy(source, lepton)

    # do the same for the capture binary
    source = path.join(DIRNAME, 'capture')
    capture = path.join(folder, 'capture')
    copy(source, capture)
    print(capture)

    # call capture and lepton binaries to save image and temperature data
    print('saving thermal photo...')
    try:
        subprocess.run([capture], check=True, cwd=folder, timeout=10)
    except subprocess.CalledProcessError as err:
        print(f"Capture error: {err.returncode}\n {err}")
    except subprocess.TimeoutExpired as err:
        print(f"Capture process timed out: {err} \n")

    print('\n saving temperature data...')
    try:
        subprocess.run([lepton], check=True, cwd=folder, timeout=10)
    except subprocess.CalledProcessError as err:
        print(f"Lepton error: {err.returncode}\n {err}")
    except subprocess.TimeoutExpired as err:
        print(f"Lepton process timed out: {err} \n")

    # delete duplicated binaries
    remove(lepton)
    remove(capture)

if __name__ == '__main__':
    import sys
    sys.exit(main())
