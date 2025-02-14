#!/bin/sh
# depending on your OS version you may need libcamera-still
rpicam-still --immediate --nopreview -o "$(date +"%Y_%m_%d_%I_%M_%S_%p").jpg"
