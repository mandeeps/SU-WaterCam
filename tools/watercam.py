#!/home/pi/SU-WaterCam/venv/bin/python
# Main script for controlling automated WaterCam functions when running in field on a schedule
# When the system wakes up this script will run functions from take_nir_photos to save images and data from the optical and Flir Lepton cameras and metadata from the IMU and GPS

import logging
import time
from time import sleep
from subprocess import call
from subprocess import Popen
import take_nir_photos # has functions for IR-CUT camera and Lepton
import coreg_multiple

def main(autostart:bool = True):
    print(f"Will shutdown: {autostart}")
    # setup
    logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s', encoding='utf-8', level=logging.DEBUG)
    last_print = time.monotonic()
    filepath = "/home/pi/SU-WaterCam/images/"

    segformer_location = "/home/pi/git/segformer_5band"
    segformer_python = "/home/pi/miniforge3/envs/5band/bin/python"
    segformer_coreg = "/home/pi/git/segformer_5band/tools/test_no_label.py"

    # Sync time if network available by calling WittyPi script. WittyPi stock software disables other network time software like Chrony and systemd-timesyncd, so either we do time sync their way or use alternative software for the WittyPi 4 like: https://github.com/trackIT-Systems/wittypi4
    #if autostart:
    from witty_pi_4 import WittyPi4
    WittyPi4().sync_time_with_network()

    # Delay between iterations
    interval = 60
    # How many sets of photos (1 NIR, 1 RGB, 1 LWIR) to take per boot
    limit = 5

    for _ in range(limit):
        current = time.monotonic()
        # only proceed to recording data if past interval time
        print(f"Current Time: {current} Prev Time: {last_print}")
        #if current - last_print >= interval:
        last_print = current

            # take photos: optical and NIR
        name, directory = take_nir_photos.main(filepath)

        print(f"Photo: {name}")
            # take FLIR photo and get temperature data from Lepton
        print("Taking Flir captures")
        take_nir_photos.flir(directory)

            # call coregistration script on new photo
        print("Run coreg")
        coreg_multiple.coreg(directory)
            # run 5 band SegFormer on coreg photos
            # Popen([segformer_python, segformer_coreg], cwd=segformer_location)

            # transmit results

#        else:
        sleep(interval)

    if autostart:
    # Once the for loop has finished we should be able to trigger a shutdown
    # Use with a WittyPi schedule that turns the system on regularly
    # I am using "doas shutdown" with a /etc/doas.conf configured for user pi 
        call("doas /usr/sbin/shutdown", shell=True)

if __name__ == "__main__":
    main(True)
