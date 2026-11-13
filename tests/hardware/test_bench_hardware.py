"""Bench-unit hardware tests: each device answers and gives a plausible reading.

Run on a bench unit with ticktalk stopped (docs/BENCH_HARDWARE_TESTS.md):

    venv/bin/python -m pytest --hardware -rs tests/hardware

A device the unit doesn't have is skipped and named in the report; one that is
fitted but wrong fails. Side effects, all undone or confined to tmp_path:
- the IR-cut test drives GPIO 21 and takes two photos (into tmp_path);
- the Lepton test runs the capture binary (output into tmp_path);
- the SegFormer test sends the daemon a small synthetic image (both in tmp_path).
Nothing here transmits over LoRa or IP, writes a Witty Pi alarm, or changes the
unit's configuration.
"""
import glob
import json
import os
import socket
import subprocess
import time

import numpy as np
import pytest

from tests.hardware.conftest import REPO, i2c_read, need

WITTYPI_ADDR = 0x08
BNO055_ADDR, BNO055_CHIP_ID = 0x28, 0xA0
AHT20_ADDR = 0x38
SEGFORMER_SOCKET = "/run/segformer/segformer.sock"
SPI_DEV = "/dev/spidev0.0"


# --- Power: Witty Pi, RTC, Pi supply ------------------------------------------

def test_wittypi_answers_with_rails_in_range(require_i2c_tools):
    from tools.wittypi_control import get_wittypi_status
    st = get_wittypi_status()
    assert st["status"] == "wittypi_data_retrieved", st
    # Output to the Pi: initial_health_check fails a unit below 4.7 V
    assert 4.7 <= st["internal_voltage"] <= 5.4, st
    # Input from the battery or supply; reads 0.4-0.5 V low on several boards
    assert st["battery_voltage"] is not None and st["battery_voltage"] > 3.0, st
    assert -20 <= st["temperature"] <= 85, st


def test_wittypi_firmware_is_readable(require_i2c_tools):
    from tools.initial_health_check import read_wittypi_firmware_id
    fw = read_wittypi_firmware_id()
    assert fw is not None, "no firmware ID from the Witty Pi at 0x08"


def test_rtc_keeps_time(require_i2c_tools):
    from tools.rtc_early_clock import rtc_timestamp
    rtc = rtc_timestamp()
    assert rtc is not None, "RTC unreadable or its oscillator stopped (time lost on power-off)"
    assert abs(rtc - time.time()) < 120, f"RTC {rtc - time.time():+.0f} s from system time"


def test_pi_is_not_throttled_now():
    from tools.pi_power import get_throttled
    t = get_throttled()
    assert t is not None, "vcgencmd get_throttled failed"
    now = {0: "under-voltage", 1: "frequency capped", 2: "throttled", 3: "soft temperature limit"}
    active = [why for bit, why in now.items() if t & (1 << bit)]
    assert not active, f"throttled=0x{t:x}: {', '.join(active)} now"


def test_no_under_voltage_since_boot():
    """The 5 V rail dipped at some point this boot (a loose USB-C plug, a weak
    supply): the Pi's own record, independent of the Witty Pi's reading."""
    from tools.pi_power import get_throttled
    t = get_throttled()
    assert t is not None and not t & (1 << 16), f"throttled=0x{t:x}: under-voltage since boot"


def test_battery_reading_has_a_source(tmp_path, monkeypatch):
    import tools.battery_manager as bm
    monkeypatch.setattr(bm, "STATE_FILE", str(tmp_path / "battery_state.json"))  # INA260 path writes it
    st = bm.get_battery_status()
    assert st["battery_source"] != "unavailable", st
    if st["battery_source"] == "wittypi_output":
        assert 4.7 <= st["output_voltage_v"] <= 5.4, st
    else:
        assert st["battery_pct"] is not None and 0 <= st["battery_pct"] <= 100, st


# --- I2C sensors --------------------------------------------------------------

