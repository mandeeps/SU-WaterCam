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
    from os import makedirs, path, rename
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

    try:
        makedirs(directory, exist_ok=True)
    except Exception as exc:
        fallback_directory = path.join(project_root, "images", "fallback")
        print(f"Unable to create FLIR output directory '{directory}': {exc}")
        try:
            makedirs(fallback_directory, exist_ok=True)
            print(f"Falling back to FLIR output directory '{fallback_directory}'")
            directory = fallback_directory
        except Exception as fallback_exc:
            print(f"Unable to create fallback FLIR output directory '{fallback_directory}': {fallback_exc}")
            return True

    def _reset_lepton():
        import sys
        if not path.isfile(lepton_reset):
            print(f"Lepton reset script not found: {lepton_reset}")
            return
        try:
            subprocess.run(
                [sys.executable, lepton_reset], check=True, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"Lepton reset failed: {exc}")

    def _run_flir_cmd(label, command):
        import time

        if not path.isfile(command[0]):
            print(f"Check Lepton state - {label} binary not found: {command[0]}")
            return []

        if label == "capture":
            out_glob = path.join(directory, "IMG_*.pgm")
        elif label == "lepton":
            out_glob = path.join(directory, "lepton_temp_*.csv")
        else:
            out_glob = None

        start_time = time.time()
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
            return []
        except Exception as exc:
            print(f"Check Lepton state - {label} failed: {exc}")
            _reset_lepton()
            return []

        if proc.returncode != 0:
            print(f"Check Lepton state - {label} failed (rc={proc.returncode})")
            _reset_lepton()
            return []

        MTIME_SLOP_SEC = 1
        # Verify output was written during this run. Allow a small mtime slop
        # for filesystems with coarse timestamp granularity and minor clock /
        # timestamp-recording skew, while still requiring output to be fresh.
        # Using mtime handles both new files and overwrites of existing files,
        # so validation stays correct when the fallback directory is reused.
        if out_glob is not None:
            fresh = [f for f in glob(out_glob) if path.getmtime(f) >= start_time - MTIME_SLOP_SEC]
            if not fresh:
                print(f"Check Lepton state - {label} completed but no fresh output found")
                _reset_lepton()
                return []
            return fresh

        return []

    # Return fresh paths from each command so the rename step uses exactly
    # the files produced by this run — no re-glob that could match a file
    # from a concurrent run sharing the same (fallback) directory.
    capture_fresh = _run_flir_cmd("capture", [capture_bin])
    if not capture_fresh:
        return True
    lepton_fresh = _run_flir_cmd("lepton", [lepton_bin])
    if not lepton_fresh:
        return True

    # Rename outputs to timestamped names matching the coregistration pipeline's expectations.
    for fresh_files, dst_name in [
        (capture_fresh, f"lepton_{date}.pgm"),
        (lepton_fresh,  f"temperatures_{date}.csv"),
    ]:
        src_path = max(fresh_files, key=path.getmtime)
        try:
            rename(src_path, path.join(directory, dst_name))
        except Exception as exc:
            print(f"Could not rename {src_path}: {exc}")

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

    picam2 = None
    try:
        picam2 = Picamera2()
        config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592, 1944)})
        picam2.configure(config)
    except Exception as exc:
        print(f"Camera loading error: {exc}")
        if picam2 is not None:
            try:
                picam2.close()
            except Exception:
                pass
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
        try:
            picam2.close()
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
