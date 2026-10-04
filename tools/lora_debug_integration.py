#!/usr/bin/env python3
"""
LoRa Debug Integration

Answers the remote debug-status downlink (channel 50, command 01), which
LoRaHandler.decode() in tools/lora_handler_concurrent.py routes here.

Sending the request
-------------------
Send the downlink as the ASCII digits "5001" (any trailing digit works, e.g.
"50011"). The hex/TLV form "500100" does NOT work: decode() tries the TLV
parser first, reads it as channel 0x50 = 80, finds no handler and drops it.

The reply
---------
One uplink: compact JSON, UTF-8, sent raw (no TLV). It always starts with
{"dbg":1 so the server can tell it apart from sensor uplinks. Fields come in
priority order and are dropped from the end until the reply fits the mDot's
current payload limit, so it still goes out at low data rates (about 105 bytes
in full; US915 DR2 allows 125, DR1 53, DR0 11):

    dbg  format version (always present)
    up   uptime, hours
    ct   CPU temperature, deg C
    th   Pi throttled register (vcgencmd get_throttled), raw integer
    em   emergency mode, 0/1
    vi   WittyPi input voltage, V
    vo   WittyPi output voltage, V
    mp   memory used, %
    dp   root filesystem used, %
    ld   1-minute load average
    ts   node clock, Unix seconds

Any field that can't be read is left out rather than sent as a fake value.
It reads only /proc, /sys, vcgencmd and the WittyPi registers: no psutil, no
sensor sweep, nothing slow, because it runs in the LoRa daemon's listener
thread. tools/debug_status_command.py still builds the full report for
local use.
"""

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

FORMAT_VERSION = 1
DEFAULT_SIZE_LIMIT = 242   # mDot payload limit at the default data rate

Field = Tuple[str, Any]


def _uptime_hours() -> Optional[float]:
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]) / 3600, 1)
    except (OSError, ValueError, IndexError):
        return None


def _cpu_temp_c() -> Optional[float]:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def _memory_used_pct() -> Optional[int]:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                info[key] = int(rest.split()[0])
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
    except (OSError, ValueError, KeyError, ZeroDivisionError, IndexError):
        return None


def _disk_used_pct(path: str = "/") -> Optional[int]:
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return round(100 * (1 - free / total)) if total else None
    except OSError:
        return None


def _load_1min() -> Optional[float]:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def _throttled() -> Optional[int]:
    try:
        from tools.pi_power import get_throttled
    except ImportError:
        return None
    return get_throttled()


def _wittypi_volts(int_reg: int) -> Optional[float]:
    try:
        from tools.powerlog import reading
    except ImportError:
        return None
    text = reading(int_reg)
    return float(text) if text else None


def collect_compact_status(emergency_mode: Optional[bool] = None) -> List[Field]:
    """The reply's fields, in priority order, leaving out any that can't be read."""
    candidates: List[Field] = [
        ("dbg", FORMAT_VERSION),
        ("up", _uptime_hours()),
        ("ct", _cpu_temp_c()),
        ("th", _throttled()),
        ("em", None if emergency_mode is None else int(bool(emergency_mode))),
        ("vi", _wittypi_volts(1)),
        ("vo", _wittypi_volts(3)),
        ("mp", _memory_used_pct()),
        ("dp", _disk_used_pct()),
        ("ld", _load_1min()),
        ("ts", int(time.time())),
    ]
    return [(k, v) for k, v in candidates if v is not None]


def encode_for_limit(fields: List[Field], size_limit: int) -> Tuple[bytes, List[str]]:
    """Compact JSON of as many leading fields as fit size_limit, and the keys dropped."""
    kept: Dict[str, Any] = {}
    payload = b""
    for i, (key, value) in enumerate(fields):
        trial = json.dumps({**kept, key: value}, separators=(",", ":")).encode()
        if len(trial) > size_limit:
            return payload, [k for k, _ in fields[i:]]
        kept[key] = value
        payload = trial
    return payload, []


def handle_debug_status_request(size_limit: int = DEFAULT_SIZE_LIMIT,
                                emergency_mode: Optional[bool] = None) -> Dict[str, Any]:
    """Build the reply and return it ready to transmit.

    On success `data` is the compact JSON hex-encoded: LoRaHandler.transmit()
    treats a str as hex digits and sends it unchanged.
    """
    timestamp = datetime.now().isoformat()
    try:
        payload, dropped = encode_for_limit(collect_compact_status(emergency_mode), size_limit)
        if not payload:
            raise ValueError(f"payload limit {size_limit} B is too small for any reply")
        if dropped:
            print(f"🔍 Debug reply trimmed to {size_limit} B; left out {','.join(dropped)}")
        return {
            'command': 'debug_status_response',
            'timestamp': timestamp,
            'status': 'success',
            'data': payload.hex(),
            'size_bytes': len(payload),
            'dropped': dropped,
        }
    except Exception as e:
        print(f"❌ Debug status request failed: {e}")
        return {
            'command': 'debug_status_response',
            'timestamp': timestamp,
            'status': 'error',
            'error': str(e),
        }


def process_debug_command(command_data: str, size_limit: int = DEFAULT_SIZE_LIMIT) -> Optional[Dict[str, Any]]:
    """Handle a raw debug command string such as "50011" (type 50, command 01).

    Returns the response for a debug status command, otherwise None.
    """
    try:
        if command_data.startswith('5001'):
            print(f"🔍 Processing debug status command: {command_data}")
            return handle_debug_status_request(size_limit)
        return None
    except Exception as e:
        print(f"⚠️ Failed to process debug command {command_data}: {e}")
        return None


if __name__ == "__main__":
    response = handle_debug_status_request()
    print(f"status: {response['status']}")
    if response['status'] == 'success':
        print(f"payload ({response['size_bytes']} bytes): {bytes.fromhex(response['data']).decode('utf-8')}")
