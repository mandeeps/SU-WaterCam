#!/home/pi/SU-WaterCam/venv/bin/python

import gpsd2 as gpsd # using py-gpsd2
from typing import List, Optional, Tuple
import time

gpsd.connect()

def get_packet():
    packet = gpsd.get_current()
    print(f'Current packet mode: {packet.mode}')
    
    if packet.mode < 2:
        return None
    else:
        return packet

def get_location() -> List[str]:
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

def get_lat_lon_alt() -> Tuple[float, float, float]:
    packet = get_packet()
    
    lat = packet.lat 
    lon = packet.lon 
    alt = packet.alt

    return (lat, lon, alt)

def get_location_with_retry(max_retries: int = 3, delay: float = 1.0) -> Optional[Tuple[float, float, float]]:
    """Get location with retry logic for better reliability."""
    for attempt in range(max_retries):
        location = get_packet()
        if location:
            return location
        
        if attempt < max_retries - 1:
            time.sleep(delay)
    
    return None

if __name__ == "__main__":
    data = get_location()
    for line in data:
        print(line)

    gps = get_lat_lon_alt()
    print(f'Lat Lon Alt: {gps}')
