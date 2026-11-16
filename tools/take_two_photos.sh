#!/bin/sh
./capture &
# Full sensor size, the size the capture workflow and the camera calibration use.
rpicam-still --immediate -n --width 2592 --height 1944 -o "$(date +"%Y_%m_%d_%I_%M_%S_%p").jpg"
