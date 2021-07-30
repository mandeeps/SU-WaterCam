#!/usr/bin/env python3
# store temp readings as pandas Series, store Series in DataFrame, 
# calc changes, rate of change per pixel, mean of change rates

# hardware
import board
import busio
import adafruit_mlx90640
#from picamera import PiCamera
# data
import pandas as pd
#import atexit
import pickle
import lzma
import os
import subprocess # if not using picamera call external script
# time
from time import time, sleep
from datetime import datetime
import pytz

i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
dirname = '/home/pi/' #os.getcwd()
df = pd.DataFrame()

# user configurable values
timezone = pytz.timezone('US/Eastern')
interval = 25 # time delay between each reading
limit = 6 # max number of frames to take
# picamera values
#camera = PiCamera()
#camera.rotation = 180
#camera.resolution = (2592, 1944)
#camera.framerate = 15
running = True

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
    global df

    for column in df:
        deriv['Change %s' % column] = df[column].diff()
        deriv['Rate of Change %s' % column] = df[column].diff() \
                        / df.index.to_series().diff().dt.total_seconds()

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
    changeFile = os.path.join(dirname, 'data/change-%s.p' % timeValue)

    # export DataFrames in csv format, and the rates list with pickle
    print('Writing to: %s, %s, %s' % (dataFile, derivFile, changeFile))
    df.to_csv(dataFile)
    deriv.to_csv(derivFile)
    # compressed pickle file for transmission over Lora radio
    with lzma.open(changeFile, 'wb') as filehandler:
        pickle.dump(change, filehandler)

def main(argv):    
    for i in range(limit):
    #while running:
        frame = pd.Series([], name = pd.to_datetime('now').tz_localize(
                  pytz.utc).tz_convert(timezone).replace(microsecond=0))
        try:
            mlx.getFrame(frame)
        except ValueError:
            continue #retry
        
        print(frame)
        
        global df
        df = pd.concat([df, frame.to_frame().T])
        
        #script to take a photo with raspistill, save to images folder
        print('saving photo...')
        subprocess.run(['/home/pi/pic.sh'])
        
        # use picamera to take a photo, save to images folder
        #timeValue = datetime.now().strftime('%Y%m%d-%H%M')
        #imageFile = os.path.join(dirname, 'images/image-%s.jpg' % timeValue)
        #camera.annotate_text = timeValue
        #print('saving photo...')
        #camera.capture(imageFile)
    
        # pause until next frame
        sleep(interval)
    
    # save recorded data after cycle completes 
    save()

if __name__ == '__main__':
    import sys
    sys.exit(main(sys.argv))
