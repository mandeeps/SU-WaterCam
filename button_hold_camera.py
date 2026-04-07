#!/home/pi/SU-WaterCam/venv/bin/python3

# alternate button-service script for use on 32bit Pi Zero
# This version is meant to be used with a multispectral cameras-only unit
# for manually collecting images and radiometric data. It is faster than
# spawning another Python process and starting PiCamera2 every time photos are
# taken. This is helpful on the Pi Zero

from os import path, makedirs
from datetime import datetime
from typing import Optional, Tuple
import subprocess
import threading
import time
from signal import pause
from picamera2 import Picamera2
from gpiozero import LED, Button, Buzzer

filepath = "/home/pi/SU-WaterCam/images/"

# Set True only for SPI/CSI interference experiments; sequential capture is far more reliable on Pi Zero.
PARALLEL_FLIR_WITH_PICAM = False

# Extra wait after process start (seconds). Usually unnecessary: use systemd Type=idle and/or
# After=multi-user.target in the unit, plus NIR_PAIR_MAX_ATTEMPTS. Increase only if first captures
# after boot still fail in the field.
STARTUP_DELAY_SEC = 0.0

# Retry attempts around NIR capture startup/capture failures (common right after boot).
# Note: partial OFF-only captures are retained and reported distinctly from full-pair success.
NIR_PAIR_MAX_ATTEMPTS = 3
NIR_PAIR_RETRY_DELAY_SEC = 0.75

FLIR_THREAD_TIMEOUT_SEC = 90

# Serialize captures: a second button event while NIR/FLIR runs would overlap Picamera2 and Lepton I/O.
_capture_lock = threading.Lock()

# Latched if FLIR thread does not stop within timeout; prevents further captures until service restart.
_flir_thread_stuck = False

# Headless feedback (no display): wire LED + resistor from STATUS_LED_BCM to GND, or optional passive buzzer.
# None disables that output. LED: on throughout capture; then 2 quick blinks = NIR pair saved, 4 slow = NIR failed.
STATUS_LED_BCM: Optional[int] = None
STATUS_BUZZER_BCM: Optional[int] = None

# NIR switching (must be a single LED instance; repeating LED(21) causes GPIOPinInUse on fast presses).
NIR_CONTROL_BCM = 21

status_led: Optional[LED] = None
status_buzzer: Optional[Buzzer] = None
nir_control_led: Optional[LED] = None


def _init_user_feedback() -> None:
    global status_led, status_buzzer
    if STATUS_LED_BCM is not None:
        try:
            status_led = LED(STATUS_LED_BCM)
        except Exception as exc:
            print(f"Status LED on GPIO {STATUS_LED_BCM} unavailable: {exc}")
    if STATUS_BUZZER_BCM is not None:
        try:
            status_buzzer = Buzzer(STATUS_BUZZER_BCM)
        except Exception as exc:
            print(f"Status buzzer on GPIO {STATUS_BUZZER_BCM} unavailable: {exc}")


def _init_nir_control_led() -> None:
    global nir_control_led
    try:
        nir_control_led = LED(NIR_CONTROL_BCM)
    except Exception as exc:
        print(f"NIR control LED on GPIO {NIR_CONTROL_BCM} unavailable: {exc}")


def _signal_capture_outcome(nir_pair_saved: bool) -> None:
    if status_led is not None:
        try:
            if nir_pair_saved:
                status_led.blink(on_time=0.1, off_time=0.1, n=2, background=False)
            else:
                status_led.blink(on_time=0.3, off_time=0.2, n=4, background=False)
        except Exception as exc:
            print(f"Status LED feedback failed: {exc}")
        finally:
            try:
                status_led.off()
            except Exception:
                pass
    if status_buzzer is not None:
        try:
            if nir_pair_saved:
                status_buzzer.beep(on_time=0.07, off_time=0.06, n=2, background=False)
            else:
                status_buzzer.beep(on_time=0.22, off_time=0.12, n=4, background=False)
        except Exception as exc:
            print(f"Status buzzer feedback failed: {exc}")

picam2: Optional[Picamera2] = None
try:
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)
except Exception as exc:
    print(f"Camera loading error: {exc}")

def take_nir_pair(directory: str, pin) -> Tuple[Optional[str], bool]:
    """Two full-resolution stills via ``start_and_capture_file`` (not a long-lived ``start()`` session).

    Project history: keeping Picamera2 running with bare ``start()`` can interfere with Flir Lepton SPI.
    Each ``start_and_capture_file`` completes its own start/stop cycle, so the pipeline is fully idle
    between frames and again before ``flir()`` runs (sequential mode). Slightly more overhead than one
    start + two ``capture_file`` calls.

    Returns (nir_off_path, full_pair_saved).
    - nir_off_path is set when the NIR-OFF frame is saved, even if NIR-ON later fails.
    - full_pair_saved is True only when both NIR-OFF and NIR-ON were saved.
    """
    if picam2 is None:
        print("Picamera2 not initialized; skipping NIR pair")
        return None, False

    for attempt in range(1, NIR_PAIR_MAX_ATTEMPTS + 1):
        nir_off_saved: Optional[str] = None
        pair_saved = False
        try:
            pin.off()
            print(f"Pin state is: {pin.value}")

            time_off = datetime.now().strftime('%Y%m%d-%H%M%S')
            path_off = path.join(directory, f'{time_off}-NIR-OFF.jpg')
            print(f"taking photo: {path_off}")

            picam2.start_and_capture_file(path_off, show_preview=False)
            nir_off_saved = path_off
            pin.on()
            print(f"Pin state is: {pin.value}")
            time_on = datetime.now().strftime('%Y%m%d-%H%M%S')
            path_on = path.join(directory, f'{time_on}-NIR-ON.jpg')
            print(f"taking photo: {path_on}")
            picam2.start_and_capture_file(path_on, show_preview=False)
            pair_saved = True
        except Exception as exc:
            print(f"Camera failed to capture (attempt {attempt}/{NIR_PAIR_MAX_ATTEMPTS}): {exc}")
        finally:
            if picam2.started:
                try:
                    picam2.stop()
                except Exception as exc:
                    # stop() must not prevent returning a path after a successful capture_file
                    print(f"Picamera2 stop failed (attempt {attempt}/{NIR_PAIR_MAX_ATTEMPTS}): {exc}")

        if pair_saved:
            return nir_off_saved, True
        if nir_off_saved is not None:
            return nir_off_saved, False
        if attempt < NIR_PAIR_MAX_ATTEMPTS:
            time.sleep(NIR_PAIR_RETRY_DELAY_SEC)

    # get IMU and GPS data and save into image EXIF and XMP
    #add_metadata.add_metadata(image)
    # no IMU or GPS on the Pi Zero units currently

    return None, False

