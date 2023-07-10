# Build Guide / How To

### Project requires a Raspberry Pi 
or compatible single board computer with GPIO, I2C, SPI etc.,

## Parts List

* Case

    https://www.amazon.com/LMioEtool-Dustproof-Waterproof-Electrical-Transparent/dp/B07PK8K8S2

* Raspberry Pi Zero for original PoC
    https://www.adafruit.com/product/2885

    Raspberry Pi Zero 2, 3B, or 4 for 64-bit compatibility and using PyTorch
    
* Optical Camera

    https://www.amazon.com/Dorhea-Raspberry-Camera-Automatic-Adjustable/dp/B07DNSSDGG/ref=pd_bxgy_img_2/136-0027955-5919373?pd_rd_w=2vD2j&pf_rd_p=fd3ebcd0-c1a2-44cf-aba2-bbf4810b3732&pf_rd_r=GYFEFWF9TX4TQ2CJAENW&pd_rd_r=47fda39a-7777-432d-9d94-af7113524711&pd_rd_wg=zb2az&pd_rd_i=B07DNSSDGG&psc=1

    If using a Pi Zero make sure to get a camera cable that will fit the smaller
    CSI connector on the Zero!
    https://www.adafruit.com/product/3157

* WittyPi 3 Mini

    https://www.adafruit.com/product/2223
    
    Witty Pi 4 supports 3A output for the Raspberry Pi 4, currently testing if 3 is sufficient for our needs
    
* Stacking header to attach WittyPi to RPi and still access pins for peripherals

    https://www.adafruit.com/product/5038

* Adafruit MLX90640 Thermal Sensor

    https://www.adafruit.com/product/4407

    or

    https://www.adafruit.com/product/4469

    or better, such as Flir Lepton
    
* Flir Lepton and breakout board

    Breakout board 2.0: https://www.mouser.com/ProductDetail/Teledyne-FLIR-Lepton/250-0577-00?qs=DRkmTr78QARne0IUCYtsyA%3D%3D
    Lepton 3.5: https://www.mouser.com/ProductDetail/Teledyne-FLIR-Lepton/500-0771-01?qs=DRkmTr78QAQNv%2FBEKfCn%252BQ%3D%3D

* Stemma QT cables to connect Adafruit sensors

    https://www.adafruit.com/product/4397

* Battery

    https://voltaicsystems.com/v50/ or other battery.

    We are using Voltaic battery packs because they do not auto-shutdown during low power draw, which is important for this system as it will be in low-power mode most of the time and losing power then would prevent it from starting back up.

    Get the appropriate cables to connect whatever battery you are using to the 
    WittyPi. For the Pi 3B and 4, use a dual USB to single micro Y splitter cable to supply adequate power from the Voltaic pack, like:
    
    https://www.amazon.com/StarTech-com-Cable-External-Drive-Micro/dp/B0047AALW6/ref=psdc_172456_t2_B00L1K1OIA?th=1

    https://www.amazon.com/zdyCGTime-Adapter-Charging-Samsung-Extension/dp/B07QTQ157W/ref=sr_1_11?keywords=dual+micro+usb+adapter&sr=8-11
    
    If using a plain LiPo battery instead of a USB power bank like 
    the Voltaic get a charger that will also let you connect the battery to 
    the microUSB port on the WittyPi like:

    https://www.adafruit.com/product/2465

    Or connect the LiPo to the exposed WittyPi power pads with jumper cables following the instructions in the WittyPi manual. If using the Adafruit USB charger you'll need to solder the included USB header on or else solder a micro USB cable directly to the output so it can be connected to the WittyPi.
    
    If you use a regular USB power bank / portable phone charger you'll need
    to adjust some WittyPi settings so it draws more power when the RPi is off to
    avoid the power bank shutting off all power. This will decrease battery life. 

* MicroSD cards
* Micro USB cables, preferably 2 if using a Pi Zero so you can configure it as a network device and login over the data USB, with the other powering the WittyPi.

