[Connect with SSH via USB](https://artivis.github.io/post/2020/pi-zero/)

To connect using SSH: https://learn.adafruit.com/turning-your-raspberry-pi-zero-into-a-usb-gadget/ethernet-gadget
Connect via serial cable: https://learn.adafruit.com/adafruits-raspberry-pi-lesson-5-using-a-console-cable/software-installation-windows

[Adafruit CircuitPython docs](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/installing-circuitpython-on-raspberry-pi)

for direct connection to Adafruit lora radio:
raspi-lora or adafruit-circuitpython-rfm9x or adafruit-circuitpython-tinylora
[Adafruit Lora radios](https://learn.adafruit.com/adafruit-rfm69hcw-and-rfm96-rfm95-rfm98-lora-packet-padio-breakouts/using-the-rfm69-radio)

WittyPi (or equivalent) to schedule on/off cycle for maximum battery life:
https://www.adafruit.com/product/5038

[Ultrasonic distance sensor:](https://www.adafruit.com/product/4007)
python3 -m pip install adafruit-circuitpython-hcsr04
sudo apt install gpiod

[Adafruit GPS](https://www.adafruit.com/product/4415)
sudo python3 -m pip install adafruit-circuitpython-gps
[Documentation](https://cdn-learn.adafruit.com/downloads/pdf/adafruit-mini-gps-pa1010d-module.pdf)

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
2000mAh Adafruit battery lasted ~16 hours powering Rpi, Mlx90640, IRcut camera, adfruit temp sensor, with WittyPi.
Voltaic V50 12,800mAh battery lasts over a week running for ten minutes per hour with WittyPi and no IR LEDs attached.

ToDo:
Implement data transmission over LoRa

Remote login over LoRa?
https://unsigned.io/15-kilometre-ssh-link-with-rnode/

Compress SD image:
https://www.pragmaticlinux.com/2020/12/how-to-clone-your-raspberry-pi-sd-card-in-linux/
https://github.com/Drewsif/PiShrink

Flir Lepton 3.5 with Flir Breakout Board 2.0
Building off these examples:
https://github.com/lukevanhorn/Lepton3
https://github.com/lukevanhorn/Lepton3/issues/4#issuecomment-652940493

Tensorflow Lite from:
https://github.com/prettyflyforabeeguy/tf_lite_on_pi_zero.git
