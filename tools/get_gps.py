#!/home/pi/SU-WaterCam/venv/bin/python

from fractions import Fraction
import gpsd2

try:
    gpsd2.connect()
except Exception as error:
    print("Error: GPS connection")

# two helper functions from https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72
def to_deg(value, loc):
    """convert decimal coordinates into degrees, minutes and seconds tuple
    Keyword arguments: value is float gps-value, loc is direction list ["S", "N"] or ["W", "E"]
    return: tuple like (25, 13, 48.343 ,'N')
    """
    if value < 0:
        loc_value = loc[0]
    elif value > 0:
        loc_value = loc[1]
    else:
        loc_value = ""
    abs_value = abs(value)
    deg =  int(abs_value)
    t1 = (abs_value-deg)*60
    minutes = int(t1)
    sec = round((t1 - minutes)* 60, 5)
    return (deg, minutes, sec, loc_value)

def change_to_rational(number):
    """convert a number to rational
    Keyword arguments: number
    return: tuple like (1, 2), (numerator, denominator)
    """
    f = Fraction(str(number))
    return (f.numerator, f.denominator)

def get_loc():
    # get current gps info from gpsd
    gps_data = []
    try:
        packet = gpsd2.get_current()
    except Exception as error:
        print("No GPS data returned")
    else:
        if packet.mode >= 2:
            gps_data = [
                f"GPS Time UTC: {packet.time}\n",
                f"GPS Time Local: {time.asctime(time.localtime(time.time()))}\n",
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

        # Conversion for exif use
        lat_deg = to_deg(packet.lat,['S','N'])
        lng_deg = to_deg(packet.lon,['W','E'])

        exiv_lat = (change_to_rational(lat_deg[0]),
                    change_to_rational(lat_deg[1]),
                    change_to_rational(lat_deg[2]))

        exiv_lng = (change_to_rational(lng_deg[0]),
                    change_to_rational(lng_deg[1]),
                    change_to_rational(lng_deg[2]))

        gps_ifd = {
                "Altitude": change_to_rational(round(packet.alt)),
                "LatitudeRef": lat_deg[3],
                "Latitude": exiv_lat,
                "LongitudeRef": lng_deg[3],
                "Longitude": exiv_lng,
        }
        print(exiv_lat)
        print(exiv_lng)

    return gps_data

if __name__ == "__main__":
    print(get_loc())
