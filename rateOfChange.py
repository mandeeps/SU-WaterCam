#!/usr/bin/env python3
# store temp readings as pandas Series, store Series in DataFrame,
# calc changes, rate of change per pixel, mean of change rates

import atexit
import pickle
import lzma
import os
import subprocess #if not using picamera, call external script
# time
from time import sleep
from datetime import datetime
import pytz
# data
import pandas as pd
from compress_pickle import dump
# hardware
import board
import busio
import adafruit_mlx90640

I2C = busio.I2C(board.SCL, board.SDA, frequency=400000)
MLX = adafruit_mlx90640.MLX90640(I2C)
MLX.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
DIRNAME = '/home/pi/HotWaterCam/' #os.getcwd()
DF = pd.DataFrame()

# config
# user configurable values
TIMEZONE = pytz.timezone('US/Eastern')
INTERVAL = 6 # time delay between each reading
LIMIT = 5 # max number of frames to take

# exit handler
@atexit.register
def close():
    # to run on shutdown
    print('exit received, saving and shutting down')
    # any other cleanup functions here
    save()

def save():
    ### save averaged out thermal sensor data ###
    # call hourly_rate if run at least once to calculate rate of change
    global DF

    # local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M')
    data_file = os.path.join(DIRNAME, f'data/data-{time_val}.csv')

    # export DataFrames in csv format
    print(f'Writing to: {data_file}')
    DF.to_csv(data_file, index=True, header=True)
    
    # save mean of each column in DF to account for sensor errors
    avg_list = []    
    for column in DF:
        avg_list.append(DF[column].mean())

    avg = pd.DataFrame(avg_list)
    avg = avg.transpose()
    avg.index = pd.DatetimeIndex([pd.to_datetime('now').tz_localize(pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0)])
    avg = avg.round(2)
    
    avg_file = os.path.join(DIRNAME, f'data/avg_temp_per_boot.csv')
    try: # check if file containing averaged readings already exists
        temp_csv = pd.read_csv(avg_file)
    except Exception: # if it doesn't create one with header values
        header_val = True
    else: # if it already exists don't write more header lines
        header_val = False

        # if it already exists, calculate rate of temperature change
        # per hour by comparing temperature per pixel to prior value
        previous_temps = temp_csv.iloc[-1:] # get last row of file
        hourly_rate(avg, previous_temps)

    avg.to_csv(avg_file, mode='a', index=True, header=header_val)

def hourly_rate(now, previous):
    print('Current avg temp per pixel: ', now)
    print('Previous hour temps per pixel', previous) 
    
    # combine previous and current temps
    combined = pd.concat(previous, now)
    
    change_per_hour = pd.DataFrame()        
    for column in combined:
        value = abs(combined[column].diff())

        # todo multiple value by 100 and round to tenths for readability
        change_per_hour[f'Change {column}'] = value 
    
    print(change_per_hour)

    time_val = datetime.now().strftime('%Y%m%d-%H%M')
    
    change_per_hour_file = os.path.join(DIRNAME, f'data/change_per_hour-{time_val}.csv')
    change_per_hour.to_csv(change_per_hour_file, index=True, header=True)
    
    # divide pixels by change rate compared to reference pixels
    # or compared to median of data set

    median = change_per_hour.median()
    land_or_Water = pd.DataFrame()
    for column in change_per_hour:
        if change_per_hour[column] < median:
            land_or_water[column] = 0 # water
        else:
            land_or_water[column] = 1 # land
            
    land_or_water_file = os.path.join(DIRNAME, f'data/land_or_water-{time_val}.csv')
    land_or_water.to_csv(land_or_water_file, index=True, header=True)
    land_or_water_file_compressed = os.path.join(DIRNAME, f'data/land_or_water-{time_val}.bz')
    dump(land_or_water, land_or_water_file_compressed, compression='bz2')
                
def main():
    for i in range(LIMIT):
        data = [None]*768
        try:
            MLX.getFrame(data)
        except ValueError:
            print('mlx frame error!!!')
            continue #retry

        frame = pd.Series(data, name=pd.to_datetime('now').tz_localize(
            pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0))

        print(frame)

        global DF
        DF = pd.concat([DF, frame.to_frame().T])

        #script to take a photo with raspistill, save to images folder
        print('saving photo...')
        subprocess.run([os.path.join(DIRNAME, 'pic.sh')], check=True)

        # pause until next frame
        if i < LIMIT:
            sleep(INTERVAL)

if __name__ == '__main__':
    import sys
    sys.exit(main())
