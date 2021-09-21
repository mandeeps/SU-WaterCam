#!/usr/bin/env python3
# Store temp readings as pandas Series, store Series in DataFrame,
# calc changes, rate of change per pixel, mean of change rates
# Generate an extent map for Lora transmission from data after each run
# starting with the second run

import atexit
from os import path
import subprocess # if not using picamera, call external script
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
# MLX90640 sensor
import adafruit_mlx90640

# Thermal sensor configuration, change values depending on sensor used
# Config the MLX90640 thermal sensor using the Adafruit library
I2C = busio.I2C(board.SCL, board.SDA, frequency=400000)
MLX = adafruit_mlx90640.MLX90640(I2C)
MLX.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
# Could use os.getcwd() or specify a different directory
DIRNAME = '/home/pi/HotWaterCam/'
RESOLUTION = 768 # 32x24 for MLX90640

DF = pd.DataFrame()
# User configurable values
TIMEZONE = pytz.timezone('US/Eastern') # Set correct timezone here
INTERVAL = 6 # Time delay between each reading
LIMIT = 5 # Max number of frames to take per boot

# Exit handler
# This will run on any shutdown
@atexit.register
def close():
    print('Exit received, saving and shutting down')
    save()
    # Any other cleanup functions can go here

def save():
    # Save the averaged out thermal sensor data
    # Call hourly_rate if run at least once to calculate rate of change
    # per pixel

    global DF

    # Local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M')
    data_file = path.join(DIRNAME, f'data/data-{time_val}.csv')

    # Export DataFrame in csv format
    print(f'Writing to: {data_file}')
    print('DF: ', DF)
    DF.to_csv(data_file, index=True, header=True)

    # Strip outliers from DF
    DF = DF[abs(DF - DF.mean()) <= (1 * DF.std())]
    print('DF sans outliers 1 std dev: ', DF)
    
    # Get mean temperature value per pixel
    avg_list = []
    for column in DF:
        avg_list.append(DF[column].mean())

    # Create a DataFrame from the list of pixel averages
    avg = pd.DataFrame(avg_list)
    # Set it so the pixels are columns not rows
    avg = avg.transpose()
    # Set the index to the current timestamp
    index = [pd.to_datetime('now').tz_localize(pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0)]
    avg.index = pd.DatetimeIndex(index)
    # Round to 2 decimal places
    avg = avg.round(2)
    print('avg: ', avg)

    avg_file = path.join(DIRNAME, 'data/avg_temp_per_boot.csv')
    # If this is not the first run the file should exist already
    if path.exists(avg_file):
        not_first_boot = True
        print('avg file exists')
        header_val = False # so don't write more header lines into it
    else:
        not_first_boot = False # if it's the first boot create the file
        print('Creating avg_temp_per_boot.csv file')
        header_val = True
    # Save the averages to a csv
    avg.to_csv(avg_file, mode='a', index=True, header=header_val)

    if not_first_boot:
        # If the file already exists, calculate rate of temperature
        # change per hour by comparing temperature per pixel to prior
        # saved values
        last_rows = pd.read_csv(avg_file, index_col=0).tail(2)
        print('last two rows: ', last_rows)
        hourly_rate(last_rows)

def hourly_rate(rows):
    change_per_hour = pd.DataFrame()

    for column in rows:
        # Save absolute values only, doesn't matter if temperature
        # change is up or down, just need the rate temps are changing
        value = abs(rows[column].diff().tail(1))
        change_per_hour[f'Change {column}'] = value

    print('change per hour: ', change_per_hour)
    # Save the change rate data
    time_val = datetime.now().strftime('%Y%m%d-%H%M')
    change_per_hour_file = path.join(DIRNAME, f'data/change_per_hour-{time_val}.csv')
    change_per_hour.to_csv(change_per_hour_file, index=True, header=True)

    # Round values to improve readability of generated median values
    change_per_hour = change_per_hour.round(2)
    change_list = []
    # Store values in a list for easy comparisons
    for column in change_per_hour:
        change_list.append(change_per_hour[column].values[0])

    # Get the median rate of change over the last run for the whole frame
    med_value = median(change_list)

    # Get the median rate of the reference pixels in the center of 
    # the frame so all other pixels can be compared to this value, 
    # assuming the center of the frame is constantly water
    ref_value = median([change_list[191], change_list[192], change_list[193]])
    print('Ref value is: ', ref_value)

    # Split pixels by their change rate compared to the median of the 
    # whole frame or the median of three reference pixels in the center
    # If a given pixels rate of temperature change is less than the
    # value it is being compared to, assume it is water
    # If the rate of change is higher assume the pixel is land
    extent = []
    for pixel in change_list:
        if pixel < ref_value:
            extent.append(0) # water
        else:
            extent.append(1) # land

    extent_file = path.join(DIRNAME, f'data/extent-processed{time_val}.p')
    extent_text = path.join(DIRNAME, f'data/extent-processed{time_val}.txt')

    print('Extent: ', extent)
    # Save uncompressed extent file
    with open(extent_text, 'w') as file_handler:
        file_handler.write(''.join(map(str, extent)))

    # Compressed pickle file for transmission over Lora radio
    # Ideally fits into a single packet to improve reliability of
    # transmission at long range or in bad conditions
    dump(extent, extent_file, compression='bz2')

def main():
    # List to save frames into for concatination in Pandas DF outside 
    # the loop. Concatinating inside a loop is slow
    frames = []
    # Create list for thermal sensor library to save the frame into
    data = [None]*RESOLUTION

    # Save multiple frames every boot so we can average out temperature
    # readings to account for any errors in the sensor
    for i in range(LIMIT):
        try:
            MLX.getFrame(data) # MLX90640 specific
        except ValueError:
            print('Frame error!!!')
            continue # retry

        # Create Pandas series using the list we saved the frame to
        # Set the name of the series to the current timestamp
        frame = pd.Series(data, name=pd.to_datetime('now').tz_localize(
            pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0))

        print(frame)

        # Instead of appending directly to DF, append to list for later
        # concatination into the DF so we're not iteratively altering
        # the DF which would be inefficient
        frames.append(frame.to_frame().T)

        # Run external script to take a photo with raspistill tool
        # Photos are saved into the images folder
        print('saving photo...')
        subprocess.run([path.join(DIRNAME, 'pic.sh')], check=True)

        # Pause until the next frame
        if i < LIMIT:
            sleep(INTERVAL)
    
    # Concat frames into global DataFrame
    global DF
    DF = pd.concat(frames)
        
if __name__ == '__main__':
    import sys
    sys.exit(main())
