#!/usr/bin/env python
# Take two photos with Dorhea IR-Cut Camera
# One with NIR filter in place and one without
# Set GPIO HIGH to include NIR in the red band and LOW for normal photo
# Call add_metadata to get info from IMU and GPS
# Run lepton and capture binaries to save data from Flir in same directory

from ticktalkpython.SQ import SQify

def take_photo(directory: str, nir: str, picam2) -> str:
    from os import path #, makedirs, chdir
    from datetime import datetime
    from tools.add_metadata import add_metadata

    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(directory, f'{time}-NIR-{nir}.jpg')
    print(f'taking photo: {image}')

    try:
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception:
        print("Camera failed to capture")

    # get IMU and GPS data and save into image EXIF and XMP
    add_metadata(image)

@SQify
def flir(directory):
    from os import chdir, rename #path, makedirs, chdir
    from time import sleep
    import subprocess 
    from datetime import datetime
    date = datetime.now().strftime('%Y%m%d-%H%M%S')

   # Flir Lepton 3.5 capture and lepton binaries for image and radiometery
    chdir(directory)
    try:
        subprocess.run(["/home/pi/SU-WaterCam/capture"], check=True, timeout=5)
    except:
        print("Check Lepton state - capture failed")
    else:
        print(f"change name to include {date}")
        rename("IMG_0000.pgm", f"lepton_{date}.pgm")

    try:
        subprocess.run(["/home/pi/SU-WaterCam/lepton"], check=True, timeout=5)
    except:
        print("Check Lepton state - radiometery failed")
    else:
        print(f"change name to include {date}")
        rename("lepton_temp_0000.csv", f"temperatures_{date}.csv")

    return True


@SQify
def take_two_photos(trigger, directory):
    from picamera2 import Picamera2
    from gpiozero import LED
    from tt_take_photos import take_photo
    import sys
    sys.path.insert(0, "/home/pi/SU-WaterCam/tools")

    global sq_state
    try:
        picam = sq_state.get("picam", None)
        if picam is None:
            picam2 = Picamera2()
            config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
            picam2.configure(config)
            sq_state['picam'] = picam2
        picam2 = sq_state['picam']
    except Exception:
        print("Camera loading error")
    
    # Adjust GPIO as appropriate. We are using GPIO 21, pin 40
    pin = LED(21)
    pin.off()
    print(f"Pin state is: {pin.value}")

    take_photo(directory, "OFF", picam2)

    pin.on()
    take_photo(directory, "ON", picam2)

    pin.close()

    return True
