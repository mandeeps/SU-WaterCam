# HotWaterCam
Code to delineate surface water extent extraction from TIR and optical cameras.




### Raspberry Pi Zero configuration Using Raspbian Lite:

in raspi-config set timezone, enable camera, ssh and i2c, set static IP address, reduce GPU memory from default to minimum 16, change default password, etc., 
[Connect with SSH via USB](https://artivis.github.io/post/2020/pi-zero/)

dependencies:
adafruit-blinka adafruit-circuitpython-mlx90640 numpy pandas
adafruit-circuitpython-tinylora
gpiozero
[Adafruit CircuitPython docs](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/installing-circuitpython-on-raspberry-pi)

for direct connection to lora radio:
adafruit-circuitpython-rfm9x
[Adafruit Lora radios](https://learn.adafruit.com/adafruit-rfm69hcw-and-rfm96-rfm95-rfm98-lora-packet-padio-breakouts/using-the-rfm69-radio)


###### power optimization:
[1](https://blues.io/blog/tips-tricks-optimizing-raspberry-pi-power/),
[2](https://raspberry-projects.com/pi/pi-hardware/raspberry-pi-zero/minimising-power-consumption),
[Removing services](https://plone.lucidsolutions.co.nz/hardware/raspberry-pi/3/disable-unwanted-raspbian-services)

power saving by:
    disabling unused hardware:
        edit - /etc/rc.local
            # Disable the HDMI port (to save power)
            /usr/bin/tvservice -o
        edit - /boot/config.txt
            # Disable the Zero's only LED (to save power)
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