def flir(directory: str) -> None:
    """Flir Lepton 3.5: run capture then lepton sequentially in ``directory`` (no global chdir — safe for threads)."""
    try:
        subprocess.run(
            ["/home/pi/SU-WaterCam/capture"],
            check=True,
            timeout=20,
            cwd=directory,
        )
    except subprocess.TimeoutExpired:
        print("Check Lepton state - capture failed")
        subprocess.run(["/home/pi/SU-WaterCam/tools/lepton_reset.py"], check=True)

    try:
        subprocess.run(
            ["/home/pi/SU-WaterCam/lepton"],
            check=True,
            timeout=20,
            cwd=directory,
        )
    except subprocess.TimeoutExpired:
        print("Check Lepton state - radiometery failed")
        subprocess.run(["/home/pi/SU-WaterCam/tools/lepton_reset.py"], check=True)


def _run_flir_safe(directory: str) -> None:
    """Run Lepton pipeline; never raise (matches parallel-thread behavior so the button handler keeps running)."""
    try:
        flir(directory)
    except Exception as e:
        print(f"FLIR capture failed: {e}")


def _join_flir_thread(flir_thread: threading.Thread) -> None:
    """Bounded FLIR wait; if wedged, latch failure to avoid lock starvation and overlapping captures."""
    global _flir_thread_stuck
    flir_thread.join(timeout=FLIR_THREAD_TIMEOUT_SEC)
    if flir_thread.is_alive():
        _flir_thread_stuck = True
        print(
            f"FLIR thread did not finish within {FLIR_THREAD_TIMEOUT_SEC}s; "
            "capture is now disabled until service restart"
        )


def photos(filepath: str) -> Tuple[Optional[str], bool, str]:
    date = datetime.now().strftime('%Y%m%d-%H%M%S')
    directory = path.join(filepath, date)
    if not path.exists(directory):
        makedirs(directory)

    if nir_control_led is None:
        print("NIR control LED not initialized; skipping NIR pair, running FLIR only")
        basename: Optional[str] = None
        if PARALLEL_FLIR_WITH_PICAM:
            flir_thread = threading.Thread(target=_run_flir_safe, args=(directory,), name="flir")
            flir_started = False
            try:
                flir_thread.start()
                flir_started = True
            finally:
                if flir_started:
                    _join_flir_thread(flir_thread)
        else:
            _run_flir_safe(directory)
        return basename, False, directory

    basename: Optional[str] = None
    pair_saved = False
    if PARALLEL_FLIR_WITH_PICAM:
        flir_thread = threading.Thread(target=_run_flir_safe, args=(directory,), name="flir")
        flir_started = False
        try:
            flir_thread.start()
            flir_started = True
            basename, pair_saved = take_nir_pair(directory, nir_control_led)
        finally:
            if flir_started:
                _join_flir_thread(flir_thread)
    else:
        basename, pair_saved = take_nir_pair(directory, nir_control_led)
        _run_flir_safe(directory)

    return basename, pair_saved, directory

def single_press(button):
    print(f"Button DOWN on pin {button.pin}")

    blocked_by_stuck_flir = False
    nir_off_name: Optional[str] = None
    nir_pair_saved = False
    directory = ""
    try:
        with _capture_lock:
            if _flir_thread_stuck:
                blocked_by_stuck_flir = True
            else:
                if status_led is not None:
                    status_led.on()
                try:
                    nir_off_name, nir_pair_saved, directory = photos(filepath)
                finally:
                    if status_led is not None:
                        status_led.off()

            if blocked_by_stuck_flir:
                print("Capture disabled because prior FLIR thread appears stuck; restart the button service")
                _signal_capture_outcome(False)
                return

            _signal_capture_outcome(nir_pair_saved)
    except Exception as exc:
        print(f"Capture aborted: {exc}")
        _signal_capture_outcome(False)

    print(f"Photo path: {directory}")
    if nir_off_name is None:
        print("NIR pair failed before NIR-OFF was saved; no valid optical basename.")

# Using GPIO 5 because it is HIGH by default and we connect it to ground
# by pushing the button in. Already using GPIO 6 for the Lepton reset function 
# Adjust button GPIO as needed
if STARTUP_DELAY_SEC > 0:
    print(f"Startup delay {STARTUP_DELAY_SEC}s (camera / subsystem readiness)")
    time.sleep(STARTUP_DELAY_SEC)

_init_user_feedback()
_init_nir_control_led()

button = Button(5, bounce_time=0.05)
button.when_released = single_press  # Call on release
pause()
