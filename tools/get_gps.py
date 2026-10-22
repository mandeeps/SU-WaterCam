#!/home/pi/SU-WaterCam/venv/bin/python

import gpsd2 as gpsd  # using py-gpsd2
from typing import List, Optional, Tuple, Any
import json
import os
import socket
import time

try:
    from tools import config_io
except ImportError:     # run from tools/ as a script
    import config_io

# The last good fix, for when there is no live one. A unit is stationary, and the
# modem's GNSS takes 40-50 s to fix after USB comes on, so a wake's first LoRa
# step (about 38 s after boot) would otherwise go without a position.
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gps_last_fix.json")
# runtime parameter gps_cache_max_age_h; 0 turns the cache off. A fixed camera takes
# a live fix once a day (tools/gps_window.py), so the cache is what most wakes use.
DEFAULT_CACHE_MAX_AGE_H = 720
#: The modem's NMEA port: present only while USB powers the modem (tools/usb_power.py)
GNSS_DEVICE = "/dev/ttyUSB1"
_MOVED_DEG = 1e-4               # about 10 m: rewrite the cache when the fix moves this much
_REFRESH_S = 3600               # or when the cached fix is this old

# What the last get_location_with_retry() returned: 'live', 'cached' or 'none',
# the fix's age in seconds (0 when live) and its horizontal error in metres
# when gpsd reports one. The IP uplink sends the age (channel 04 02).
last_fix = {"source": "none", "age_s": None, "error_m": None}

# gpsd connection. Every request (connect, welcome, ?WATCH, ?POLL) times out
# after REQUEST_TIMEOUT_S: py-gpsd2's own connect() and get_current() block on a
# readline with no timeout, so a gpsd that accepted the connection but stopped
# answering hung the capture step. This is not a wait for a fix: gpsd answers
# ?POLL straight away, fix or not, and waiting for a fix is
# get_location_with_retry()'s retries plus the cached fix. A failed connect, or
# any error on the connection, drops it, and the next call connects again.
GPSD_HOST = "127.0.0.1"
GPSD_PORT = 2947
REQUEST_TIMEOUT_S = 10

_gps_available = False      # connected to gpsd


def _disconnect() -> None:
    global _gps_available
    _gps_available = False
    for name in ("gpsd_stream", "gpsd_socket"):
        obj = getattr(gpsd, name, None)
        try:
            if obj is not None:
                obj.close()
        except Exception:
            pass
        try:
            setattr(gpsd, name, None)
        except Exception:
            pass


def _connect() -> bool:
    """gpsd2.connect() with a socket timeout on every read."""
    global _gps_available
    _disconnect()
    try:
        sock = socket.create_connection((GPSD_HOST, GPSD_PORT), timeout=REQUEST_TIMEOUT_S)
        sock.settimeout(REQUEST_TIMEOUT_S)
        stream = sock.makefile(mode="rw")
        gpsd.gpsd_socket, gpsd.gpsd_stream = sock, stream
        welcome = json.loads(stream.readline())
        if welcome.get("class") != "VERSION":
            raise RuntimeError("unexpected welcome from gpsd: is it a gpsd 3 server?")
        stream.write('?WATCH={"enable":true}\n')
        stream.flush()
        for _ in range(2):
            gpsd._parse_state_packet(json.loads(stream.readline()))
    except Exception as e:
        print(f"GPS: can't connect to gpsd ({e})")
        _disconnect()
        return False
    _gps_available = True
    return True


def _ensure_connected() -> bool:
    return _gps_available or _connect()


def _get_packet():
    if not _ensure_connected():
        return None
    try:
        packet = gpsd.get_current()
    except UserWarning:     # "GPS not active": gpsd answered; the connection is fine
        raise
    except Exception:
        _disconnect()       # timeout or a broken stream: connect again next time
        raise
    print(f'Current packet mode: {packet.mode}')
    if packet.mode < 2:
        return None
    return packet

def get_location() -> List[str]:
    packet = _get_packet()
    if not packet:
        return []

    try:
        device_str = gpsd.device()
    except Exception:
        device_str = "unknown"
    gps_data = [
        f"GPS Time UTC: {packet.time}\n",
        f"Time Local: {time.asctime(time.localtime(time.time()))}\n",
        f"Latitude: {packet.lat} degrees\n",
        f"Longitude: {packet.lon} degrees\n",
        f"Track: {packet.track}\n",
        f"Satellites: {packet.sats}\n",
        f"Error: {packet.error}\n",
        f"Precision: {packet.position_precision()}\n",
        f"Map URL: {packet.map_url()}\n",
        f"Device: {device_str}\n",
    ]
    if packet.mode >= 3:
        gps_data.append(f"Altitude: {packet.alt}\n")
    return gps_data

def _get_lat_lon_alt_with_packet() -> Tuple[dict, Optional[Any]]:
    """Internal function that returns GPS data and packet."""
    packet = _get_packet()

    if not packet:
        return ({}, None)

    try:
        lat = packet.lat
        lon = packet.lon
        alt = packet.alt if packet.mode >= 3 else None

        return ({
            'gps_lat': lat,
            'gps_lon': lon,
            'gps_alt': alt
        }, packet)
    except AttributeError:
        return ({}, None)

