#!/bin/sh
echo "Start VLC on your computer and connect to network stream tcp/h264://RASPBERRY_IP_ADDRESS:8888   -- change the IP address to the Pi's address or hostname"

libcamera-vid -t 0 --inline --listen -o tcp://0.0.0.0:8888
