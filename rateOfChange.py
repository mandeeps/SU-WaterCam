#!/usr/bin/env python3
# store temp readings as pandas Series, store Series in DataFrame,
# calc changes, rate of change per pixel, mean of change rates

#import atexit
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
# hardware
import board
import busio
import adafruit_mlx90640
#from picamera import PiCamera

I2C = busio.I2C(board.SCL, board.SDA, frequency=400000)
MLX = adafruit_mlx90640.MLX90640(I2C)
MLX.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
DIRNAME = '/home/pi/HotWaterCam/' #os.getcwd()
DF = pd.DataFrame()

# user configurable values
TIMEZONE = pytz.timezone('US/Eastern')
INTERVAL = 6 # time delay between each reading
LIMIT = 10 # max number of frames to take
# picamera values
#camera = PiCamera()
#camera.rotation = 180
#camera.resolution = (2592, 1944)
#camera.framerate = 15
RUN = True

# exit handler
#@atexit.register
#def close():
    # to run on shutdown
#    print('exit received, saving and shutting down')
    #save()

def save():
    ### save data for later processing on workstation ###
    deriv = pd.DataFrame()
    rates = []
    global DF

    for column in DF:
        deriv['Change %s' % column] = abs(DF[column].diff())
        deriv['Rate of Change %s' % column] = abs(DF[column].diff()) \
                        / DF.index.to_series().diff().dt.total_seconds()

    # round values for readability
    DF = DF.round(2)
    deriv = deriv.round(2)

    # calculate mean of rate of change per pixel
    rates = deriv.loc[:, deriv.columns.str.contains('Rate')]
    change = []
    for column in rates:
        change.append(rates[column].mean())

    # local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M')

    data_file = os.path.join(DIRNAME, 'data/data-%s.csv' % time_val)
    deriv_file = os.path.join(DIRNAME, 'data/deriv-%s.csv' % time_val)
    change_file = os.path.join(DIRNAME, 'data/change-%s.p' % time_val)

    # export DataFrames in csv format, and the rates list with pickle
    print('Writing to: %s, %s, %s' % (data_file, deriv_file, change_file))
    DF.to_csv(data_file, index=True, header=True)
    deriv.to_csv(deriv_file, index=True, header=True)
    # compressed pickle file for transmission over Lora radio
    with lzma.open(change_file, mode='wb') as filehandler:
        pickle.dump(change, filehandler)

def main():
    for i in range(LIMIT):
    #while RUN:
        data = [None]*768
        try:
            MLX.getFrame(data)
            frame = pd.Series(data, name=pd.to_datetime('now').tz_localize(
            pytz.utc).tz_convert(TIMEZONE).replace(microsecond=0))
        except ValueError:
            print('mlx frame error!!!')
            continue #retry

        print(frame)

        global DF
        DF = pd.concat([DF, frame.to_frame().T])

        #script to take a photo with raspistill, save to images folder
        print('saving photo...')
        subprocess.run([os.path.join(DIRNAME, 'pic.sh')], check=True)

        # use picamera to take a photo, save to images folder
        #time_val = datetime.now().strftime('%Y%m%d-%H%M')
        #imageFile = os.path.join(DIRNAME, 'images/image-%s.jpg' % time_val)
        #camera.annotate_text = time_val
        #print('saving photo...')
        #camera.capture(imageFile)

        # pause until next frame
        if i < LIMIT:
            sleep(INTERVAL)

    # save recorded data after cycle completes
    save()

if __name__ == '__main__':
    import sys
    sys.exit(main())
