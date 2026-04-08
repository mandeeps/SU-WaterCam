#!/home/pi/SU-WaterCam/venv/bin/python3

# alternate button-service script for use on 32bit Pi Zero
# This version is meant to be used with a multispectral cameras-only unit
# for manually collecting images and radiometric data. It is faster than
# spawning another Python process and starting PiCamera2 every time photos are
# taken. This is helpful on the Pi Zero

from os import path, makedirs, listdir, rename, environ
from datetime import datetime
from typing import Optional, Tuple
import subprocess
import threading
import time
from glob import glob
from signal import pause
from picamera2 import Picamera2
from gpiozero import LED, Button, Buzzer


def _infer_repo_root() -> str:
    """SU-WaterCam root (where ``capture`` / ``lepton`` live).

    The script may run as ``button_hold_camera.py`` at repo root or as ``tools/button_hold_camera.py``;
    dirname(__file__) alone is wrong for the latter. Prefer walking up until both native helpers exist,
    else if this file is under ``tools/``, use the parent directory.
    Override with env ``WATERCAM_REPO`` when needed.
    """
    override = environ.get("WATERCAM_REPO")
    if override:
        return path.normpath(override)
    script_dir = path.dirname(path.realpath(__file__))
    d = script_dir
    for _ in range(12):
        if path.isfile(path.join(d, "capture")) and path.isfile(path.join(d, "lepton")):
            return path.normpath(d)
        parent = path.dirname(d)
        if parent == d:
            break
        d = parent
    if path.basename(script_dir) == "tools":
        return path.normpath(path.join(script_dir, ".."))
    return path.normpath(script_dir)


REPO_ROOT = _infer_repo_root()
IMAGES_ROOT = path.join(REPO_ROOT, "images")
try:
    makedirs(IMAGES_ROOT, exist_ok=True)
except OSError as exc:
    print(f"Could not create images directory {IMAGES_ROOT}: {exc}")

