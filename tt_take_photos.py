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
    from os import makedirs, path, listdir, rename
    from glob import glob
    import subprocess
    from datetime import datetime

    date = datetime.now().strftime('%Y%m%d-%H%M%S')

    def _find_project_root(start):
        candidate = path.abspath(start)
        for _ in range(6):
            candidate = path.dirname(candidate)
            if path.exists(path.join(candidate, "capture")):
                return candidate
        return None

    project_root = _find_project_root(directory) or path.dirname(path.dirname(path.abspath(directory)))
    capture_bin = path.join(project_root, "capture")
    lepton_bin  = path.join(project_root, "lepton")
    lepton_reset = path.join(project_root, "tools", "lepton_reset.py")

    makedirs(directory, exist_ok=True)

    def _reset_lepton():
        try:
            subprocess.run(
                [lepton_reset], check=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"Lepton reset failed: {exc}")

    def _run_flir_cmd(label, command):
        if not path.isfile(command[0]):
            print(f"Check Lepton state - {label} binary not found: {command[0]}")
            return False
        try:
            proc = subprocess.run(
                command,
                timeout=20,
                cwd=directory,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"Check Lepton state - {label} timed out")
            _reset_lepton()
            return False
        except Exception as exc:
            print(f"Check Lepton state - {label} failed: {exc}")
            _reset_lepton()
            return False

        if proc.returncode != 0:
            print(f"Check Lepton state - {label} failed (rc={proc.returncode})")
            _reset_lepton()
            return False

        # Verify expected output file appeared in the session directory.
        try:
            files = listdir(directory)
        except Exception as exc:
            print(f"Check Lepton state - {label} could not inspect output dir: {exc}")
            _reset_lepton()
            return False

        if label == "capture":
            if not any(f.lower().endswith(".pgm") for f in files):
                print("Check Lepton state - capture completed but no .pgm output found")
                _reset_lepton()
                return False
        elif label == "lepton":
            if not any(f.startswith("lepton_temp_") and f.lower().endswith(".csv") for f in files):
                print("Check Lepton state - radiometery completed but no lepton_temp_*.csv found")
                _reset_lepton()
                return False

        return True

    if not _run_flir_cmd("capture", [capture_bin]):
        return True
    if not _run_flir_cmd("lepton", [lepton_bin]):
        return True

    # Rename outputs to timestamped names matching the coregistration pipeline's expectations.
    for src_glob, dst_name in [
        (path.join(directory, "IMG_*.pgm"),          f"lepton_{date}.pgm"),
        (path.join(directory, "lepton_temp_*.csv"),  f"temperatures_{date}.csv"),
    ]:
        matches = sorted(glob(src_glob))
        if matches:
            try:
                rename(matches[0], path.join(directory, dst_name))
            except Exception as exc:
                print(f"Could not rename {matches[0]}: {exc}")

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
