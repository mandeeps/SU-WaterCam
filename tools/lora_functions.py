#!/usr/bin/env python

# https://github.com/oliveiraleo/LoRa-RSSI-Grabber/blob/master/send_control_packets.py
import re
import time
import serial

def returnFilteredINTs(data: str) -> list:
    """Extract all integer values from an AT command response string."""
    return [int(x) for x in re.findall(r'\d+', data)]


class LoraEndDevice:
    def __init__(self):

        self.loraSerial = serial.Serial()
        self.loraSerial.port = '/dev/ttyAMA1'
        self.loraSerial.baudrate = 115200
        self.loraSerial.bytesize = 8
        self.loraSerial.parity='N'
        self.loraSerial.stopbits=1
        self.loraSerial.timeout=2
        self.loraSerial.rtscts=False
        self.loraSerial.xonxoff=False

        self.lastAtCmdRx = ''

    def setPortCom(self, newPort):
        self.loraSerial.port = newPort

    def openSerialPort(self):
        self.loraSerial.open()

    def closeSerialPort(self):
        self.loraSerial.close()

    # resets the serial connection
    def resetSerialPort(self):
        #it clears the connection buffer
        self.closeSerialPort()
        time.sleep(2)
        self.openSerialPort()

    # sends a command to the device
    def sendCmdAt(self,cmd):
        if self.loraSerial.is_open:
            self.loraSerial.write(cmd.encode())
        else:
            print("[ERROR] It\'s not possible to communicate with LoRa module!")

    def getAtAnswer(self):
        self.lastAtCmdRx = self.loraSerial.read(100)

    # prints the answer of device's serial port (i.e. the messages you see when using minicom)
    def printLstAnswer(self):
        print(self.lastAtCmdRx.decode('UTF-8'))
    
    # gets the answer of device's serial port (i.e. the messages you see when using minicom)
    def getLstAnswer(self):
        data = self.lastAtCmdRx.decode('UTF-8')
        return data

    # sends a command via serial port
    def sendMessage(self, msg):
        msg = '{}\r\n'.format(msg)
        self.sendCmdAt(msg)
        self.getAtAnswer()
    
    def sendPacketToGateway(self, message):
        cmd = 'AT+SEND=' + str(message) + '\r\n'
        self.sendMessage(cmd)
        # self.printLstAnswer() #DEBUG

    def sendJoinRequest(self):
        self.sendMessage('AT+JOIN\r\n')
        # self.printLstAnswer() #DEBUG

    def checkJoinStatus(self):
        self.sendMessage('AT+NJS?\r\n')
        # self.printLstAnswer() #DEBUG
        answer_data = self.getLstAnswer()
        data = returnFilteredINTs(answer_data)
        try:
            status = data[0]
            if status == 0:
                return False
            elif status == 1:
                return True
        except (serial.SerialException, OSError, IndexError):
            print("[ERROR] Error acquiring join status! Please, check the serial connection")
            return None
