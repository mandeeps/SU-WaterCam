#!/usr/bin/env python3
"""
LoRa Debug Integration

Builds the node's status reply: the answer to the status request (command
50 01) and the ack of every server command frame (tools/command_log.py), which
LoRaHandler.decode() in tools/lora_handler_concurrent.py routes here.

Sending the request
-------------------
The server sends it as a command frame like any other: [seq] 50 01
(API app/commands.py, kind status_request). By hand, e.g. in ChirpStack's
enqueue form: 005001 (seq 0: applied every time, acked as "ak":0).

The reply
---------
One uplink: compact JSON, UTF-8, sent raw (no TLV). It starts with {"ak": when
it acks a command, else {"dbg":1, so the server can tell it apart from sensor
uplinks. Fields come in priority order and are dropped from the end until the
reply fits the mDot's current payload limit, so it still goes out at low data
rates (about 115 bytes in full; US915 DR2 allows 125, DR1 53, DR0 11; the ack
alone, {"ak":123}, is 10):

    ak   seq of the command this reply acks (only in an ack)
    ar   its result when not 0: 1 partly applied, 2 rejected
    sc   the wake schedule set by 15 95: "HHMM-HHMM/interval/awake"
    ut   1 while the configured IP server is untrusted (IP readings stay queued)
    dbg  format version (always present)
    up   uptime, hours
    ct   CPU temperature, deg C
    th   Pi throttled register (vcgencmd get_throttled), raw integer
    em   emergency mode, 0/1
    ipr  IP uploads the server requested (17 97) still to do; only when some are
    vi   WittyPi input voltage, V
    vo   WittyPi output voltage, V
    mp   memory used, %
    dp   root filesystem used, %
    ld   1-minute load average
    ts   node clock, Unix seconds
    sd   store-and-forward entries dropped unsent since install (LoRa + IP queues)

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


def _software_version() -> Optional[str]:
    try:
        from tools.node_version import short_version
        return short_version()
    except Exception:   # noqa: BLE001 - a status reply must never fail over this
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


def _sf_dropped() -> Optional[int]:
    try:
        from tools import drop_counter
        return drop_counter.total()
    except Exception:   # noqa: BLE001 - a status reply must never fail over this
        return None


def _lora_daemon_unavailable() -> Optional[int]:
    """Cycles whose LoRa packets were queued because the daemon was down; only when any."""
    try:
        from tools import drop_counter
        return drop_counter.events().get("lora_daemon_unavailable") or None
    except Exception:   # noqa: BLE001 - a status reply must never fail over this
        return None


def _ip_uploads_requested() -> Optional[int]:
    try:
        from tools.ip_fallback import pending_wakes
        return pending_wakes() or None
    except Exception:   # noqa: BLE001 - a status reply must never fail over this
        return None


def _schedule_label() -> Optional[str]:
    try:
        from tools import schedule_control
        s = schedule_control.current()
        return s.label() if s else None
    except Exception:   # noqa: BLE001 - a status reply must never fail over this
        return None


def _ip_server_untrusted() -> Optional[int]:
    try:
        from tools.initial_health_check import ip_server_untrusted
        return 1 if ip_server_untrusted() else None
    except Exception:   # noqa: BLE001 - a status reply must never fail over this
        return None


def collect_compact_status(emergency_mode: Optional[bool] = None,
                           ack: Optional[Tuple[int, int]] = None) -> List[Field]:
    """The reply's fields, in priority order, leaving out any that can't be read.

    ack = (seq, result) of the server command this reply answers: first, so it
    survives trimming even at DR0 ({"ak":123} is 10 B); the result only when
    not 0 (applied).
    """
    candidates: List[Field] = []
    if ack is not None:
        seq, result = ack
        candidates += [("ak", int(seq)), ("ar", int(result) or None)]
    candidates += [
        ("dbg", FORMAT_VERSION),
        # second: the server confirms emergency on/off from it, so it must survive
        # trimming to a small payload limit ({"dbg":1,"em":1} is 16 B)
        ("em", None if emergency_mode is None else int(bool(emergency_mode))),
        # IP uploads the server asked for (17 97) still to do; only while some are
        # (tools/ip_fallback.py), so the server can confirm the request arrived
        ("ipr", _ip_uploads_requested()),
        # software release, e.g. "v2026.10.1-dirty" (tools/node_version.py)
        ("v", _software_version()),
        ("up", _uptime_hours()),
        ("ct", _cpu_temp_c()),
        ("th", _throttled()),
        ("vi", _wittypi_volts(1)),
        ("vo", _wittypi_volts(3)),
        ("mp", _memory_used_pct()),
        ("dp", _disk_used_pct()),
        ("ld", _load_1min()),
        ("ts", int(time.time())),
        ("sd", _sf_dropped()),
        # cycles queued because the LoRa daemon was down (tools/drop_counter.py)
        ("du", _lora_daemon_unavailable()),
        # the wake schedule set by 15 95, "HHMM-HHMM/interval/awake"
        ("sc", _schedule_label()),
        # 1 while the configured IP server is untrusted, so IP readings stay queued
        ("ut", _ip_server_untrusted()),
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
                                emergency_mode: Optional[bool] = None,
                                ack: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """Build the reply and return it ready to transmit.

    On success `data` is the compact JSON hex-encoded: LoRaHandler.transmit()
    treats a str as hex digits and sends it unchanged.
    """
    timestamp = datetime.now().isoformat()
    try:
        payload, dropped = encode_for_limit(collect_compact_status(emergency_mode, ack), size_limit)
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


if __name__ == "__main__":
    response = handle_debug_status_request()
    print(f"status: {response['status']}")
    if response['status'] == 'success':
        print(f"payload ({response['size_bytes']} bytes): {bytes.fromhex(response['data']).decode('utf-8')}")
