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

# ─────────────────────────────────────────────────────────────────────────


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
        },
    }
    p = tmp_path / "runtime_config.json"
    p.write_text(json.dumps(cfg))
    return str(p)
