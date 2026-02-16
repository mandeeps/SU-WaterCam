#!/home/pi/SU-WaterCam/venv/bin/python

import gpsd2 as gpsd # using py-gpsd2
from typing import List, Optional, Tuple, Any
import time

gpsd.connect()

def get_packet():
    packet = gpsd.get_current()
    print(f'Current packet mode: {packet.mode}')
    
    if packet.mode < 2:
        return None
    else:
        return packet

def get_loc() -> List[str]:
    packet = get_packet()
    if not packet:
        return []

    elif packet.mode >= 2:
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
            f"Device: {gpsd.device()}\n"]

        if packet.mode >= 3:
            gps_data.append(f"Altitude: {packet.alt}\n")

        return gps_data

def _get_lat_lon_alt_with_packet() -> Tuple[dict, Optional[Any]]:
    """Internal function that returns GPS data and packet."""
    packet = get_packet()
    
    if not packet:
        return ({}, None)
    
    try:
        lat = packet.lat 
        lon = packet.lon 
        alt = packet.alt

        return ({
            'gps_lat': lat,
            'gps_lon': lon,
            'gps_alt': alt
        }, packet)
    except AttributeError:
        # GPS data not available
        return ({}, None)

def get_lat_lon_alt() -> dict:
    """Get GPS latitude, longitude, and altitude as a dictionary."""
    gps_data, _ = _get_lat_lon_alt_with_packet()
    return gps_data

def get_location_with_retry(max_retries: int = 3, delay: float = 1.0) -> Tuple[dict, Optional[Any]]:
    """Get location with retry logic for better reliability.
    
    Returns:
        tuple: (dict, packet) where dict contains GPS data (or empty dict if unavailable) 
               and packet is the raw GPS packet (or None if unavailable)
    """
    for attempt in range(max_retries):
        location, packet = _get_lat_lon_alt_with_packet()
        if location:
            return (location, packet)
        
        if attempt < max_retries - 1:
            time.sleep(delay)
    
    return ({}, None)

if __name__ == "__main__":
    data = get_loc()
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
