#!/bin/sh
# Output a video stream to the specified port on available network interface
# This makes it easier to focus the optical camera
echo "Start VLC on your computer and connect to network stream tcp/h264://RASPBERRY_IP_ADDRESS:8080   -- change the IP address to the Pi's address or hostname -- If you are using Tailscale you can use the hostname it gives you for this device"

libcamera-vid -t 0 --inline --listen -o tcp://0.0.0.0:8080
