#!/usr/bin/env python3
# store temp readings as pandas Series, store Series in DataFrame, 
# calc change and rate of change per pixel

import board
import busio
import adafruit_mlx90640
import pandas as pd
from time import time, sleep
from datetime import datetime
import atexit
import pickle
from picamera import PiCamera
import lzma
import os
import pytz

i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
df = pd.DataFrame()
dirname = os.getcwd()
camera = PiCamera()
camera.rotation = 180

# configurable values
timezone = pytz.timezone('US/Eastern')
interval = 60 # time delay between each reading
limit = 100 # number of frames to take
running = True

# exit handler
@atexit.register
def close():
    # to run on shutdown
    print('exit received, saving and shutting down')
    save()

def save():
    ### save data for calculations on workstation ###
    global df
    deriv = pd.DataFrame()
    rates = []
    
    for column in df:
        deriv['Change %s' % column] = df[column].diff()
        deriv['Rate of Change %s' % column] = df[column].diff() / df.index.to_series().diff().dt.total_seconds()

    # round values for readability
    df = df.round(2)
    deriv = deriv.round(2)
    
    # calculate mean of rate of change per pixel
    rates = deriv.loc[:, deriv.columns.str.contains('Rate')]
    change = []
    for column in rates:
        change.append(rates[column].mean())
        
    # local timezone
    timeValue = datetime.now().strftime('%Y%m%d-%H%M')
    
    dataFile = os.path.join(dirname, 'data/data-%s.csv' % timeValue) 
    derivFile = os.path.join(dirname, 'data/deriv-%s.csv' % timeValue)
    changeFile = os.path.join(dirname, 'data/change-rates-%s.p' % timeValue)

    # export DataFrames in csv format, and the rates list with pickle
    print('Writing data to %s, %s, and %s' % (dataFile, derivFile, changeFile))
    df.to_csv(dataFile)
    deriv.to_csv(derivFile)
    # compressed pickle file for transmission over Lora radio
    with lzma.open(changeFile, 'wb') as filehandler:
        pickle.dump(change, filehandler)

def main(argv):
    #for i in range(limit):
    while running:
        frame = pd.Series([], name = pd.to_datetime('now').tz_localize(pytz.utc).tz_convert(timezone).replace(microsecond=0))
        try:
            mlx.getFrame(frame)
        except ValueError:
            continue #retry
        
        print(frame)
        global df
        df = pd.concat([df, frame.to_frame().T])
        
        # call script to take a photo with raspistill, save to images folder
        #subprocess.run(['/home/pi/pic.sh']) 
        
        # use picamera to take a photo, save to images folder
        timeValue = datetime.now().strftime('%Y%m%d-%H%M')
        imageFile = os.path.join(dirname, 'images/image-%s.jpg' % timeValue)
        camera.annotate_text = timeValue
        camera.capture(imageFile)
        
        # pause until next frame
        sleep(interval)

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
