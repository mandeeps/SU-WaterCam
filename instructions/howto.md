# Build Guide / How To

### Project requires a Raspberry Pi or compatible single board computer
running Linux.

If using a Pi Zero, install the headers if it doesn't ship with them attached

### Parts List

Case

https://www.amazon.com/LMioEtool-Dustproof-Waterproof-Electrical-Transparent/dp/B07PK8K8S2

Raspberry Pi Zero

https://www.adafruit.com/product/2885

Optical Camera

https://www.amazon.com/Dorhea-Raspberry-Camera-Automatic-Adjustable/dp/B07DNSSDGG/ref=pd_bxgy_img_2/136-0027955-5919373?pd_rd_w=2vD2j&pf_rd_p=fd3ebcd0-c1a2-44cf-aba2-bbf4810b3732&pf_rd_r=GYFEFWF9TX4TQ2CJAENW&pd_rd_r=47fda39a-7777-432d-9d94-af7113524711&pd_rd_wg=zb2az&pd_rd_i=B07DNSSDGG&psc=1

If using a Pi Zero make sure to get a camera cable that will fit the smaller
CSI connector on the Zero!

https://www.adafruit.com/product/3157

WittyPi 3 Mini

https://www.adafruit.com/product/2223

Stacking header to attach WittyPi to RPi 

https://www.adafruit.com/product/5038

Adafruit MLX90640 Thermal Sensor

https://www.adafruit.com/product/4407

or

https://www.adafruit.com/product/4469

or better

Stemma QT cable to connect Adafruit thermal sensor to headers

https://www.adafruit.com/product/4397

If using a different thermal sensor (like the Flir Lepton) get needed
cables or breakout boards for it. Source code will need to be modified
to use the appropriate library for that sensor.

Battery
https://voltaicsystems.com/v50/ or other battery. I like Voltaic battery
packs for this since they do not auto-shutdown during low power draw, 
which is important for this system as it will be in low-power mode most 
of the time and losing power then would prevent it from starting back up. 

Make sure to have a way to connect whatever battery you are using to the 
WittyPi. If using a plain lipo battery instead of a USB power bank like 
the Voltaic get a charger that will also let you connect the battery to 
the microUSB port on the WittyPi like 

https://www.adafruit.com/product/2465

You'll need to solder the inlcuded USB header on or else solder a micro USB cable
to the output so it can be connected to the WittyPi
If you use a regular USB power bank / portable phone charger you'll need
to adjust some WittyPi settings so it draws more power when the RPi is off to
avoid the power bank shutting off all power. This will of course decrease
battery life. 

MicroSD card
micro USB cables, at least one preferably 2

Instructions assume you are using a Raspberry Pi Zero with headers installed
with either Adafruit MLX90640 sensor

Flash the provided disk image onto the microSD card if not already done

If installing regular Raspbian/RaspberryPi OS image:
in raspi-config set timezone, enable camera, ssh and i2c, set static IP address, reduce GPU memory to 128 minimum for optical camera, change default password, etc.,
use apt to install python3-pandas: sudo apt install python3-pandas
and libgpiod-dev: sudo apt install libgpiod-dev
use pip to install other dependencies: python3 -m pip install compress_pickle adafruit-blinka adafruit_circuitpython-mlx90640 gpiozero

If using SD card image provided this should already have been done for you!
Insert into the micrSD card slot on the Raspberry Pi

It's easier to connect the optical camera before installing the WittyPi
Be careful with the connector and the cable, IME ribbon cables and ZIF
sockets are way too fragile. While the cable can flex it should not be folded. Be careful
you don't break the socket clip by using too much force.
For the RPi Zero you'll want to replace any cable
included with the camera with the correct RPi Zero camera cable

Insert the larger end of the RPi camera ribbon cable into the optical camera by gently
pulling the black plastic clip out and inserting the cable in, making sure
the exposed side with the gold pins is facing down. Secure the cable by
pusing the plastic clip back in.

Now on the Raspberry Pi end, gently pull forward the black plastic clip on the CSI camera connector
Insert the cable into the connector making sure the exposed side is down
Push the black plastic clip back into place

Install stacking header onto the Raspberry Pi header making sure it is
firmly in place

Install WittyPi 3 Mini onto the extended headers making sure it is in place

Once the WittyPi is installed you can connect your thermal sensor
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

If you are using a provided microSD card or if you flashed the image yourself, 
the OS should already be set to run everything for 10 minutes every hour, saving data to the card.
If not, you can log into the Pi and use the WittyPi program to set the schedule (or increase idle power use if using a normal power bank)

https://cdn-shop.adafruit.com/product-files/5038/5038_WittyPi3Mini_UserManual.pdf

If you have a computer set up to connect to the RPi Zero using USB-OTG networking

https://learn.adafruit.com/turning-your-raspberry-pi-zero-into-a-usb-gadget/ethernet-gadget

https://artivis.github.io/post/2020/pi-zero/

you can plug a micro USB cable into the data port of the RPi (the middle port of the three)
and the other end into your computer. Assuming your computer configuration is correct you
should be able to use SSH to access the Zero. If you are not using a Zero
you'll need to use a serial connection, ethernet, or wifi to connect to the Pi.

Once everything is tested and configured, package it up in a water-resistant container and deploy. 
The Voltaic V50 battery ran for over a week in my test, with the above configuration.
It would likely have run longer but I cut the test short to work on the code.
