#!/bin/sh

DATE=$(date +"%Y-%m-%d_%H%M")

raspistill -vf -hf -o /home/pi/images/$DATE.jpg
