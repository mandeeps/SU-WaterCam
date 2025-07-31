#!/home/pi/SU-WaterCam/venv/bin/python

from fractions import Fraction
import gpsd2
import time

try:
    gpsd2.connect()
except Exception as error:
    print("Error: GPS connection")

def get_loc(exif=False):
    # get current gps info from gpsd
    gps_data = []
    try:
        packet = gpsd2.get_current()
        print(f"\n packet mode: {packet.mode} \n")
        if packet.mode < 2:
            time.sleep(3)
            packet = gpsd2.get_current()
            print(f"\n packet mode: {packet.mode} \n")
    except Exception as error:
        print("No GPS data returned")
    else:
           
        if packet.mode >= 2:
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
                f"Device: {gpsd2.device()}\n"]

        if packet.mode >= 3:
            gps_data.append(f"Altitude: {packet.alt}\n")

    if exif:
        return (gps_data, packet)
    else:
        return gps_data

def get_lat_lon():
    current = get_loc()

    if not current:
        time.sleep(1)
        current = get_loc()
    
    data = dict((k, current[k]) for k in ["Latitude", "Longitude", "Altitude"] if k in current)

# if we have no GPS data return False. If we have Lat/Long but not Alt return dict 
# with Alt as 0.
    if not "Longitude" in data:
        return False
    if not "Latitude" in data:
        return False
    if not "Altitude" in data:
        data["Altitude"] = 0

    return {'lat_lon_z' : (data["Latitude"], data["Longitude"], data["Altitude"])}

if __name__ == "__main__":
    data, packet = get_loc(exif=True)

    if not data:
        time.sleep(1)
        data, packet = get_loc(True)

    for line in data:
        print(line)

    print(packet)