# Flir timing vs NIR (see tools/benchmark_capture_workflow.py):
#   True  — benchmark D: Flir runs in a thread while single-session NIR runs on the main thread (fastest).
#   False — benchmark C: single-session NIR, then Flir sequentially (more reliable if SPI/CSI clashes).
PARALLEL_FLIR_WITH_PICAM = True
if environ.get("BUTTON_HOLD_SEQUENTIAL_FLIR", "").strip().lower() in ("1", "true", "yes"):
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
    """Two full-resolution stills in one Picamera2 session: ``start()`` → OFF → ON → ``stop()``.

    Faster than two ``start_and_capture_file`` calls (benchmark C/D NIR path). Pipeline is idle after
    ``stop()`` before ``flir()`` unless ``PARALLEL_FLIR_WITH_PICAM`` overlaps Flir in another thread.

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
            time_off = datetime.now().strftime('%Y%m%d-%H%M%S')
            path_off = path.join(directory, f'{time_off}-NIR-OFF.jpg')
            time_on = datetime.now().strftime('%Y%m%d-%H%M%S')
            path_on = path.join(directory, f'{time_on}-NIR-ON.jpg')

            picam2.start()
            pin.off()
            print(f"Pin state is: {pin.value}")
            print(f"taking photo: {path_off}")
            picam2.capture_file(path_off)
            if (not path.exists(path_off)) or path.getsize(path_off) == 0:
                raise RuntimeError(f"NIR-OFF file not written: {path_off}")
            nir_off_saved = path_off
            pin.on()
            print(f"Pin state is: {pin.value}")
            print(f"taking photo: {path_on}")
            picam2.capture_file(path_on)
            if (not path.exists(path_on)) or path.getsize(path_on) == 0:
                raise RuntimeError(f"NIR-ON file not written: {path_on}")
            pair_saved = True
        except Exception as exc:
            print(f"Camera failed to capture (attempt {attempt}/{NIR_PAIR_MAX_ATTEMPTS}): {exc}")
        finally:
            try:
                picam2.stop()
            except Exception as exc:
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

def flir(session_dir: str) -> None:
    """Flir Lepton 3.5: run capture then lepton sequentially in ``session_dir`` (same folder as NIR stills).

    The ``capture`` and ``lepton`` executables are in ``REPO_ROOT`` (SU-WaterCam), not under ``tools/``.
    Only ``tools/lepton_reset.py`` lives in ``tools/`` (Python helper for SPI reset).
    """

    def _reset_lepton() -> None:
        try:
            subprocess.run([path.join(REPO_ROOT, "tools", "lepton_reset.py")], check=True)
        except Exception as exc:
            print(f"Lepton reset failed: {exc}")

    def _run_flir_cmd(label: str, command: list[str]) -> bool:
        try:
            # Discard child stdout/stderr: chatty binaries; buffering PIPEs can deadlock if stderr fills
            # the OS pipe buffer while the parent waits (stdout=DEVNULL, stderr=PIPE was unsafe).
            proc = subprocess.run(
                command,
                timeout=20,
                cwd=session_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"{label} timed out; resetting Lepton")
            _reset_lepton()
            return False
        except Exception as exc:
            print(f"{label} failed before execution completed: {exc}")
            _reset_lepton()
            return False

        if proc.returncode != 0:
            print(f"{label} failed (rc={proc.returncode})")
            _reset_lepton()
            return False

        # Post-execution output checks for expected Flir artifacts.
        try:
            files = listdir(session_dir)
        except Exception as exc:
            print(f"{label} completed but could not inspect output directory: {exc}")
            _reset_lepton()
            return False

        if label == "capture":
            has_pgm = any(name.lower().endswith(".pgm") for name in files)
            if not has_pgm:
                print("capture completed but no .pgm output file was found in capture directory")
                _reset_lepton()
                return False
        elif label == "lepton":
            has_temp_csv = any(
                name.startswith("lepton_temp_") and name.lower().endswith(".csv")
                for name in files
            )
            if not has_temp_csv:
                print("lepton completed but no lepton_temp_*.csv output file was found in capture directory")
                _reset_lepton()
                return False

        return True

    def _rename_flir_to_timestamped_names() -> None:
        """Match tt_take_photos: lepton_{date}.pgm and temperatures_{date}.csv in the session folder."""
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        pgms = sorted(glob(path.join(session_dir, "IMG_*.pgm")))
        if pgms:
            dst = path.join(session_dir, f"lepton_{ts}.pgm")
            try:
                rename(pgms[0], dst)
                print(f"Renamed FLIR PGM to {path.basename(dst)}")
            except Exception as exc:
                print(f"Could not rename {pgms[0]} -> {dst}: {exc}")
        csvs = sorted(glob(path.join(session_dir, "lepton_temp_*.csv")))
        if csvs:
            dst = path.join(session_dir, f"temperatures_{ts}.csv")
            try:
                rename(csvs[0], dst)
                print(f"Renamed FLIR CSV to {path.basename(dst)}")
            except Exception as exc:
                print(f"Could not rename {csvs[0]} -> {dst}: {exc}")

    # If capture fails, skip radiometry call and let the next button press retry after reset.
    if not _run_flir_cmd("capture", [path.join(REPO_ROOT, "capture")]):
        return
    if not _run_flir_cmd("lepton", [path.join(REPO_ROOT, "lepton")]):
        return
    _rename_flir_to_timestamped_names()


def _run_flir_safe(session_dir: str) -> None:
    """Run Lepton pipeline; never raise (matches parallel-thread behavior so the button handler keeps running)."""
    try:
        flir(session_dir)
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


def photos(images_root: str) -> Tuple[Optional[str], bool, str]:
    """Create one session folder per press; NIR JPEGs and FLIR PGM/CSV all use that same path."""
    date = datetime.now().strftime('%Y%m%d-%H%M%S')
    session_dir = path.join(images_root, date)
    if not path.exists(session_dir):
        makedirs(session_dir)

    if nir_control_led is None:
        print("NIR control LED not initialized; skipping NIR pair, running FLIR only")
        basename: Optional[str] = None
        if PARALLEL_FLIR_WITH_PICAM:
            flir_thread = threading.Thread(target=_run_flir_safe, args=(session_dir,), name="flir")
            flir_started = False
            try:
                flir_thread.start()
                flir_started = True
            finally:
                if flir_started:
                    _join_flir_thread(flir_thread)
        else:
            _run_flir_safe(session_dir)
        return basename, False, session_dir

    basename: Optional[str] = None
    pair_saved = False
    if PARALLEL_FLIR_WITH_PICAM:
        flir_thread = threading.Thread(target=_run_flir_safe, args=(session_dir,), name="flir")
        flir_started = False
        try:
            flir_thread.start()
            flir_started = True
            basename, pair_saved = take_nir_pair(session_dir, nir_control_led)
        finally:
            if flir_started:
                _join_flir_thread(flir_thread)
    else:
        basename, pair_saved = take_nir_pair(session_dir, nir_control_led)
        _run_flir_safe(session_dir)

    return basename, pair_saved, session_dir

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
                try:
                    if status_led is not None:
                        status_led.on()
                    nir_off_name, nir_pair_saved, directory = photos(IMAGES_ROOT)
                finally:
                    if status_led is not None:
                        status_led.off()

        # Feedback can take ~2s (blocking blink/beep); run only after releasing the lock so the next
        # press is not delayed — the lock serializes capture hardware, not UI.
        if blocked_by_stuck_flir:
            print("Capture disabled because prior FLIR thread appears stuck; restart the button service")
            _signal_capture_outcome(False)
            return

        _signal_capture_outcome(nir_pair_saved)
    except Exception as exc:
        print(f"Capture aborted: {exc}")
        _signal_capture_outcome(False)

    print(f"Photo path: {directory}")
    if nir_control_led is not None and nir_off_name is None:
        print("NIR pair failed before NIR-OFF was saved; no valid optical basename.")

# Using GPIO 5 because it is HIGH by default and we connect it to ground
# by pushing the button in. Already using GPIO 6 for the Lepton reset function 
# Adjust button GPIO as needed
if STARTUP_DELAY_SEC > 0:
    print(f"Startup delay {STARTUP_DELAY_SEC}s (camera / subsystem readiness)")
    time.sleep(STARTUP_DELAY_SEC)

_init_user_feedback()
_init_nir_control_led()

print(
    f"button_hold_camera: REPO_ROOT={REPO_ROOT} IMAGES_ROOT={IMAGES_ROOT} "
    f"PARALLEL_FLIR_WITH_PICAM={PARALLEL_FLIR_WITH_PICAM} (False=C reliability, True=D fastest)"
)
for _name in ("capture", "lepton"):
    _bp = path.join(REPO_ROOT, _name)
    if not path.isfile(_bp):
        print(f"Warning: expected FLIR helper missing or not a file: {_bp}")

button = Button(5, bounce_time=0.05)
button.when_released = single_press  # Call on release
pause()
