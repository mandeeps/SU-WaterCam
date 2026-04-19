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
    import subprocess
    from datetime import datetime
    date = datetime.now().strftime('%Y%m%d-%H%M%S')

    # Flir Lepton 3.5 capture and lepton binaries for image and radiometery
    # Derive project root from directory (always <root>/images/<date>), then
    # scan upward for the capture binary as a cross-check.  Must be computed
    # before any chdir so we are not misled by a changed working directory.
    def _find_project_root(start):
        candidate = path.abspath(start)
        for _ in range(6):
            candidate = path.dirname(candidate)
            if path.exists(path.join(candidate, "capture")):
                return candidate
        return None

    project_root = _find_project_root(directory) or path.dirname(path.dirname(path.abspath(directory)))
    capture_bin = path.join(project_root, "capture") if path.exists(path.join(project_root, "capture")) else None
    lepton_bin = path.join(project_root, "lepton") if path.exists(path.join(project_root, "lepton")) else None

    # Ensure target directory exists (create fallback if needed)
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
        directory = path.join(project_root, 'images', 'fallback')
        try:
            makedirs(directory, exist_ok=True)
        except Exception:
            directory = path.join(project_root, 'images')
            makedirs(directory, exist_ok=True)

    try:
        chdir(directory)
    except Exception:
        directory = path.join(project_root, 'images', 'fallback')
        makedirs(directory, exist_ok=True)
        chdir(directory)

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
    from os import path
    from datetime import datetime
    from tools.add_metadata import add_metadata

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
        return True

    import time
    # IR-CUT filter motor needs ~300 ms to move; 0.5 s provides margin.
    NIR_FILTER_SETTLE_S = 0.5
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
        if img and path.isfile(img):
            try:
                add_metadata(img)
            except Exception as exc:
                print(f"Metadata write failed for {img}: {exc}")

    return True
