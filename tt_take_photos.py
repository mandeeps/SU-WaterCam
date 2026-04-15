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
    from os import chdir, rename, makedirs, path
    from time import sleep
    import subprocess
    from datetime import datetime
    import inspect
    import sys
    from pathlib import Path
    date = datetime.now().strftime('%Y%m%d-%H%M%S')

    # Flir Lepton 3.5 capture and lepton binaries for image and radiometery
    # Ensure target directory exists (create fallback if needed)
    def _safe_project_root():
        try:
            fpath = inspect.getfile(flir)
            return path.dirname(path.abspath(fpath))
        except Exception:
            try:
                mod_file = sys.modules.get(__name__).__dict__.get('__file__')
                if mod_file:
                    return path.dirname(path.abspath(mod_file))
            except Exception:
                pass
            return str(Path.cwd())

    need_fallback = False
    try:
        if not path.isdir(directory):
            if directory.startswith('/home/pi/') or directory == '/home/pi':
                need_fallback = True
            else:
                makedirs(directory, exist_ok=True)
    except PermissionError:
        need_fallback = True
    except Exception:
        need_fallback = True

    if need_fallback:
        project_root = _safe_project_root()
        directory = path.join(project_root, 'images', 'fallback')
        try:
            makedirs(directory, exist_ok=True)
        except Exception:
            directory = path.join(_safe_project_root(), 'images')
            makedirs(directory, exist_ok=True)

    try:
        chdir(directory)
    except Exception:
        project_root = _safe_project_root()
        directory = path.join(project_root, 'images', 'fallback')
        makedirs(directory, exist_ok=True)
        chdir(directory)

    # Resolve binary paths using project root
    project_root = _safe_project_root()
    capture_bin = next((p for p in [path.join(project_root, "capture")] if path.exists(p)), None)
    lepton_bin = next((p for p in [path.join(project_root, "lepton")] if path.exists(p)), None)

    try:
        if not capture_bin:
            raise FileNotFoundError("capture binary not found")
        subprocess.run([capture_bin], check=True, timeout=5)
    except:
        print("Check Lepton state - capture failed")
    else:
        print(f"change name to include {date}")
        rename("IMG_0000.pgm", f"lepton_{date}.pgm")

    try:
        if not lepton_bin:
            raise FileNotFoundError("lepton binary not found")
        subprocess.run([lepton_bin], check=True, timeout=5)
    except:
        print("Check Lepton state - radiometery failed")
    else:
        print(f"change name to include {date}")
        rename("lepton_temp_0000.csv", f"temperatures_{date}.csv")

    return True


@SQify
def take_two_photos(trigger, directory):
    try:
        from picamera2 import Picamera2
    except Exception:
        print("Camera module (picamera2) unavailable - skipping photo capture")
        return True
    from gpiozero import LED
    from tt_take_photos import take_photo

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