* Ethernet cable for Pi 3B, 4 - so you can connect to a network for updates and initial configuration. I shared the wifi connection on my laptop with the Pi over ethernet using Network Manager's connection sharing on Linux. Other operating systems have similar functionality.

* Serial TTL cable - useful for logging into the Pi before networking has been configured, and for debugging.

    https://www.adafruit.com/product/954
    or similar serial cable.

* Cellular modem
    https://www.amazon.com/Quectel-LTE-EC25-AF-Mini-PCIe/dp/B082SL8KY1
    
    https://www.amazon.com/gp/product/B07YY5967K

## Raspberry Pi 4 with Flir camera, IMU, and Quectel Cellular modem+GPS
Ideally we will have an image that can be flashed onto an SD card for new builds.

Installation from scratch: Use Raspberry Pi Imager to install current stable 64-bit Raspberry Pi OS lite to a microSD card with SSH enabled in the configuration options, along with the user account name and password, and configure a unique hostname for each system that makes sense (like the installation location) 

https://www.raspberrypi.com/software/

After flashing, add enable_uart=1 at the end of the /boot/config.txt file. Insert the SD card in the Pi, attach power and boot it up.

https://www.jeffgeerling.com/blog/2021/attaching-raspberry-pis-serial-console-uart-debugging

Use a serial cable to connect to the console and use sudo raspi-config to configure the device settings (locale, timezone, predictable network names, etc.,) and select Network Manager in place of dhcpcd in networking settings.
https://learn.adafruit.com/adafruits-raspberry-pi-lesson-5-using-a-console-cable/software-installation-windows

Leave GPU memory at the default of 32 MB, PiCamera2 will not need more and the camera will not work with less.

Use sudo nmtui to configure the ethernet connection to a static IP, with your computer IP as the gateway and DNS server if you are sharing your Internet connection with the Pi. Otherwise configure for whatever network setup you have.

Now you can use ssh to login to the Pi after connecting it to your computer with an ethernet cable. Connection sharing can be setup using Network Manager on a Linux computer, or Windows Connection sharing, or the macOS equivalent.

Once you've logged in and are sharing an internet connection from your computer to the Pi, run sudo apt update and sudo apt upgrade

Verify the Pi is on the latest firmware with rpi-eeprom-update.

Helpful tools: sudo apt install git tmux
Install your preferred editor, which should be neovim, and aptitude if you want a TUI for apt

Set /boot/config.txt options:
### Disable LEDs to save a little power
dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
dtparam=pwr_led_activelow=off

To disable ethernet LEDs on Pi 3 & 4 try:

dtparam=eth_led0=4
dtparam=eth_led1=4

or:

dtparam=eth_led0=14
dtparam=eth_led1=14

### disable audio
dtparam=audio=off

### disable wireless, we won't use WiFi or Bluetooth
dtoverlay=disable-wifi
dtoverlay=disable-bt
dtoverlay=pi3-disable-wifi
dtoverlay=pi3-disable-bt

### I2C clock stretching for BNO055 IMU
dtparam=i2c_arm_baudrate=10000

if there are issues with taking high-resolution images use the vc4-kms-v3d driver with options: dtoverlay=vc4-kms-v3d,cma-320

Can also add nohdmi to the vc4-kms-v3d line to disable HDMI ports and save ~30mA

### Add to /boot/cmdline.txt
spidev.bufsiz=131072        - for Flir camera

### SD Card settings
Disable swap and set noatime to prolong SD card life: sudo swapoff --all, sudo apt purge dphys-swapfile.

Add noatime,commit=60 settings to ext4 partitions in /etc/fstab - noatime prevents writing access times to files, commit collects and delays writes to every N seconds. Data loss will be limited to the last N seconds of writes if power is lost. Do NOT change the /boot partition settings, it is a vfat filesystem and these options will not work and will cause the Pi to not boot.

Set temp directories like /tmp, /var/tmp to mount in RAM, ex. tmpfs /var/tmp tmpfs nodev,nosuid,size=20M 0 0 in fstab

