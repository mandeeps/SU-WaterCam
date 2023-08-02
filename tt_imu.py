from ticktalkpython.SQ import STREAMify

@STREAMify
def get(trigger):
    import time
    import logging
    import board
    import adafruit_bno055
    i2c = board.I2C()
    sensor = adafruit_bno055.BNO055_I2C(i2c)

    time.sleep(1)
    print(sensor.euler)
    return sensor.euler 
