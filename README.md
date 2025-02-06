Prerequisite Knowledge:
Basic Linux, electronics, general computer troubleshooting

Helpful Resources:
Soldering: https://mightyohm.com/files/soldercomic/FullSolderComic_EN.pdf

# Build Guide

## Parts List

- Raspberry Pi 4B

- [Case 150x150x90 mm or larger](https://www.amazon.com/LMioEtool-Dustproof-Waterproof-Electrical-Transparent/dp/B07PK84N5D)

- [Optical Camera w/ controllable NIR filter (IR-CUT)](https://www.amazon.com/Dorhea-Raspberry-Camera-Automatic-Adjustable/dp/B07DNSSDGG/136-0027955-5919373) - should come with a cable to connect to the Pi 4B

- WittyPi 4 power management: [Witty Pi 4 HAT - RTC & Power Management for Raspberry Pi : ID 5704 : Adafruit Industries](https://www.adafruit.com/product/5704)

        Witty Pi 4 supports 3A power output for the Raspberry Pi 4

        [CR2032 Battery for WittyPi 4](https://www.adafruit.com/product/654)

- Stacking header for accessing GPIO after adding custom PCB: [GPIO Stacking Header for Pi A+/B+/Pi 2/Pi 3 [Extra-long 2x20 Pins] : ID 2223 : Adafruit Industries](https://www.adafruit.com/product/2223)

- Flir Lepton 3.5 and Flir Breakout Board v2

        [Flir Breakout board 2.0](https://www.mouser.com/ProductDetail/Teledyne-FLIR-Lepton/250-0577-00?qs=DRkmTr78QARne0IUCYtsyA%3D%3D)
        [Flir Lepton 3.5](https://www.mouser.com/ProductDetail/Teledyne-FLIR-Lepton/500-0771-01?qs=DRkmTr78QAQNv%2FBEKfCn%252BQ%3D%3D)

- [Stemma QT header cables to connect Adafruit sensors to Pi](https://www.adafruit.com/product/4397), plus [Stemma QT to Stemma QT cable](https://www.adafruit.com/product/4210) for connecting Adafruit sensors to other Adafruit sensors

- [Adafruit BNO085 IMU](https://www.adafruit.com/product/4754) - for motion detection / orientation reporting

- [Adafruit AHT20](https://www.adafruit.com/product/4566) Temperature and Humidity Sensor - for monitoring device health

- [Voltaic V50 Battery](https://voltaicsystems.com/v50/) or V75 or other battery

        We are using Voltaic battery packs because they do not auto-shutdown during low power draw, which is important for this system as it will be in low-power mode most of the time and losing power then would prevent it from starting back up. They are intended to be directly charged from 6V solar panels. If using a battery that is not always-on configure the WittyPi to draw more power when idle to avoid losing power.

- [Solar Panels](https://voltaicsystems.com/10-watt-panel-etfe/) - 6 volt panel if charging Voltaic battery pack directly  

- MicroSD cards - preferably higher capacity than needed (at least 64GB), consider "high endurance" or "industrial" (for temperature tolerance) cards: https://www.dzombak.com/blog/2023/12/Choosing-the-right-SD-card-for-your-Pi.html
  
  Example: [SanDisk High Endurance microSD](https://shop.sandisk.com/products/memory-cards/microsd-cards/sandisk-high-endurance-uhs-i-microsd?sku=SDSQQNR-064G-GN6IA)
  
  Test the SD cards with F3 Fight Flash Fraud to verify they are legit: https://github.com/AltraMayor/f3

       TODO: Alternatively boot from USB SSD

* USB C cables

* Ethernet cable - so you can connect to a network for updates and initial configuration. I shared the WiFi connection on my laptop with the Pi over Ethernet using Network Manager's connection sharing on Linux. Other operating systems have similar functionality.

* [Serial TTL USB adapter cable](https://www.adafruit.com/product/954) - useful for logging into the Pi before networking has been configured, and for debugging/troubleshooting.

* Cellular modem with USB Adapter board
  
  [Quectel EC25 Cellular Modem](https://www.amazon.com/Quectel-LTE-EC25-AF-Mini-PCIe/dp/B082SL8KY1)
  
  [Cell Modem USB Carrier/Adapter with SIM Card Slot](https://www.amazon.com/gp/product/B07YY5967K)
  
  [Antennas for Modem and GPS](https://www.amazon.com/Antenna-698-2700MHZ-Universal-Directional-Wireless/dp/B08XBSYT8N) - need 3 antennas, 2 for cellular and 1 for GPS
  
  [Cables to connect Antennas to Cell Modem](https://www.amazon.com/Coaxial-Pigtail-Antenna-Bulkhead-Extender/dp/B098QH631G) - get appropriate cables to connect to uFL on the cellular board. SMA male antennas need SMA female cables.
  
  [USB Right Angle Up Adapter Cable for Modem](https://www.amazon.com/Antrader-Degree-Extension-Converter-Adapter/dp/B07F7Y21GW)
  
    Right Angle adapters for connecting the battery may be useful if using a small case.
  
  SIM Card for Cell Modem - 1nce for example

* Multitech mDot LoRa module
    https://www.multitech.com/brands/multiconnect-mdot
  
    [Antenna for mDot](https://www.amazon.com/915MHz-LoRa-Antenna-Indoor-Cable/dp/B0CTXKBMH9) - SMA male connector needed on 915 MHz antenna 
  
    2mm pitch header cable for connecting mDot to Raspberry Pi

* 

* LWIR transmission window material
  
  Conformal coating
  
  Silicone sealant for water-resistance
  
  [Copper or aluminum heatsinks](https://www.amazon.com/GeeekPi-Heatsinks-Conductive-Electronic-Compatible/dp/B0C7Z27Q3R) for Raspberry Pi
  
  Anti-fog spray/hydrophobic coating for lens
  
  Dessicant packs
  
  [Stemma QT to header cables](http://adafru.it/4397)
  
  Assorted female-female header cables for connecting components 

### Setup Raspberry Pi 4 with Flir Camera, IMU, and Quectel Cellular modem+GPS

  Refer to this image for GPIO pin numbers

![](documentation_assets/cbbc013483ece19e1ff6cbd77a34d63fbe3192e2.png)

Eventually we will have an image file that can be flashed onto an SD card for new builds.

<details>
<summary>If and only if installing software from scratch:</summary>

Use Raspberry Pi Imager to install current stable 64-bit Raspberry Pi OS Lite (Bookworm) to a microSD card with SSH enabled in the configuration options, along with the user account name and password, and configure a unique hostname for each system that makes sense (like the installation location)

https://www.raspberrypi.com/software/

After flashing, add enable_uart=1 at the end of the /boot/config.txt file. Insert the SD card in the Pi, attach power and boot it up.

https://www.jeffgeerling.com/blog/2021/attaching-raspberry-pis-serial-console-uart-debugging

Use a serial cable to connect to the console and use sudo raspi-config to configure the device settings (locale, timezone, predictable network names, etc.,) and select Network Manager in place of dhcpcd in networking settings.

https://learn.adafruit.com/adafruits-raspberry-pi-lesson-5-using-a-console-cable/software-installation-windows

Witihin raspi-config, leave GPU memory at the default of 32 MB, PiCamera2 will not need more and the camera will not work with less.

Use sudo nmtui to configure the ethernet connection to a static IP, with your computer IP as the gateway and DNS server if you are sharing your Internet connection with the Pi. Otherwise configure for whatever network setup you have.

Now you can use ssh to login to the Pi after connecting it to your computer with an ethernet cable. Connection sharing can be setup using Network Manager on a Linux computer, or Windows Connection sharing, or the macOS equivalent.

Once you've logged in and are sharing an internet connection from your computer to the Pi, run sudo apt update and sudo apt upgrade

Verify the Pi is on the latest firmware with rpi-eeprom-update.

Helpful tools: sudo apt install git tmux htop rpicam-apps
Serial console application: tio [https://github.com/tio/tio] or other (screen, minicom, etc.,)
Install your preferred editor (which should be neovim) and aptitude if you want a TUI for apt

Set /boot/config.txt options:

###### Disable LEDs to save a little power

dtparam=act_led_trigger=none
dtparam=act_led_activelow=off
dtparam=pwr_led_activelow=off

To disable ethernet LEDs on Pi 3 & 4 try:

dtparam=eth_led0=4
dtparam=eth_led1=4

or:

dtparam=eth_led0=14
dtparam=eth_led1=14

###### disable audio

dtparam=audio=off

###### disable wireless, we won't use WiFi or Bluetooth past setup

dtoverlay=disable-wifi
dtoverlay=disable-bt
dtoverlay=pi3-disable-wifi
dtoverlay=pi3-disable-bt

###### I2C clock stretching for BNO055 IMU

dtparam=i2c_arm_baudrate=10000

###### Add to /boot/cmdline.txt - Flir Lepton SPI settings

spidev.bufsiz=131072

###### SD Card settings

Disable swap and set noatime to prolong SD card life: sudo swapoff --all, sudo apt purge dphys-swapfile.

Add noatime,commit=60 settings to ext4 partitions in /etc/fstab - noatime prevents writing access times to files, commit collects and delays writes to every N seconds. Data loss will be limited to the last N seconds of writes if power is lost. Do NOT change the /boot partition settings, it is a vfat filesystem and these options will not work and will cause the Pi to not boot.

Set temp directories like /tmp, /var/tmp to mount in RAM, ex. tmpfs /var/tmp tmpfs nodev,nosuid,size=20M 0 0 in fstab

Use a larger SD card size than needed so you have free space for automatic wear-leveling (is this a thing on cheap SD cards?)

##### Optional Tweaks

You can disable services we won't be needing to speed up boot slightly (~3s)
sudo systemctl disable man-db.timer wpa_supplicant keyboard-setup triggerhappy

If there are issues with taking high-resolution images use the vc4-kms-v3d driver with options: dtoverlay=vc4-kms-v3d,cma-320

Can also add nohdmi to the vc4-kms-v3d line to disable HDMI ports and save ~30mA

### Power Management

Next, configure the WittyPi 4 for power management.
Download: wget http://www.uugear.com/repo/WittyPi4/install.sh
Install: sudo sh install.sh
Shutdown the Pi, install the WittyPi onto the Pi using the extended headers
Reboot, then run wittyPi.sh from the wittypi directory to configure the schedule.

Remove uwi since we will not be using it: sudo systemctl disable uwi, then rm the uwi directory.

### SU-WaterCam software setup from scratch

Clone the public git repo: https://github.com/mandeeps/SU-WaterCam.git
Compile lepton.c and capture.c for the device. Install build-essential if not already done: sudo apt install build-essential. Then cd to the SU-WaterCam/tools directory and run: 
gcc lepton.c -o lepton && gcc capture.c -o capture

Copy to the root of the SU-WaterCam directory: From tools directory, run "cp lepton ../." and "cp capture ../."

Use apt to install these packages: sudo apt install libgpiod-dev python3-pandas python3-dev python3-venv exempi python3-wheel python3-picamera2 python3-rasterio

Make sure picamera2 is installed as system package, not through pip

Create a virtual environment with python -m venv --system-site-packages /home/pi/SU-WaterCam/venv, (we use system-site-packages to copy over pandas and other installed modules)
activate with source /home/pi/SU-WaterCam/venv/bin/activate, and then install modules with pip install -r /home/pi/SU-WaterCam/requirements.txt
or manually with pip install compress_pickle adafruit-blinka gpiozero piexif py-gpsd2 python-xmp-toolkit

Set default Python to the venv by adding 'source /home/pi/SU-WaterCam/venv/bin/activate' to the end of your .bashrc file.

### Adafruit BNO055 IMU

pip install adafruit-circuitpython-bno055 in the venv

### Adafruit BNO085 IMU

pip install adafruit-circuitpython-bno08x in the venv

### Calibrate the IMU prior to use:

TODO add instructions

Calibrate BNO055

Calibrate BNO085

</details>

## Hardware Setup

Current order of installation:

Insert microSD, modify and install NIR camera, install passive heatsink, install WittyPi 4 with CR2032 battery, connect cellular modem, connect Flir Lepton, connect mDot, power on and test device before installing into case.

Drill holes for cameras and external power (and optionally antennas if too large to fit within or signal blocked) into case, place components and battery into case, install cameras, connect antennas. Power on and test device.

Connect to solar panel power. Apply silicone sealant to all openings into case, and install LWIR transmission window for Lepton. Check water-resistance before field installation.

### Optical Camera

Desolder the photo resistor/light sensor from the Dorhea IR-CUT camera. Solder a wire so the IR filter can be manually controlled by the Pi. The wire is soldered to the third point from the bottom of the camera on the backside and connected to pin #40 on the Pi for use with the take_nir_photos.py script.

Before removing the photoresistor:

![](documentation_assets/eec68f83a9c776836e74e2533f9fc5d29389d65b.jpg)

Solder a header cable like so:

![](documentation_assets/b176dde40a564b61f8dc3d2b25897d14209191f5.jpg)

It should look like this when done:

![](documentation_assets/347c3f1525e4321f3e7c8eff3596e8b765fe2997.jpg)

Insert the cable into the camera with the metal pins facing the board:

![](documentation_assets/4d30de17d7fa64a534349d509ad71f28c22dfe91.jpg)

On the Rapsberry Pi find the CAMERA slot. The other end of the ribbon cable should be installed with the metal pins facing away from the black plastic retainer towards the pins in the slot:

![](documentation_assets/6d00c3ddceb155d7bbed6e278a3615e26f77ac0a.jpg)

### Quectel EC25 Modem and GPS

Install the miniPCIE card into the USB adapter. The mini PCIE card is the component on top and the USB adapter is the component on the bottom of this image:

![](documentation_assets/0467104137c0c19fa75ce0755bab2f464fd42280.jpg)

The card can only fit into the adapter one way:

![](documentation_assets/dadb613d101c3772d56f3ad7641b17da2c42b2d8.jpg)

The MAIN and DIV UFL ports should connect to cellular antennas using UFL to SMA cables, and the GNSS slot is for a GPS antenna. Read this before connecting anything to UFL connectors: [Three Quick Tips About Using U.FL - SparkFun Learn](https://learn.sparkfun.com/tutorials/three-quick-tips-about-using-ufl/all)

The UFL connectors on the cables can be easy to damage, consider using a tool like this for installing and removing the cables: [U.FL Push/Pull Tool](https://www.sparkfun.com/u-fl-push-pull-tool.html)

![](documentation_assets/2fc42bb090cdef121de22d49cad3dfb411cf9cad.jpg)

Use a right-angle USB adapter cable to make connecting to the Raspberry Pi easier:

![](documentation_assets/75cf3c2b92f0e262d8e373dfd5fdc7fbf23100d0.jpg)

Also consider taping or otherwise securing the connections once everything is installed in the waterproof case to reduce the chance of disconnections during field installation.

<details>

<summary>Cellular Modem Manual Software Setup</summary>

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

Reduce the priority of the cellular modem so Ethernet is preferred while you are building the unit and still installing updates: https://superuser.com/a/1603124

sudo nmcli con mod Quectel ipv4.route-metric 100 and do the same for ipv6

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

</details>

### Flir Lepton Breakout board wiring

![Raspberry Pi GPIO Header](documentation_assets/54ae0311ce729fee2c56db6e9898af8cf892b79e.jpg)

Image source: [Lepton/docs/RaspberryPiGuide.md at main · FLIR/Lepton · GitHub](https://github.com/FLIR/Lepton/blob/main/docs/RaspberryPiGuide.md)

Eventually we will have a PCB to connect the Lepton breakout board and Raspberry Pi.

<details>
<summary>Manual Lepton Breakout Board Wiring</summary>
Orient the back of the breakout board towards yourself. The front is the side with the socket for the Lepton camera.
Let's call the pins that are closest to you pins 1 through 10, starting from the left and going to the right. Right is the side with the mini ZIF connector on top (the white plastic bit above the QR code sticker)
Let's call the pins on the bottom (away from you) pins A through J.

![Flir Lepton Breakout 2.0 Pins](documentation_assets/d0cf10945c10c618cd43d86e5a04483159c54ba9.jpg)


Image by Kaitlyn Gilmore (https://github.com/kmgmore)

We can use the top pin of the two pins without jumpers on the back of the board (side towards you right now) for ground. In other words, the pin with nothing covering it that is closest to the white ZIF socket towards the top is the ground pin, so connect it to the ground pin on the Pi.

Wiring diagram: ![](documentation_assets/3d352206a6508c0c4cf506a1cf869aee435c1609.png)

Because we need I2C for other peripherals, use splitter cables for the two I2C pins (SDA and SCL) on the Pi. So get or make two cables that each have a female header on one end and a male and female header on the other end. One female end connects to a pin on the Raspberry Pi GPIO header, and the other two ends are for the Flir breakout board and a peripheral like the Adafruit IMU. Another pair of split cables is useful for 3.3V and ground.

The SDA pin on the Pi (pin #3) will connect to pin C on the breakout board (side away from you) - use a splitter

The SCL pin on the Pi (pin #5) will connect to pin 4 on the breakout board (side towards you) - use a splitter

MOSI on the Pi (pin #19) connects to pin E on the breakout

MISO on the Pi (pin #21) connects to pin 6 on the breakout board

CLK pin on the Pi (pin #23) will connect to pin D on the breakout

CS pin (pin #24, right across from CLK, aka CE0, GPIO 8) connects to pin 5 on the breakout board

The VSYNC pin is Pin #11, GPIO 17 on the Pi connected to pin H on the breakout board.

Reset pin on the breakout is pin I following the convention declared above. Connect it to an arbritrary GPIO pin on Pi that is set high by default (options are 0-8)

I am using GPIO 6 (pin 31 on the Pi) in the lepton_reset.py script. We need a pin that is high by default because the breakout board reset triggers on low.

Insert the Flir camera into the breakout board.
Check everything is correct by running the capture and lepton binaries in SU-WaterCam. Rename or copy the appropriate 32 or 64-bit binaries to "lepton" and "capture" and then run: ./capture

Examine the created files to verify things are working.

Binaries are from https://github.com/lukevanhorn/Lepton3

Thanks Luke Van Horn! Also, thanks to Max Lipitz for the tip about the output containing the temperature values in degrees Kelvin.

</details>

<details>
<summary>Additional Software for Lepton</summary>

#### Leptonic for live thermal image stream

We're setting up an unused Pi 3 for collecting thermal images for coregistration - using leptonic from github, a forked branch that can be built on Debian 12 Bookworm

https://github.com/rob-coco/leptonic/tree/bookworm-update

checkout the bookworm-update branch, compile that after installing dependencies: libzmq3-dev

Port forward, first ssh into pi and run leptonic on /dev/spidev0.0, then open another terminal and port forward with:
➜ ssh -L 5555:10.42.0.3:5555 pi@10.42.0.3

Run the leptonic web server on your own machine, it's too much for the Pi 3 to do both: 
npm start in frontend directory then
127.0.0.1:3000 in your browser

</details>

### Multitech mDot LoRa module

The default mDot firmware is set up for UART. The WittyPi can tell if the Raspberry Pi is off by reading the TX pin, which should be set low when the Pi shuts down. The mDot seems to interfere with this, keeping the TX pin on the Pi set high and preventing the WittyPi from cutting off power to the system. So turn on alternative UART pins on the Pi 4B and use those to connect to the mDot instead.

By adding dtoverlay=uart5 to /boot/config.txt on a Pi 4 we can use pin 32 for TX and pin 33 for RX. On the Pi 4, make sure "enable_uart=1" is in the /boot/config.txt file, and add dtoverlay=uart5. Save and reboot. Connect the TX pin on the mDot to pin #33 on the Pi and connect the RX pin on the mDot to pin #32 on the Pi.

The power pin (VOD, pin # 1) on the mDot can be connected to the 5V or 3.3V power pin on the Pi. Connect ground (pin 10 on the mDot) to a free ground pin. Connect the mDot UART TX (transmit, pin #2) to the Pi RX (receive) pin (#10 default, pin #33 if using uart5), and the mDot RX pin (#3) to the Pi TX pin (#8 default, pin #32 using uart5).

On the Pi run sudo minicom -s -D /dev/serial0 to connect to the mDot if it is on the default TX/RX pins, minicom -s -D /dev/ttyAMA1 if on uart5, and issue AT commands. Use the settings specified in the mDot manual:

Baud rate 115200

Data bits 8

Parity N

Stop bits 1

Hardware/software flow control off

For deployment we'll want the mDot to have a seperate power source so we can remotely trigger it to signal the WittyPi to boot up the system and record data.

### Tailscale for remote login over cellular data

https://tailscale.com/download

Consider using Tailscale SSH: https://tailscale.com/kb/1193/tailscale-ssh

tailscale up --ssh

Install and use mosh for high-latency cellular connections
sudo apt install mosh

Use an appropriate client for your own device.

### Filebrowser remote file access

Helpful when using a system interactively for data collection: https://github.com/filebrowser/Filebrowser

### Remote Video Streaming

with libcamera-apps-lite installed run:

libcamera-vid -t 0 --inline --listen -o tcp://0.0.0.0:8888

On your machine connected to the Pi (over Tailscale or directly) use VLC to stream the video using 'open network stream' and enter tcp/h264://PI_ADDRESS_OR_HOSTNAME:8888 with the appropriate IP address and port number. 

### Pytorch

pip install torch torchvision (in the venv)

Model based on FloodNet data set and DeepLab
FloodNet: https://ieeexplore.ieee.org/document/9460988

## Clone SD Card

If you need to clone/copy a Pi microSD card:

on a Linux/*nix system use dd to copy the entire device. Make sure you have enough free space first. Use lsblk to determine the location of the SD card. Clone the entire disk, not a partition.

sudo dd bs=4M if=/dev/mmcblk0 of=sd_clone conv=fsync status=progress

You might need to run dd with sudo if your user account does not have access to the SD device. Change the owner of the new file if so.

If you only want the image as a backup or won't be flashing new SD cards with it for a while, use PiShrink to save some space: https://github.com/Drewsif/PiShrink

sudo pishrink.sh -a -Z image_file

For creating new SD cards with the file you copied use dd as above but with the input and output reversed to write to a blank SD card. Always check you are writing to the correct device when you use dd. For more information on this: https://www.pragmaticlinux.com/2020/12/how-to-clone-your-raspberry-pi-sd-card-in-linux/ 

## Manual Data Collection

If you need to take a unit out in the field to collect data you can add a couple of wires to a button to trigger the cameras and use the button-service-gpiozero.py script with GPIO Zero installed: https://github.com/gpiozero/gpiozero 

We are using a simple pushbutton on a perboard with two header cables connected to pin #29 and ground on the Pi. Autostart the script with systemd (or alt init)
