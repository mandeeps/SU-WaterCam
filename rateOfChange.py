#!/usr/bin/env python3
# store temp readings as pandas Series, store Series in DataFrame,
# calc changes, rate of change per pixel, mean of change rates

import atexit
from os import path
import subprocess #if not using picamera, call external script
from statistics import median
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
    data_file = path.join(DIRNAME, f'data/data-{time_val}.csv')

    # export DataFrames in csv format
    print(f'Writing to: {data_file}')
    DF.to_csv(data_file, index=True, header=True)

    # save mean of each column in DF to account for sensor errors
    avg_list = []
    for column in DF:
        avg_list.append(DF[column].mean())

    avg = pd.DataFrame(avg_list)
    avg = avg.transpose()
    index = [pd.to_datetime('now').tz_localize(pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0)]
    avg.index = pd.DatetimeIndex(index)
    avg = avg.round(2)
    print('avg: ', avg)

    avg_file = path.join(DIRNAME, 'data/avg_temp_per_boot.csv')
    try: # check if file containing averaged readings already exists
        temp_csv = pd.read_csv(avg_file, index_col=0)
    except Exception: # if it doesn't create one with header values
        print('Creating avg_temp_per_boot.csv file')
        header_val = True
    else: # if it already exists
        print('avg file exists')
        header_val = False # don't write more header lines into the file
    finally: # add avg dataframe to csv file
        avg.to_csv(avg_file, mode='a', index=True, header=header_val)

    if not header_val:
        # if it already exists, calculate rate of temperature change
        # per hour by comparing temperature per pixel to prior saved
        # values
        last_rows = pd.read_csv(avg_file, index_col=0).tail(2)
        print('last two rows: ', last_rows)
        hourly_rate(last_rows)

def hourly_rate(rows):
    change_per_hour = pd.DataFrame()

    for column in rows:
        #print('Column in rows: ', rows[column])
        value = abs(rows[column].diff().tail(1))
        change_per_hour[f'Change {column}'] = value

    print('change per hour: ', change_per_hour)
    time_val = datetime.now().strftime('%Y%m%d-%H%M')

    change_per_hour_file = path.join(DIRNAME, f'data/change_per_hour-{time_val}.csv')
    change_per_hour.to_csv(change_per_hour_file, index=True, header=True)

    change_per_hour = change_per_hour.round(2)
    change_list = []
    for column in change_per_hour:
        change_list.append(change_per_hour[column].values[0])

    med_value = median(change_list)

    #TODO implement ref values to compare to
    #ref_value = #reference pixel values

    # divide pixels by change rate compared to median or reference pixels
    extent = []
    for pixel in change_list:
        if pixel < med_value:
            extent.append(0) # water
        else:
            extent.append(1) # land

    extent_file = path.join(DIRNAME, f'data/extent-processed{time_val}.p')
    extent_text = path.join(DIRNAME, f'data/extent-processed{time_val}.txt')

    print('Extent: ', extent)
    with open(extent_text, 'w') as file_handler:
        file_handler.write(''.join(map(str, extent)))

    # compressed pickle file for transmission over Lora radio
    dump(extent, extent_file, compression='bz2')

def main():
    data = [None]*768
    for i in range(LIMIT):

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
        subprocess.run([path.join(DIRNAME, 'pic.sh')], check=True)

        # pause until next frame
        if i < LIMIT:
            sleep(INTERVAL)

if __name__ == '__main__':
    import sys
    sys.exit(main())
