#!/home/pi/SU-WaterCam/venv/bin/python

# Take two photos with Dorhea IR-Cut Camera
# One with NIR filter in place and one without
# Set GPIO HIGH to include NIR in the red band and LOW for normal photo
# Call add_metadata to get info from IMU and GPS
# Run lepton and capture binaries to save data from Flir in same directory

import subprocess
import time
from os import path, makedirs, chdir
from datetime import datetime
from picamera2 import Picamera2
from gpiozero import LED
import add_metadata

# Seconds to wait after changing the NIR filter GPIO state before capturing.
# The IR-CUT filter motor needs ~300 ms to physically complete its movement.
NIR_FILTER_SETTLE_S = 0.5

try:
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)
    # picam2.start() -- do not start outside start_and_capture function as this interferes with Flir Lepton! (for some reason I don't understand)
except Exception:
    print("Camera loading error")

def take_photo(directory: str, nir: str) -> str:
    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(directory, f'{time}-NIR-{nir}.jpg')
    print(f'taking photo: {image}')

    try:
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception:
        print("Camera failed to capture")

    # get IMU and GPS data and save into image EXIF and XMP
    add_metadata.add_metadata(image)
    return image

def flir(directory):
    # Flir Lepton 3.5 capture and lepton binaries for image and radiometery
    chdir(directory)
    try:
        subprocess.run(["/home/pi/SU-WaterCam/capture"], check=True, timeout=5)
    except subprocess.TimeoutExpired:
        print("Check Lepton state - capture failed")
        subprocess.run(["/home/pi/SU-WaterCam/tools/lepton_reset.py"], check=True)
    try:
        subprocess.run(["/home/pi/SU-WaterCam/lepton"], check=True, timeout=5)
    except subprocess.TimeoutExpired:
        print("Check Lepton state - radiometery failed")
        subprocess.run(["/home/pi/SU-WaterCam/tools/lepton_reset.py"], check=True)

def main(filepath: str) -> str:
    date = datetime.now().strftime('%Y%m%d-%H%M%S')
    directory = path.join(filepath, date)
    if not path.exists(directory):
        makedirs(directory)

    # AWB/AEC warmup: let the camera stabilize on the scene before locking so
    # both captures use identical settings and the difference reflects the filter.
    NIR_AWB_WARMUP_S = 2.0

    pin = LED(21)
    image_off = None
    image_on = None
    try:
        picam2.start()

        # Drive pin LOW (IR filter IN) and wait for both filter movement and
        # AWB/AEC convergence before locking controls.
        pin.off()
        time.sleep(NIR_FILTER_SETTLE_S + NIR_AWB_WARMUP_S)

        # Lock AWB and AEC at current settled values.
        try:
            meta = picam2.capture_metadata()
            gains = meta.get("ColourGains", (1.0, 1.0))
            exp   = meta.get("ExposureTime", 10_000)
            gain  = meta.get("AnalogueGain", 1.0)
            picam2.set_controls({
                "AwbEnable": False, "ColourGains": gains,
                "AeEnable": False, "ExposureTime": exp, "AnalogueGain": gain,
            })
            time.sleep(0.1)
        except Exception as exc:
            print(f"Camera control lock failed: {exc}; continuing with auto")

        print(f"Pin state is: {pin.value}")
        ts_off = datetime.now().strftime('%Y%m%d-%H%M%S')
        image_off = path.join(directory, f'{ts_off}-NIR-OFF.jpg')
        print(f'taking photo: {image_off}')
        picam2.capture_file(image_off)

        pin.on()
        time.sleep(NIR_FILTER_SETTLE_S)
        print(f"Pin state is: {pin.value}")
        ts_on = datetime.now().strftime('%Y%m%d-%H%M%S')
        image_on = path.join(directory, f'{ts_on}-NIR-ON.jpg')
        print(f'taking photo: {image_on}')
        picam2.capture_file(image_on)

    except Exception as exc:
        print(f"Camera capture failed: {exc}")
    finally:
        try:
            picam2.set_controls({"AwbEnable": True, "AeEnable": True})
        except Exception:
            pass
        try:
            picam2.stop()
        except Exception:
            pass
        pin.close()

    # Add metadata after camera is stopped (GPS/IMU reads can be slow).
    for img in (image_off, image_on):
        if img and path.exists(img):
            add_metadata.add_metadata(img)

    return image_off, directory


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
    else:
        filepath = "/home/pi/SU-WaterCam/images/"

    # take photos: optical and NIR
    nir_off_name, directory = main(filepath)

    print(f"Photo path: {directory}")
    # take FLIR photo and get temperature data from Lepton
    flir(directory)
