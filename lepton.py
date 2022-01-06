#!/usr/bin/env python3

import atexit
from os import path, mkdir, listdir, remove
import subprocess # to call external apps
from statistics import median
# time
from time import sleep
from datetime import datetime
import pytz
# data
import pandas as pd
from compress_pickle import dump
import shutil

DIRNAME = '/home/pi/SU-WaterCam/'
# User configurable values
TIMEZONE = pytz.timezone('US/Eastern') # Set correct timezone here
INTERVAL = 1#6 # Time delay between each reading
LIMIT = 5 # Max number of frames to take per boot

def main():
    # Local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M')
    
    # create new folder named with datetime to save data to
    folder = path.join(DIRNAME, f'data/lepton-{time_val}')
    mkdir(folder)
    
    # copy lepton binary into new folder so it will save data there
    # This is not an ideal approach but it works...
    source = path.join(DIRNAME, 'lepton')
    app = path.join(folder, 'lepton')
    print(app)
    shutil.copy(source, app)
    
    # call capture and lepton binaries to save image and temp data
    for i in range(LIMIT):
        # Photos are saved into the images folder
        print('saving photo...')
        subprocess.run([path.join(DIRNAME, 'capture')], check=True)
        print('\nsaving temp data...')
        subprocess.run([app], check=True, cwd=folder)
        
        # Pause until the next frame
        if i < LIMIT:
            sleep(INTERVAL)
            
    # once done saving data, read temps back into 
    # Pandas dataframe for processing and export to csv
    # first remove copied lepton binary
    print('\n second stage \n')
    remove(app)
    
    DF = pd.DataFrame()
    
    for item in listdir(folder):
        print(item)
#        tempDF = pd.read_csv(item, header=None)
#        print(tempDF)
        #DF.append(pd.read_csv(item, sep='\s', header=0))
        
#    print(DF)

if __name__ == '__main__':
    import sys
    sys.exit(main())
