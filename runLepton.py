#!/usr/bin/env python3
from os import path, mkdir, listdir, remove
import subprocess # to call external apps
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
INTERVAL = 6 # Time delay between each reading
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
    
    # call external capture and lepton binaries to save image and temp data
    for i in range(LIMIT):
        # Photos are saved into the images folder
        print('saving thermal photo...')
        subprocess.run([path.join(DIRNAME, 'capture')], check=True)
        print('\nsaving temp data...')
        subprocess.run([app], check=True, cwd=folder)
        # save normal picture
        subprocess.run([path.join(DIRNAME, 'pic.sh')], check=False)
        
        # Pause until the next frame
        if i < LIMIT:
            sleep(INTERVAL)
            
    # once done saving data, read temps back into 
    # Pandas dataframe for processing and export to csv
    # first remove copied lepton binary
    print('\n second stage \n')
    remove(app)
    
    frames = []
    for item in listdir(folder):
        tempDF = pd.read_csv(path.join(folder, f'{item}'), sep='\s+', index_col=False, header=None)
        # Create Pandas series using the DF we saved the frame to
        #series = tempDF.transpose()[0]
        # Set the name of the series to the current timestamp
        #series = pd.Series(tempDF.transpose()[0], name=pd.to_datetime('now').tz_localize(
        #    pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0))
        #frame = pd.Series(data, name=pd.to_datetime('now').tz_localize(
        #    pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0))
        
        # Instead of appending directly to DF, append to list for later
        # concatination into the DF so we're not iteratively altering
        # the DF which would be inefficient
        #frames.append(tempDF.to_frame().T)
        frames.append(tempDF)
        
    DF = pd.concat(frames)
    DF = DF.divide(100)
    DF.applymap(np.mean)
    print(DF)
    data_file = path.join(folder, f'{time_val}.csv')
    
    DF.to_csv(data_file)

if __name__ == '__main__':
    import sys
    sys.exit(main())
