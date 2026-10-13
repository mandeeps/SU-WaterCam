#!/home/pi/SU-WaterCam/venv/bin/python

import gpsd2 as gpsd  # using py-gpsd2
from typing import List, Optional, Tuple, Any
import json
import os
import tempfile
import time

# The last good fix, for when there is no live one. A unit is stationary, and the
# modem's GNSS takes 40-50 s to fix after USB comes on, so a wake's first LoRa
# step (about 38 s after boot) would otherwise go without a position.
CACHE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gps_last_fix.json")
DEFAULT_CACHE_MAX_AGE_H = 168   # runtime parameter gps_cache_max_age_h; 0 turns the cache off
_MOVED_DEG = 1e-4               # about 10 m: rewrite the cache when the fix moves this much
_REFRESH_S = 3600               # or when the cached fix is this old

_gps_available = False
try:
    gpsd.connect()
    _gps_available = True
except Exception:
    pass


def _get_packet():
    if not _gps_available:
        return None
    packet = gpsd.get_current()
    print(f'Current packet mode: {packet.mode}')
    if packet.mode < 2:
        return None
    return packet

def get_location() -> List[str]:
    if not _gps_available:
        return []
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
    if not _gps_available:
        return ({}, None)
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
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE_PATH), prefix=".gps_last_fix.")
        with os.fdopen(fd, "w") as f:
            json.dump(entry, f)
        os.chmod(tmp, 0o600)   # it says where the unit is
        os.replace(tmp, CACHE_PATH)
    except OSError as e:
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
    saved fix is returned instead if it's within gps_cache_max_age_h (default a
    week); its packet is None, so photos taken then get no EXIF position.

    Returns:
        tuple: (dict, packet) where dict contains GPS data (or empty dict if unavailable)
               and packet is the raw GPS packet (or None if unavailable or cached)
    """
    if _gps_available:
        for attempt in range(max_retries):
            try:
                location, packet = _get_lat_lon_alt_with_packet()
            except Exception as e:   # gpsd answers "GPS not active" until it has the modem's port
                location, packet = {}, None
                if attempt == max_retries - 1:
                    print(f"GPS: no live fix ({e})")
            if location:
                _save_fix(location)
                return (location, packet)
            if attempt < max_retries - 1:
                time.sleep(delay)
    if use_cache:
        location, age = cached_fix()
        if location:
            print(f"GPS: no live fix; using the last fix from {age / 3600:.1f} h ago")
            return (location, None)
    return ({}, None)

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
