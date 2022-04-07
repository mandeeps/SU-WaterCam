#!/usr/bin/env python3
# Send AT commands to an xDot device

import time
import serial

# PySerial config
ser = serial.Serial(xonxoff=False)
ser.port = '/dev/ttyACM0' # this will vary across systems, could be USB0
ser.baudrate = 115200
ser.bytesize = serial.EIGHTBITS
ser.parity = serial.PARITY_NONE
ser.stopbits = serial.STOPBITS_ONE
time.sleep(0.5) # give it time to open, just to make sure

# function to send AT commands to xDot
def send(msg):
    # open and close port each time
    try:
        ser.open()
    except SerialException as e:
        print('Error opening serial port: ' + str(e))
        exit()
    if ser.isOpen():
        try:
            ser.write(msg) 
            time.sleep(0.5) # wait for response
            # limit device response length
            numLines = 1
            while numLines < 5:
                response = ser.readline()
                print(response)
                numLines += 1
            ser.close()
        except ValueError as e:
            print('Error: ' + str(e))
    else:
        print('Port is closed')

# keep port open for rest of program


# Now we can start passing commands to the xDot
# ATI returns version information from the xDot
send(b'ATI\r\n') # must include carriage return and send as binary
send(b'AT+DI\r\n')
# connect to gateway
send(b'AT+PN=0\r\n') # MTS mode for Conduit connection

# save config to non-volatile memory
send(b'AT&W\r\n')