def test_wittypi_on_i2c(require_i2c_tools):
    assert i2c_read(WITTYPI_ADDR, 0) is not None, "nothing answers at 0x08 on bus 1"


def test_imu_chip_id(require_i2c_tools):
    chip = i2c_read(BNO055_ADDR, 0x00)
    need(chip is not None, "BNO055 IMU at 0x28")
    assert chip == BNO055_CHIP_ID, f"0x28 answers with chip ID 0x{chip:02x}, not a BNO055 (0xa0)"


def test_temperature_humidity_sensor(require_i2c_tools):
    need(i2c_read(AHT20_ADDR) is not None, "AHT20 at 0x38")
    from tools.aht20_temperature import get_aht20
    r = get_aht20()
    assert r, "AHT20 answered on I2C but returned no reading"
    assert -30 <= r["temperature_celsius"] <= 70, r
    assert 0 <= r["relative_humidity"] <= 100, r


# --- Cameras ------------------------------------------------------------------

def _camera_count():
    from picamera2 import Picamera2
    return len(Picamera2.global_camera_info())


def test_optical_camera_is_detected():
    assert _camera_count() >= 1, "libcamera sees no camera (ribbon cable, camera_auto_detect)"


def _nir_pair(directory):
    """The production NIR pair (tt_take_photos.take_two_photos): filter in, settle,
    lock exposure and white balance, shoot; filter out, settle, shoot."""
    from gpiozero import LED
    from picamera2 import Picamera2
    cam = Picamera2()
    pin = None
    off, on = os.path.join(directory, "t-NIR-OFF.jpg"), os.path.join(directory, "t-NIR-ON.jpg")
    try:
        cam.configure(cam.create_still_configuration(main={"format": "RGB888", "size": (2592, 1944)}))
        pin = LED(21)
        cam.start()
        pin.off()                                  # IR-cut filter in
        time.sleep(0.5 + 2.0)                      # filter move + AWB/AE settle
        meta = cam.capture_metadata()
        cam.set_controls({"AwbEnable": False, "ColourGains": meta.get("ColourGains", (1.0, 1.0)),
                          "AeEnable": False, "ExposureTime": meta.get("ExposureTime", 10_000),
                          "AnalogueGain": meta.get("AnalogueGain", 1.0)})
        time.sleep(0.1)
        cam.capture_file(off)
        pin.on()                                   # filter out
        time.sleep(0.5)
        cam.capture_file(on)
    finally:
        try:
            cam.stop()
        finally:
            time.sleep(0.5)
            cam.close()
            if pin is not None:
                pin.off()
                pin.close()
    return off, on


def test_ir_cut_filter_switches(tmp_path):
    """The two frames of a NIR pair differ (tools/nir_filter_check.py): a filter
    that doesn't move makes the NIR band a copy of visible light."""
    need(_camera_count() >= 1, "optical camera")
    from tools.nir_filter_check import SAME_MAX_DIFF, STATUS_OK, check_pair
    off, on = _nir_pair(str(tmp_path))
    r = check_pair(off, on)
    if r is None:
        pytest.skip("scene too dark to judge the filter (IR-cut frame mean < 20): light the view")
    assert r["status"] == STATUS_OK, (
        f"IR-cut filter not switching: frames differ by {r['diff']:.1f} (needs >= {SAME_MAX_DIFF}); "
        f"check the filter's ribbon/wiring and GPIO 21")


def test_thermal_camera_captures_a_frame(tmp_path):
    binary = os.path.join(REPO, "capture")
    need(os.path.exists(SPI_DEV) and os.path.isfile(binary), f"Lepton on {SPI_DEV} with {binary}")
    r = subprocess.run([binary], cwd=str(tmp_path), capture_output=True, text=True, timeout=30)
    frames = glob.glob(str(tmp_path / "IMG_*.pgm"))
    assert frames, f"no frame from the Lepton (exit {r.returncode}): {r.stderr.strip()[-200:]}"
    from PIL import Image
    img = np.asarray(Image.open(frames[0]))
    assert img.size >= 160 * 120, f"frame is {img.shape}"
    assert img.std() > 0, "every pixel the same: the Lepton returned a blank frame (try tools/lepton_reset.py)"


