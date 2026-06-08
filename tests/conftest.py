"""
Hardware stubs and shared fixtures for the WaterCam CI test suite.

All Raspberry Pi / hardware-specific Python modules are replaced with
lightweight stubs at import time, before any project code is loaded.
This lets the pipeline logic be exercised on a plain Linux runner without
any physical sensors, serial ports, or GPIO pins.
"""
import json
import os
import queue
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Add project root and tools/ to sys.path ───────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
for _p in [str(_REPO_ROOT), str(_REPO_ROOT / "tools")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Hardware module stubs ─────────────────────────────────────────────────
# Installed here (module level, not in a fixture) so they are in place
# before any test file triggers a project import.

def _stub(name, attrs=None):
    """Register an empty module stub in sys.modules if not already present."""
    if name not in sys.modules:
        m = types.ModuleType(name)
        if attrs:
            for k, v in attrs.items():
                setattr(m, k, v)
        sys.modules[name] = m
    return sys.modules[name]


# Adafruit / CircuitPython hardware
_stub("board", {"SCL": None, "SDA": None, "D17": 17, "D21": 21, "D22": 22,
                "I2C": lambda: MagicMock()})
_stub("busio")
_stub("digitalio")
_stub("adafruit_ahtx0")
_stub("adafruit_bno055")
_stub("adafruit_ads1x15")
_stub("adafruit_ads1x15.ads1115")
_stub("adafruit_ads1x15.analog_in")
_stub("adafruit_ina260")
_stub("adafruit_extended_bus")
_stub("smbus2")

# GPIO / Pi-specific
_stub("RPi")
_stub("RPi.GPIO")
_stub("gpiozero", {"LED": MagicMock})

# Camera
_stub("picamera2")
_stub("libcamera")

# GPS
_stub("gpsd")

# python-xmp-toolkit (needs exempi C library, not available in CI)
_stub("libxmp")
_stub("libxmp.consts")

# Preload so patch("tools.compress_segmented.compress_image") resolves at import
import tools.compress_segmented  # noqa: F401

# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_serial_port(monkeypatch, tmp_path):
    """Patch serial.Serial and the inter-process lock file for CI environments.

    Two things prevent LoRaHandler from constructing without real hardware:
      1. serial.Serial raises SerialException when /dev/ttyAMA5 is absent.
      2. os.open('/run/lock/watercam-lora.lock') raises PermissionError in
         environments where /run/lock is not writable.

    IMPORTANT: test_device_scenarios.py / test_utils.py replaces sys.modules['serial']
    with its own mock module.  To survive that, we patch the serial module object
    that lora_handler_concurrent actually holds (its .serial attribute), NOT
    sys.modules['serial'] which may be the wrong object after test_device_scenarios runs.
    """
    import os as _os
    import sys as _sys

    mock_ser = MagicMock()
    mock_ser.is_open = True
    mock_ser.in_waiting = 0
    mock_ser.read_until.return_value = b""
    mock_ser.readline.return_value = b""
    mock_ser.get_settings.return_value = {}

    # Patch via the module-level 'serial' reference held by lora_handler_concurrent.
    import tools.lora_handler_concurrent as _lhc
    _real_serial_mod = _lhc.serial
    monkeypatch.setattr(_real_serial_mod, "Serial", lambda *a, **kw: mock_ser)

    # If lora_handler_concurrent is also importable without the tools. prefix
    # and uses a different module object, patch that too.
    _bare = _sys.modules.get("lora_handler_concurrent")
    if _bare is not None and hasattr(_bare, "serial") and _bare.serial is not _real_serial_mod:
        monkeypatch.setattr(_bare.serial, "Serial", lambda *a, **kw: mock_ser)

    # Redirect the lock file from /run/lock/ to a writable tmp directory.
    _real_os_open = _os.open
    _lock_substitute = str(tmp_path / "watercam-lora.lock")

    def _patched_os_open(path, flags, mode=0o666, **kwargs):
        if "watercam-lora" in str(path) and not kwargs:
            return _real_os_open(_lock_substitute, flags, mode)
        return _real_os_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(_os, "open", _patched_os_open)

    yield mock_ser


@pytest.fixture
def data():
    """Minimal AHT20-style sensor dict used by test_aht20_data_flow.py."""
    return {"temperature_celsius": 22.5, "relative_humidity": 55}


@pytest.fixture
def sensor():
    """BNO055-style sensor stub for test_bno055_data_flow.py."""
    from unittest.mock import MagicMock
    s = MagicMock()
    s.calibration_status = (3, 3, 3, 3)
    s.euler = (0.0, 0.0, 0.0)
    s.quaternion = (1.0, 0.0, 0.0, 0.0)
    s.linear_acceleration = (0.0, 0.0, 0.0)
    s.gravity = (0.0, 0.0, 9.81)
    return s


@pytest.fixture
def sensor_data():
    """Basic sensor dict used by test_lora_reception.py."""
    return {
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "emergency_status": 0,
        "status_area_threshold": 10,
        "stage_threshold": 50,
        "monitoring_frequency": 60,
        "emergency_frequency": 5,
        "neighborhood_emergency_frequency": 30,
    }


@pytest.fixture
def flow_result():
    """Minimal sensor+encoding result for test_tttoken_full_sensor_data.py."""
    return {
        "temperature_celsius": 22.5,
        "relative_humidity": 55,
        "gps_lat": 43.158,
        "gps_lon": -76.138,
        "gps_alt": 130.0,
        "battery_percent": 75,
        "emergency_status": 0,
        "status_area_threshold": 10,
        "stage_threshold": 50,
        "monitoring_frequency": 60,
        "emergency_frequency": 5,
        "neighborhood_emergency_frequency": 30,
    }


@pytest.fixture
def wittypi_data():
    """Pre-fetched WittyPi status dict used by test_wittypi_lora.py."""
    return {
        "status": "wittypi_data_retrieved",
        "temperature": 23.0,
        "battery_voltage": 3.8,
        "internal_voltage": 5.05,
        "internal_current": 0.45,
    }


class _MockLoRaHandler:
    """Minimal LoRa handler stub that records transmissions for assertions."""

    def __init__(self):
        self.transmit_calls: list = []
        self.binary_calls: list = []
        self.transmit_queue = queue.Queue()
        self._lock = threading.Lock()

    def queue_transmit(self, data):
        with self._lock:
            self.transmit_calls.append(data)

    def queue_binary_transmit(self, data):
        with self._lock:
            self.binary_calls.append(data)

    def process_transmit_queue(self):
        pass

    def start_listening(self):
        pass

    def stop_listening(self):
        pass

    def set_runtime_callback(self, cb):
        pass

    def compressed_encoding(self, data: dict) -> bytes:
        # Minimal binary encoding: each numeric field as 3-byte TLV
        import struct
        out = bytearray()
        for k, v in data.items():
            if isinstance(v, (int, float)) and v is not True and v is not False:
                out += bytes([0x00, 0x00]) + struct.pack(">f", float(v))
        return bytes(out)


@pytest.fixture
def mock_lora_handler():
    return _MockLoRaHandler()


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level singletons before every test so tests are isolated."""
    # Import lazily to avoid triggering hardware init at collection time.
    try:
        import tools.lora_runtime_integration as lri
        lri._runtime_manager = None
    except Exception:
        pass
    try:
        import tools.lora_handler_concurrent as lhc
        lhc._lora_handler = None
    except Exception:
        pass
    yield
    try:
        import tools.lora_runtime_integration as lri
        lri._runtime_manager = None
    except Exception:
        pass
    try:
        import tools.lora_handler_concurrent as lhc
        lhc._lora_handler = None
    except Exception:
        pass


@pytest.fixture
def tmp_image_dir(tmp_path):
    """Temporary directory that mimics a per-cycle image output folder."""
    d = tmp_path / "20250424-120000"
    d.mkdir()
    return str(d)


@pytest.fixture
def synthetic_png(tmp_path):
    """16×16 binary PNG that simulates a segmentation output mask.

    Skipped automatically when numpy or Pillow are not installed (e.g. in a
    bare dev virtualenv); the CI pipeline installs both.
    """
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")

    arr = np.zeros((16, 16), dtype=np.uint8)
    arr[4:12, 4:12] = 255  # white square = flooded region
    img = Image.fromarray(arr, mode="L")
    p = tmp_path / "final_5_band_segmentation.png"
    img.save(str(p))
    return str(p)


@pytest.fixture
def runtime_config(tmp_path):
    """Isolated runtime_config.json for tests that read or write parameters."""
    cfg = {
        "area_threshold": 10,
        "stage_threshold": 50,
        "monitoring_frequency": 60,
        "emergency_frequency": 5,
        "photo_interval": 60,
        "neighborhood_emergency_frequency": 30,
        "emergency_mode": False,
        "debug_mode": False,
        "always_transmit_sensors": False,
        "max_retransmissions": 3,
        "auto_shutdown_enabled": True,
        "shutdown_iteration_limit": 3,
        "data_retention_days": 7,
        "backup_enabled": True,
        "iteration_count": 0,
        "ip_upload": {
            "enabled": False,
            "server_url": "http://localhost:8000",
            "api_key": "",
            "device_id": "watercam-test",
            "timeout_s": 2,
            "retry_attempts": 1,
            "retry_backoff_s": 0,
            "fallback_to_lora": False,
            "downlink_poll_interval_s": 60,
            "max_queue_depth": 48,
            "max_queue_age_days": 7,
        },
    }
    p = tmp_path / "runtime_config.json"
    p.write_text(json.dumps(cfg))
    return str(p)