Use a larger SD card size than needed so you have free space for wear-leveling.

### Power Management
Next, configure the WittyPi 3 Mini for power management.
Download: wget http://www.uugear.com/repo/WittyPi3/install.sh
Install: sudo sh install.sh
Reboot, then run wittyPi.sh from the wittypi directory to configure the schedule.

### SU-WaterCam setup
Clone the public git repo: https://github.com/mandeeps/SU-WaterCam.git
Compile lepton.c and capture.c for the device. cd to the SU-WaterCam/tools directory and run: 
gcc lepton.c -o lepton
gcc capture.c -o capture

Copy to the root of the SU-WaterCam directory. From tools, run "cp lepton ../." and "cp capture ../."

use apt to install these packages: sudo apt install libgpiod-dev python3-pandas python3-dev python3-venv exempi python3-wheel python3-picamera2

We need to use virtual environments for Python on Debian-derivatives like Raspberry Pi OS starting with Debian 12 (codenamed Bookworm). 
As of 6-20-23 Debian 11 remains the current stable base for Raspberry Pi OS, but let's future proof by using a venv now.
Create a virtual environment with python -m venv --system-site-packages /home/pi/SU-WaterCam/venv, (we use system-site-packages to copy over pandas and other modules)
activate with source /home/pi/SU-WaterCam/venv/bin/activate, and then install modules with pip install -r /home/pi/SU-WaterCam/requirements.txt
or manually with pip install compress_pickle adafruit-blinka gpiozero piexif py-gpsd2 python-xmp-toolkit

If using MLX90640 thermal sensor also install: adafruit_circuitpython-mlx90640.
If using MPU6050 IMU install: adafruit_circuitpython_mpu6050.
If using BNO055 IMU: pip install adafruit-circuitpython-bno055 in the venv.

### If using Adafruit MPU6050 IMU:
Change the WittyPi 3 i2c address to avoid a conflict. The WittyPi 3 uses 0x68 for the RTC, and 0x69 for its microcontroller. The RTC address cannot be changed, but the microcontroller address can. The MPU6050 uses 0x68 by default. First solder the connection on the back of the MPU6050 board to change its i2c address to 0x69. Then change the WittyPi 3 microcontroller i2c address to something else, like 0x70 by following the instructions in the manual. WittyPi 4 does not require this.

Change the Witty Pi 3 microcontroller I2C address: i2cset -y 1 0x69 9 0x70

Edit the utilities.sh file in the wittypi directory and change I2C_MC_ADDRESS=0x69 to 0x70

Then shutdown the system: sudo halt

Disconnect the power to the wittypi 3, reconnect it, start the system up

Check the i2c settings changed: i2cdetect -y 1

Run the wittypi script to verify ./wittypi/wittyPi.sh

Note changing the microcontroller address seems to interfere with displaying power draw using wittyPi.sh despite having changed the address in utilities.sh

now install adafruit_circuitpython_mpu6050 to use the MPU6050 in Python

### Adafruit BNO055 IMU
pip install adafruit-circuitpython-bno055 in the venv

### Calibrate the IMU prior to use:
With the IMU stable and flat, run the mpu6050 calibration script to save offset values.

TODO: Calibrate BNO055

### Quectel EC25 Modem and GPS
sudo apt install gpsd gpsd-clients

Remove and purge udhcpcd and openresolv: sudo apt purge udhcpcd openresolv
Reconfigure current network devices with network manager to retain local networking during setup - sudo nmtui is easiest way
Make sure /etc/network/interfaces has no references to devices you want NM to manage

Then configure the cellular modem and verify everything works as expected after restarts
sudo mmcli -m 0 --simple-connect='apn=iot.1nce.net' Replace apn as appropriate

Setup connection with NetworkManager: sudo nmcli c add type gsm ifname cdc-wdm0 con-name Quectel apn iot.1nce.net