# --- Daemons: SegFormer, LoRa, GPS --------------------------------------------

def test_segformer_daemon_segments_an_image(tmp_path):
    need(os.path.exists(SEGFORMER_SOCKET), f"SegFormer daemon ({SEGFORMER_SOCKET})")
    import rasterio
    from rasterio.transform import from_origin
    from tools.segformer_client import segformer_via_daemon
    h, w = 240, 320
    yy, xx = np.mgrid[0:h, 0:w]
    bands = np.stack([(xx * 255 // w), (yy * 255 // h), ((xx + yy) * 255 // (w + h)),
                      np.full((h, w), 128), (255 - xx * 255 // w)]).astype(np.uint8)
    tiff = str(tmp_path / "color_preserved_5_band.tiff")
    with rasterio.open(tiff, "w", driver="GTiff", height=h, width=w, count=5, dtype="uint8",
                       transform=from_origin(0, 0, 1, 1)) as dst:
        dst.write(bands)
        dst.update_tags(BAND_ORDER="red,green,blue,thermal,nir")
        for i, name in enumerate(("red", "green", "blue", "thermal", "nir"), start=1):
            dst.set_band_description(i, name)
    out = str(tmp_path / "segmentation.png")
    info = {}
    assert segformer_via_daemon(tiff, out, socket_path=SEGFORMER_SOCKET, info=info), info
    from PIL import Image
    assert Image.open(out).size == (w, h)
    assert info.get("inference_ms") is not None, info


def _lora():
    from tools.lora_handler_concurrent import LORA_DAEMON_SOCKET_PATH, get_lora_handler
    need(os.path.exists(LORA_DAEMON_SOCKET_PATH), f"LoRa daemon ({LORA_DAEMON_SOCKET_PATH})")
    handler = get_lora_handler(socket_path=LORA_DAEMON_SOCKET_PATH)
    assert handler is not None, "LoRa daemon socket exists but no client could connect"
    return handler


def test_lora_daemon_answers():
    depth = _lora().get_queue_depth()          # in the daemon's memory: no AT, no radio
    assert isinstance(depth, int) and depth >= 0, depth


def test_mdot_has_joined():
    """AT+NJS? on the mDot: a local query, nothing is transmitted. It reports the
    saved session, so it stays joined with no gateway in range."""
    assert _lora().is_joined() is True, "mDot not joined to the LoRaWAN network (keys, sub-band, gateway)"


def _gpsd(command: bytes, want: str, timeout_s: float = 5.0):
    with socket.create_connection(("127.0.0.1", 2947), timeout=timeout_s) as s:
        s.sendall(command)
        buf, end = b"", time.monotonic() + timeout_s
        while time.monotonic() < end:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            for line in buf.split(b"\n"):
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("class") == want:
                    return msg
    return None


def test_gpsd_answers():
    try:
        version = _gpsd(b'?VERSION;\n', "VERSION")
    except OSError as e:
        pytest.fail(f"gpsd not reachable on 127.0.0.1:2947: {e}")
    assert version is not None, "gpsd connected but sent no VERSION"


def test_gnss_receiver_is_seen_by_gpsd():
    from tools.get_gps import GNSS_DEVICE
    need(os.path.exists(GNSS_DEVICE), f"GNSS receiver at {GNSS_DEVICE} (modem powered)")
    devices = _gpsd(b'?DEVICES;\n', "DEVICES") or {}
    paths = [d.get("path") for d in devices.get("devices", [])]
    assert GNSS_DEVICE in paths, f"gpsd lists {paths}, not {GNSS_DEVICE}"