def get_lat_lon_alt() -> dict:
    """Get GPS latitude, longitude, and altitude as a dictionary.
    Returns only the dict for backward compatibility; use get_location_with_retry() for (dict, packet).
    """
    gps_data, _ = _get_lat_lon_alt_with_packet()
    return gps_data


def get_loc() -> List[str]:
    """Alias for get_location() for backward compatibility (e.g. add_metadata logging)."""
    return get_location()


def _cache_max_age_s() -> float:
    try:
        from tools.lora_runtime_integration import get_parameter
        hours = float(get_parameter('gps_cache_max_age_h', DEFAULT_CACHE_MAX_AGE_H))
    except Exception:
        hours = DEFAULT_CACHE_MAX_AGE_H
    return max(0.0, hours) * 3600


def _read_cache() -> Optional[dict]:
    try:
        with open(CACHE_PATH) as f:
            c = json.load(f)
        if isinstance(c.get("gps_lat"), (int, float)) and isinstance(c.get("gps_lon"), (int, float)):
            return c
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _save_fix(location: dict) -> None:
    """Remember a live fix (atomically), when it moved or the cache is getting old."""
    if not all(isinstance(location.get(k), (int, float)) for k in ("gps_lat", "gps_lon")):
        return
    now = time.time()
    old = _read_cache()
    if old and abs(old["gps_lat"] - location["gps_lat"]) < _MOVED_DEG \
            and abs(old["gps_lon"] - location["gps_lon"]) < _MOVED_DEG \
            and now - old.get("fix_time", 0) < _REFRESH_S:
        return
    entry = {"gps_lat": location["gps_lat"], "gps_lon": location["gps_lon"],
             "gps_alt": location.get("gps_alt"), "fix_time": now}
    try:
        # atomic and fsynced; 0o600 because it says where the unit is
        config_io.write_json(CACHE_PATH, entry, mode=0o600)
    except (OSError, TypeError, ValueError) as e:
        print(f"⚠️ GPS: couldn't save the last fix: {e}")


def cached_fix() -> Tuple[dict, Optional[float]]:
    """The last saved fix, if it's within gps_cache_max_age_h: (dict, age in s)."""
    max_age = _cache_max_age_s()
    c = _read_cache()
    if not c or max_age <= 0:
        return ({}, None)
    age = time.time() - float(c.get("fix_time", 0))
    if age < 0 or age > max_age:
        return ({}, None)
    return ({"gps_lat": c["gps_lat"], "gps_lon": c["gps_lon"], "gps_alt": c.get("gps_alt")}, age)


def get_location_with_retry(max_retries: int = 3, delay: float = 1.0,
                            use_cache: bool = True) -> Tuple[dict, Optional[Any]]:
    """Get location with retry logic for better reliability.

    A live fix is saved (CACHE_PATH). With no live fix after the retries, the last
    saved fix is returned instead if it's within gps_cache_max_age_h (default
    two days); its packet is None, so photos taken then get no EXIF position.
    last_fix records which it was.

    Returns:
        tuple: (dict, packet) where dict contains GPS data (or empty dict if unavailable)
               and packet is the raw GPS packet (or None if unavailable or cached)
    """
    # With USB off there is no GNSS: don't spend seconds asking gpsd
    if os.path.exists(GNSS_DEVICE) and _ensure_connected():
        for attempt in range(max_retries):
            try:
                location, packet = _get_lat_lon_alt_with_packet()
            except Exception as e:   # gpsd answers "GPS not active" until it has the modem's port
                location, packet = {}, None
                if attempt == max_retries - 1:
                    print(f"GPS: no live fix ({e})")
            if location:
                _save_fix(location)
                last_fix.update(source="live", age_s=0.0, error_m=_horizontal_error_m(packet))
                return (location, packet)
            if attempt < max_retries - 1:
                time.sleep(delay)
    if use_cache:
        location, age = cached_fix()
        if location:
            print(f"GPS: no live fix; using the last fix from {age / 3600:.1f} h ago")
            last_fix.update(source="cached", age_s=age, error_m=None)
            return (location, None)
    last_fix.update(source="none", age_s=None, error_m=None)
    return ({}, None)


def _horizontal_error_m(packet) -> Optional[float]:
    """gpsd's estimated horizontal error (95%), in metres, if it gives one."""
    try:
        err = packet.error or {}
        xs = [float(err[k]) for k in ("x", "y") if err.get(k) not in (None, "", 0)]
        return max(xs) if xs else None
    except (AttributeError, TypeError, ValueError):
        return None

if __name__ == "__main__":
    data = get_location()
    for line in data:
        print(line)

    gps, packet = get_location_with_retry()
    print(f'GPS Data: {gps}')
    print(f'GPS Packet: {packet}')

    if gps:
        print(f'Latitude: {gps.get("gps_lat", "N/A")}')
        print(f'Longitude: {gps.get("gps_lon", "N/A")}')
        print(f'Altitude: {gps.get("gps_alt", "N/A")}')
    else:
        print('No GPS data available')
