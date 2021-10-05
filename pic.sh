#!/bin/sh

DATE=$(date +"%Y-%m-%d_%H%M%S")

raspistill -vf -hf -o /home/pi/SU-WaterCam/images/$DATE.jpg
