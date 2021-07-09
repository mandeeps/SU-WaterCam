#!/usr/bin/env python3
# store temp readings as pandas Series, store Series in DataFrame, 
# calc change and rate of change per pixel vs previous measurement

import board
import busio
import adafruit_mlx90640
import pandas as pd
from time import time, sleep
from datetime import datetime
import atexit
import pickle

i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_2_HZ
interval = 1800 # time delay between each reading
limit = 10 # number of frames to take
running = True
df = pd.DataFrame()

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
    dataFile = 'data-%s.csv' % timeValue 
    derivFile = 'deriv-%s.csv' % timeValue
    changeFile = 'change-rates-%s.p' % timeValue

    # export DataFrames in csv format, and the rates list with pickle
    print('Writing data to %s, %s, and %s' % (dataFile, derivFile, changeFile))
    df.to_csv(dataFile)
    deriv.to_csv(derivFile)
    with open(changeFile, 'wb') as filehandler:
        pickle.dump(change, filehandler)

#for i in range(limit):
while running:
    frame = pd.Series([], name = pd.to_datetime('now').replace(microsecond=0)) # UST timezone
    try:
        mlx.getFrame(frame)
    except ValueError:
        continue #retry
    
    print(frame)
    df = pd.concat([df, frame.to_frame().T])
    
    raspistill -o datetime.now().strftime('%Y%m%d-%H%M')
    # pause until next frame
    sleep(interval)

exit()
