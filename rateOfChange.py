#!/usr/bin/python3
# store temp readings as pandas Series, store Series in DataFrame, 
# calc change and rate of change per pixel vs previous measurement

import board
import busio
import adafruit_mlx90640
import pandas as pd

i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_1_HZ

df = pd.DataFrame()

for i in range(10):
    frame = pd.Series([], name = pd.to_datetime('now').replace(microsecond=0))
    try:
        mlx.getFrame(frame)
    except ValueError:
        continue #retry
    
    print(frame)
    df = pd.concat([df, frame.to_frame().T])
# TODO add frame values to DataFrame outside loop to reduce computation

deriv = pd.DataFrame()
for column in df:
    deriv['%s Change' % column] = df[column].diff()
    #print('Change', df[column].diff())    
    deriv['%s Rate of Change' % column] = df[column].diff()
    #print('Rate of Change', df[column].diff().diff())
    
# export DataFrame for calculations on workstation
df.to_csv('data-%s.csv' % pd.to_datetime('now').strftime('%Y%m%d-%H%M%S'))
deriv.to_csv('deriv-%s.csv' % pd.to_datetime('now').strftime('%Y%m%d-%H%M%S'))

# export DataFrame in hd5 format
#store = pd.HDFStore('%s.h5' % pd.to_datetime('now').strftime('%Y%m%d-%H%M%S'))
#store['df'] = df
