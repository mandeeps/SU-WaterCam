# HotWaterCam
Code to delineate surface water extent extraction from TIR and optical cameras.




### Raspberry Pi Zero configuration Using Raspbian Lite:

in raspi-config set timezone, enable camera, ssh and i2c, set static IP address, reduce GPU memory to 128 minimum for optical camera, change default password, etc., 
[Connect with SSH via USB](https://artivis.github.io/post/2020/pi-zero/)

python3 dependencies(use apt for pandas):
adafruit-blinka adafruit-circuitpython-mlx90640 numpy pandas compress_pickle
adafruit-circuitpython-tinylora
gpiozero, libgpiod-dev
[Adafruit CircuitPython docs](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/installing-circuitpython-on-raspberry-pi)

for direct connection to lora radio:
raspi-lora or adafruit-circuitpython-rfm9x
[Adafruit Lora radios](https://learn.adafruit.com/adafruit-rfm69hcw-and-rfm96-rfm95-rfm98-lora-packet-padio-breakouts/using-the-rfm69-radio)

WittyPi (or equivalent) to schedule on/off cycle for maximum battery life:
https://www.adafruit.com/product/5038

#### power optimization:
[1](https://blues.io/blog/tips-tricks-optimizing-raspberry-pi-power/),
[2](https://raspberry-projects.com/pi/pi-hardware/raspberry-pi-zero/minimising-power-consumption),
[Removing services](https://plone.lucidsolutions.co.nz/hardware/raspberry-pi/3/disable-unwanted-raspbian-services)

power saving by:

    disabling unused hardware:
        edit - /etc/rc.local
            # Disable the HDMI port
            /usr/bin/tvservice -o
            
        edit - /boot/config.txt
            # Disable the Zero's only LED
            dtparam=act_led_trigger=none
            dtparam=act_led_activelow=off
            # Disable wifi and bluetooth
            dtoverlay=disable-wifi
            dtoverlay=disable-bt
            
    disabling services:
        systemctl disable bluetooth
        systemctl disable avahi-daemon
        systemctl disable triggerhappy
        systemctl disable wpa_supplicant     
           
    disabling loading wifi/bluetooth drivers:
        edit - /etc/modprobe.d/raspi-blacklist.conf
            # WiFi
            blacklist brcmfmac
            blacklist brcmutil
            # Bluetooth
            blacklist btbcm
            blacklist hci_uart

Micro-optimizations, not really needed:
Boot time optimization (to reduce time spent turned on)
https://unix.stackexchange.com/questions/239432/systemd-boot-optimization-dev-mmcblk0p2-device#240644
https://www.samplerbox.org/article/fastbootrpi
disable man-db.timer, apt-daily.timer, apt-daily-upgrade.timer
https://unix.stackexchange.com/questions/492221/is-it-safe-to-disable-apt-daily-service


RaspberryPi Resources:
https://gist.github.com/htruong/7df502fb60268eeee5bca21ef3e436eb#file-chroot-to-pi-sh

Notes:
2000mAh Adafruit battery lasted ~16 hours powering Rpi, Mlx90640, IRcut camera, adfruit temp sensor, with WittyPi  