On Bookworm:
sudo mmcli -m 0 –-location-enable-gps-unmanaged -- to tell ModemManager to start the GPS on the Quectel EC25 but not control it, so gpsd can manage it instead
Enable gps.service in the git config directory so this will be done automatically on boot.

Bullseye: ModemManager on Bullseye doesn't support --location-enable-gps-unmanaged for the Quectel EC25 apparently, and since RaspberryPi OS has not officially released a Bookworm-based version yet, we are using Bullseye and working around this by creating a custom Udev rule to tell ModemManager to ignore the GPS:

create file /etc/udev/rules.d/77-mm-quectel-ignore-gps.rules
with contents: ATTRS{idVendor}=="2c7c", ATTRS{idProduct}=="0125", SUBSYSTEM=="tty", ENV{ID_MM_PORT_IGNORE}="1"

Save this and run sudo udevadm control --reload

sudo udevadm trigger

If using a different cellular modem change the ids to the appropriate ones, use lsusb to lookup the ids.

Might need to reboot before next step...

Activate the GPS and enable autostart for future use -

install minicom if not already available and run it:
minicom -b 9600 -D /dev/ttyUSB2

(ttyUSB2 is the AT port for the Quectel. ttyUSB1 is the GPS output port)

In minicom, issue the following AT commands -

Enable NMEA:
AT+QGPSCFG="nmeasrc",1

Enable Autostart:
AT+QGPSCFG="autogps",1

Turn GPS on:
AT+QGPS=1

Assisted location fix:
AT+QGPSXTRA=1

Quit minicom with ctrl-a, x
These should be saved to the device's NVRAM so this should only need to be done once.

Now edit /etc/default/gpsd to set the correct gps device, in this case /dev/ttyUSB1

Then in python we can get gps data with py-gpsd2:
import gpsd2
gpsd2.connect()
packet = gpsd2.get_current()
print(packet.position())

If there are issues getting a fast location fix try updating the XTRA assist data by downloading a new xtra2.bin from xtrapath4.izatcloud.net/xtra2.bin and uploading it to the modem with sudo mmcli -m 0 --location-inject-assistance-data=xtra2.bin

sources:
https://sigquit.wordpress.com/2012/03/29/enabling-gps-location-in-modemmanager/
Stackoverflow mirror: https://code.whatever.social/questions/6146131/python-gps-module-reading-latest-gps-data

### Flir Lepton Breakout board wiring
8 female-female jumper cables needed.
At least 2 should be splitters to share I2C with other devices.

Orient the back of the breakout board towards yourself. The front is the side with the socket for the Lepton camera.
Let's call the pins that are closest to you pins 1 through 10, starting from the left and going to the right. Right is the side with the mini ZIF connector on top (the white plastic bit above the QR code sticker)
Let's call the pins on the bottom (away from you) pins A through J
Pin 1 is for power, so wire that to the 3.3V power pin on the Pi. See the Flir Lepton Wiring image for help.
We can use the top pin of the two pins without jumpers on the back of the board (side towards you right now) for ground. In other words, the pin with nothing covering it that is closest to the white ZIF socket towards the top is the ground pin, so connect it to the ground pin on the Pi.

TODO: upload photos of Lepton wiring

Because we need I2C for other peripherals, use splitter cables for the two I2C pins (SDA and SCL) on the Pi. So get or make two cables that each have a female header on one end and a male and female header on the other end. One female end connects to a pin on the Raspberry Pi GPIO header, and the other two ends are for the Flir breakout board and a peripheral like the Adafruit IMU. Another pair of split cables is useful for 3.3V and ground.

