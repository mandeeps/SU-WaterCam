#!/home/pi/SU-WaterCam/venv/bin/python
# embed GPS/IMU data into image EXIF
# Takes image path as parameter

import json
import os
import time
from fractions import Fraction
import piexif
import piexif.helper
from libxmp import XMPFiles, consts

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "runtime_config.json")


def _read_device_id(config_path: str = _CONFIG_PATH) -> str:
    """Return the device_id from runtime_config.json, or '' if unavailable."""
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        return cfg.get("ip_upload", {}).get("device_id", "")
    except Exception:
        return ""

try:
    from tools import bno055_imu
except ImportError:
    print("BNO055 import issue")
except Exception:
    print("BNO055 hardware issue")

try:
    from tools.get_gps import get_location_with_retry, get_loc
except ImportError:
    print("GPS import issue")
except Exception:
    print("GPS issue")

# two helper functions from https://gist.github.com/c060604/8a51f8999be12fc2be498e9ca56adc72
# These are for converting values from the GPS for exif / xmp 
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


def add_metadata(image):
    # add metadata to an image
    DATA = "/home/pi/SU-WaterCam/data/metadata_log.txt"

    # Initialize IMU variables
    roll = None
    pitch = None
    yaw = None

    # get IMU data
    try:
        imu_values = bno055_imu.get_values()
    except Exception as error:
        print("IMU Error")
    else:
        # log IMU data to text file
        imu = [f"\nFile: {image}\n",
            f"Time: {time.asctime(time.localtime(time.time()))}\n",
            f"Accelerometer: {imu_values['Accelerometer']}\n",
            f"Gyro: {imu_values['Gyro']}\n",
            f"Temperature: {imu_values['Temperature']}\n"]

        with open(DATA, 'a', encoding="utf8") as data:
            for line in imu:
                data.writelines(line)

        yaw, roll, pitch = imu_values['Euler']

    # Start exif handling
    # load original exif data
    exif_data = piexif.load(image)

    # Embed unit ID in EXIF BodySerialNumber (tag 0xA431)
    device_id = _read_device_id()
    if device_id:
        exif_data["Exif"][piexif.ExifIFD.BodySerialNumber] = device_id.encode()

    # Add roll/pitch/yaw to UserComment tag if they exist
    if roll is not None:
        user_comment = piexif.helper.UserComment.dump(f"Roll {roll} Pitch {pitch} Yaw {yaw}")
        exif_data["Exif"][piexif.ExifIFD.UserComment] = user_comment

    # Write XMP tags for Pix4D: unit ID always, orientation when available
    xmp_props: dict = {}
    if device_id:
        xmp_props["DeviceID"] = device_id
    if roll is not None:
        xmp_props["Roll"] = str(roll)
        xmp_props["Pitch"] = str(pitch)
        xmp_props["Yaw"] = str(yaw)

    if xmp_props:
        xmpfile = XMPFiles(file_path=image, open_forupdate=True)
        xmp = xmpfile.get_xmp()
        for prop, val in xmp_props.items():
            xmp.set_property(consts.XMP_NS_DC, prop, val)
        xmpfile.put_xmp(xmp)
        xmpfile.close_file()

    # GPS: get current info from gpsd
    formatted_gps_data = []
    packet = None
    
    try:
        # Get GPS packet for EXIF data (we discard the location dict as we use get_loc() for logging)
        _, packet = get_location_with_retry()
    except Exception as error:
        print(f"No GPS data returned from get_location_with_retry: {error}")
        with open(DATA, 'a', encoding="utf8") as data:
            data.write("\nGPS Error \n")
    
    try:
        # Get formatted GPS data for logging
        formatted_gps_data = get_loc()
    except Exception as error:
        print(f"No GPS data returned from get_loc: {error}")
    
    # save formatted GPS data to text file
    if formatted_gps_data:
        with open(DATA, 'a', encoding="utf8") as data:
            for line in formatted_gps_data:
                data.writelines(line)

    # Only add EXIF GPS data if we have a valid packet
    if packet:
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
                piexif.GPSIFD.GPSVersionID: (2,0,0,0),
                piexif.GPSIFD.GPSAltitudeRef: 0,
                piexif.GPSIFD.GPSAltitude: change_to_rational(round(packet.alt)),
                piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
                piexif.GPSIFD.GPSLatitude: exiv_lat,
                piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
                piexif.GPSIFD.GPSLongitude: exiv_lng,
                piexif.GPSIFD.GPSTrack: change_to_rational(packet.track)
        }

        # Since we have GPS data, add to Exif
        gps_exif = {"GPS": gps_ifd}
        print(gps_exif)
        # add gps tag to original exif data
        exif_data.update(gps_exif)

    # Finish exif handling
    # convert to byte format for writing into file
    exif_bytes = piexif.dump(exif_data)
    # write to disk
    piexif.insert(exif_bytes, image)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        add_metadata(filepath)
        print(f"Photo: {filepath} has been updated with metadata")
    else:
        print("Need path to image you want to add metadata to")
