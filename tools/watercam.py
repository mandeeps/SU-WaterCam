#!/home/pi/SU-WaterCam/venv/bin/python
# Main script for controlling automated WaterCam functions when running in field on a schedule
# When the system wakes up this script will run functions from take_nir_photos to save images and data from the optical and Flir Lepton cameras and metadata from the IMU and GPS

import logging
import time
from time import sleep
from subprocess import call
import take_nir_photos # has functions for Flir Lepton and IR-CUT cameras

def main():
    # setup
    logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s', encoding='utf-8', level=logging.DEBUG)
    last_print = time.monotonic()
    filepath = "/home/pi/SU-WaterCam/images/"

    # Delay between iterations
    interval = 15
    # How many sets of photos to take per iteration
    limit = 10

    for _ in range(limit):
        current = time.monotonic()
        # only proceed to recording data if past interval time
        if current - last_print >= interval:
            last_print = current

            # take photos: optical and NIR
            name, directory = take_nir_photos.main(filepath)

            print(f"Photo: {name}")
            # take FLIR photo and get temperature data from Lepton
            take_nir_photos.flir(directory)
        else:
            sleep(interval)

    # Once the for loop has finished we should be able to trigger a shutdown
    # Use with a WittyPi schedule that turns the system on regularly
    # I am using "doas shutdown" with a /etc/doas.conf configured for user pi 
    call("doas /usr/sbin/shutdown", shell=True)

if __name__ == "__main__":
    main()