The SDA pin on the Pi (pin #3) will connect to pin C on the breakout board (side away from you) - use a splitter

The SCL pin on the Pi (pin #5) will connect to pin 4 on the breakout board (side towards you) - use a splitter

MOSI on the Pi (pin #19) connects to pin E on the breakout

MISO on the Pi (pin #21) connects to pin 6 on the breakout board

CLK pin on the Pi (pin #23) will connect to pin D on the breakout

CS pin (pin #24, right across from CLK, aka CE0, GPIO 8) connects to pin 5 on the breakout board

The VSYNC pin is optional and we are not using it but it would be Pin #11, GPIO 17 on the Pi connected to pin H on the breakout board.

Insert the Flir camera into the breakout board.
Check everything is correct by running the capture and lepton binaries in SU-WaterCam. Rename or copy the appropriate 32 or 64-bit binaries to "lepton" and "capture" and then run: ./capture

Examine the created files to verify things are working.

Binaries are from https://github.com/lukevanhorn/Lepton3

Thanks Luke Van Horn! Also, thanks to Max Lipitz for the tip about the output containing the temperature values in degrees Kelvin.

### Multitech mDot LoRa module
The default mDot firmware is set up for UART. Remove "console=serial0,115200 console=tty1" from /boot/cmdline.txt on the Pi if you've been using a serial connection to the Pi for debugging. Reboot the Pi for this to take effect.

The power pin (VOD, pin # 1) on the mDot can be connected to the 5V power pin on the Pi. Connect ground (pin 10 on the mDot) to a free ground pin. Connect the mDot UART TX (transmit, pin #2) to the Pi RX (receive) pin (#10), and the mDot RX pin (#3) to the Pi TX pin (#8).

For deployment we'll want the mDot to have a seperate power source so we can remotely trigger it to signal the WittyPi to boot up the system and record data.

On the Pi, run sudo minicom -s -D /dev/serial0 to connect to the mDot and issue AT commands. Use the settings specified in the mDot manual: 

Baud rate 115200
Data bits 8
Parity N
Stop bits 1
Hardware/software flow control off

TODO: the WittyPi can tell if the Raspberry Pi is off by reading the TX pin, which should be set low. The mDot seems to interfere with this, keeping the TX pin on the Pi set high and preventing the WittyPi from cutting off power to the system. There are several possible workarounds for this:

1 - Use something other than the WittyPi. We already have several and it works well otherwise, so this is not a good option.

2 - Use something other than the mDot. We also have several of these, so this is not an ideal option.

3 - Use a USB-Serial adapter to connect the mDot to the Pi instead of the UART pins. This would increase power draw and add another component to the build, so it is not ideal but better than the first two.

4 - Modify the mDot firmware to keep its RX pin low. We do not have a devkit for this device so this is not possible right now. The same applies to modifying the firmware to use I2C instead. Maybe we could use a resistor to connect to ground?

5 - Turn on alternative UART pins on the Pi 4 and use those to connect to the mDot instead of the pin the WittyPi reads to determine the Pi's state. This is the option I am currently looking into. Potential downside is reduced performance if we use mini-UART and have to disable variable clock rates. Upside is we can restore the serial console for debugging and use the mDot at the same time.

By adding dtoverlay=uart3 to /boot/config.txt on a Pi 4 we can use pin 7 for TX and pin 29 for RX.

The non-4 models only expose one UART on GPIO at a time, so using the USB adapter may be the only real option for those. For a Pi 3 or other non-4, connect the Adafruit USB-serial adapter to the USB port. Connect the mDot TX to the white RX header, and the mDot RX to the green TX header. Use minicom or screen with the settings listed in the mDot manual to connect to the appropriate device, it will be /dev/ttyUSBx where x is a number. Check dmesg to see what device the adapter is: dmesg | grep "cp210x"

On the Pi 4, make sure "enable_uart=1" is in the /boot/config.txt file, and add dtoverlay=uart5. Save and reboot. Connect the TX pin on the mDot to pin #33 on the Pi and connect the RX pin on the mDot to pin #32 on the Pi. Check with minicom or screen connecting to /dev/ttyAMA1 with the appropriate settings from the manual.


### Tailscale for remote login over cellular data
https://tailscale.com/download

Install and use mosh for high-latency cellular connections
sudo apt install mosh

Use an appropriate client for your own device.

### Remote Video Streaming
with libcamera-apps-lite installed run:

libcamera-vid -t 0 --inline --listen -o tcp://0.0.0.0:8888

On your machine connected to the Pi (over Tailscale or directly) use VLC to stream the video using 'open network stream' and enter tcp/h264://PI_ADDRESS_OR_HOSTNAME:8888 with the appropriate IP address and port number. 

### Pytorch
pip install torch torchvision (in the venv)

## Old Pi Zero 32-bit Instructions 
Written assuming you are using a Raspberry Pi Zero with headers installed
and the Adafruit MLX90640 sensor

Flash the provided disk image onto the microSD card if not already done

If installing regular Raspbian/RaspberryPi OS image from scratch:
in raspi-config set timezone, enable camera, ssh and i2c, set static IP address, reduce GPU memory to 128 minimum for optical camera, change default password, etc.,
use apt to install python3-pandas and libgpiod-dev: sudo apt install libgpiod-dev python3-pandas

Create a virtual environment with python -m venv --system-site-packages /home/pi/SU-WaterCam/venv, activate with source /home/pi/SU-WaterCam/venv/bin/activate, and then install modules with pip install -r /home/pi/SU-WaterCam/requirements.txt
or manually use pip to install dependencies: python3 -m pip install compress_pickle adafruit-blinka adafruit_circuitpython-mlx90640 gpiozero adafruit_circuitpython_mpu6050

It's easier to connect the optical camera before installing the WittyPi.
Be careful with the connector and the cable, IME ribbon cables and ZIF
sockets are fragile. While the cable can flex it should not be folded. Be careful
you don't break the socket clip by using too much force.
For the RPi Zero you'll want to replace any cable
included with the camera with the correct RPi Zero camera cable.

Insert the larger end of the RPi camera ribbon cable into the optical camera by gently
pulling the black plastic clip out and inserting the cable in, making sure
the exposed side with the gold pins is facing down. Secure the cable by
pusing the plastic clip back in.

Now on the Raspberry Pi end, gently pull forward the black plastic clip on the CSI camera connector.
Insert the cable into the connector making sure the exposed side is down.
Push the black plastic clip back into place.

Install stacking header onto the Raspberry Pi header making sure it is
firmly in place.

Install WittyPi 3 Mini onto the extended headers making sure it is in place.

Once the WittyPi is installed you can connect your thermal sensor.
See the reference Raspberry Pi Zero image

Connect the Stemma QT cable to the Adafruit MLX90640 sensor, either socket works

Connect the red header cable to pin 1, the 3.3v pin on the top left

Connect the blue header cable to pin 3, the GPIO data pin just below pin 1

Connect the yellow header cable to pin 5, the GPIO clock pin just below pin 3

Connect the black header cable to pin 6, the Ground pin just right of pin 5

Make sure your battery is charged.
Connect the battery to the WittyPi micro USB port. Either directly with a 
cable if using a Voltaic power pack, or through the charger if using a lipo battery.
Press the button on the WittyPi to check if everything turns on. Check the LEDs

Log into the Pi and use the WittyPi program to set the schedule (and increase idle power use if using a normal power bank that isn't intended for IoT devices/doesn't have an always-on mode. Voltaic batteries should not need this.)

https://cdn-shop.adafruit.com/product-files/5038/5038_WittyPi3Mini_UserManual.pdf

If you have a computer set up to connect to the RPi Zero using USB-OTG networking

https://learn.adafruit.com/turning-your-raspberry-pi-zero-into-a-usb-gadget/ethernet-gadget

https://artivis.github.io/post/2020/pi-zero/

you can plug a micro USB cable into the data port of the RPi (the middle port of the three)
and the other end into your computer. Assuming your computer configuration is correct you
should be able to use SSH to access the Zero. If you are not using a Zero
you'll need to use a serial connection to connect to the Pi or configure ethernet or wifi networking on the Pi to SSH in.
Or just plug in a monitor and keyboard.
