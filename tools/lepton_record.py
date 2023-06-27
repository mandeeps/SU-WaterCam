#!/usr/bin/env python3
# Use subprocess to run lepton and capture binaries sequentially
# Records image and temperature data from Flir Lepton

import shutil
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
    app = path.join(folder, 'lepton')
    print(app)
    shutil.copy(source, app)

    # call external capture and lepton binaries to save image and temperature data
    print('saving thermal photo...')
    subprocess.run([path.join(DIRNAME, 'capture')], check=True)
    print('\nsaving temp data...')
    subprocess.run([app], check=True, cwd=folder)
    remove(app)

if __name__ == '__main__':
    import sys
    sys.exit(main())
