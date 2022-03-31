#!/usr/bin/env python3
# Send AT commands to an xDot device

import time
import serial
# PySerial config
ser = serial.Serial()
ser.port = '/dev/ttyACM0' # this will vary across systems, could be USB0
ser.baudrate = 115200
ser.bytesize = serial.EIGHTBITS
ser.parity = serial.PARITY_NONE
ser.stopbits = serial.STOPBITS_ONE

try:
    ser.open()
except SerialException as e:
    print('Error opening serial port: ' + str(e))
    exit()
if ser.isOpen():
    try:
        ser.write(b'ATI\r') # must send carriage return 
        time.sleep(0.5) # wait for response
        numLines = 0
        while True:
            response = ser.readline()
            print(response)
            numLines += 1
            if (numLines >= 6):
                break
        ser.close()
    except ValueError as e:
        print('Error: ' + str(e))
else:
    print('Port is closed') 

