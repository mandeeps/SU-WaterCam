#!/usr/bin/python3
# quick test of adafruit mlx camera
import board
import busio
import adafruit_mlx90640
import pandas as pd

i2c = busio.I2C(board.SCL, board.SDA, frequency=800000)
mlx = adafruit_mlx90640.MLX90640(i2c)
mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_1_HZ

# Read temp from MLX90640 camera, record to pandas dataframes, export
# to csv
frame = [0] * 768

for i in range(3):
    try:
        mlx.getFrame(frame)
    except ValueError:
        #retry
        continue
    
    screen = []
    for row in range(24):
        line = []
        for pixel in range(32):
            temp = frame[row * 32 + pixel]
            print("%0.1f, " % temp, end = "")
            line.append(temp)
        screen.append(line)
            
    df = pd.DataFrame.from_records(screen)
    df.to_csv('data.csv', mode='a')
    
