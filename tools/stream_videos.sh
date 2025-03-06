#!/bin/sh
# Simple script to launch both optical camera and thermal camera streaming servers
# Access normal video on <address>:8080 and thermal on <address>:3000 
# You can use the address or hostname Tailscale gives you for this device 
# Open two different browser windows
python video.py &
/home/pi/git/leptonic/bin/leptonic /dev/spidev0.0 &
cd /home/pi/git/leptonic/frontend && npm start
