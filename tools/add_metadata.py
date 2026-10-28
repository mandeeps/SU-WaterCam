#!/home/pi/SU-WaterCam/venv/bin/python
# embed GPS/IMU data into image EXIF
# Takes image path as parameter

import datetime
import logging
import time
from fractions import Fraction
import piexif
import piexif.helper
from libxmp import XMPFiles, XMPMeta, consts

logger = logging.getLogger(__name__)


def _read_device_id() -> str:
    """Return the device_id from runtime_config.json as a str, or '' if unavailable."""
    from tools.transmit_ip import _load_ip_config
    ip_cfg = _load_ip_config()
    raw = ip_cfg.get("device_id", "")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float, bool)):
        return str(raw)
    if raw is not None:
        logger.warning("device_id in config has unexpected type %s; ignoring", type(raw).__name__)
    return ""

try:
    from tools import bno055_imu
except ImportError:
    print("BNO055 import issue")
except Exception:
    print("BNO055 hardware issue")

try:
    from tools.debug_log import debug
except ImportError:     # run from tools/ as a script
    from debug_log import debug

last_fix: dict = {}   # replaced by tools.get_gps.last_fix when that imports
try:
    from tools.get_gps import get_location_with_retry, get_loc, last_fix
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


def _vector(v, n):
    """v as a tuple of n floats, or None if it is not a complete finite reading."""
    try:
        out = tuple(float(x) for x in v)
    except (TypeError, ValueError):
        return None
    if len(out) != n or any(x != x or abs(x) == float("inf") for x in out):
        return None
    return out


_METADATA_LOG = "/home/pi/SU-WaterCam/data/metadata_log.txt"


def add_metadata(image):
    # add metadata to an image
    DATA = _METADATA_LOG

    # Initialize IMU variables
    roll = None
    pitch = None
    yaw = None
    heading_std = None
    calib_sys = calib_gyro = calib_accel = calib_mag = None

    # get IMU data
    try:
        imu_values = bno055_imu.get_values()
    except Exception as error:
        print("IMU Error")
        imu_values = {}

    if imu_values:
        # Read calibration status so we can log and gate on heading quality
        try:
            cal = bno055_imu._get_sensor().calibration_status
            calib_sys, calib_gyro, calib_accel, calib_mag = cal
        except Exception:
            calib_sys = calib_gyro = calib_accel = calib_mag = None

        calib_str = (f"sys={calib_sys} gyro={calib_gyro} "
                     f"accel={calib_accel} mag={calib_mag}"
                     if calib_sys is not None else "unknown")

        # log IMU data to text file
        imu = [f"\nFile: {image}\n",
            f"Time: {time.asctime(time.localtime(time.time()))}\n",
            f"Accelerometer: {imu_values.get('Accelerometer')}\n",
            f"Gyro: {imu_values.get('Gyro')}\n",
            f"Temperature: {imu_values.get('Temperature')}\n",
            f"CalibStatus: {calib_str}\n"]

        with open(DATA, 'a', encoding="utf8") as data:
            for line in imu:
                data.writelines(line)

        # Average 20 samples (~300 ms) for stable heading, pitch, and roll
        try:
            euler_stable = bno055_imu.get_euler_stable()
        except Exception:
            euler_stable = {}

        if euler_stable:
            yaw         = euler_stable.get("heading_mean")
            roll        = euler_stable.get("roll_mean")
            pitch       = euler_stable.get("pitch_mean")
            heading_std = euler_stable.get("heading_std")
            if calib_mag is not None and calib_mag < 2:
                print(f"WARNING: BNO055 magnetometer uncalibrated (mag={calib_mag}/3) "
                      f"— heading in EXIF will be unreliable")

    # Quaternion and gravity vector: Euler angles cannot be compared across captures
    # near +/-90 degrees pitch (gimbal lock), and a fixed camera is mounted close to
    # it. Read just after the Euler average above, and stamped with the read time.
    pose = {}
    pose_time = None
    if imu_values:
        try:
            pose = bno055_imu.get_pose() or {}
            pose_time = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
        except Exception as error:
            print(f"IMU pose error: {error}")
            pose = {}
    quaternion = _vector(pose.get("quaternion"), 4)
    gravity = _vector(pose.get("gravity"), 3)

    # GPS: get current info from gpsd, or the last saved fix (packet None) when
    # there is no live one
    formatted_gps_data = []
    packet = None
    location = {}

    try:
        # The packet feeds the EXIF position of a live fix; the dict is all a cached fix has
        location, packet = get_location_with_retry()
    except Exception as error:
        print(f"No GPS data returned from get_location_with_retry: {error}")
        with open(DATA, 'a', encoding="utf8") as data:
            data.write("\nGPS Error \n")

    try:
        # Get formatted GPS data for logging
        formatted_gps_data = get_loc()
    except Exception as error:
        # "GPS not active" is the normal case with USB off between daily fixes (#239)
        (debug if "not active" in str(error) else print)(f"No GPS data returned from get_loc: {error}")

    # save formatted GPS data to text file
    if formatted_gps_data:
        with open(DATA, 'a', encoding="utf8") as data:
            for line in formatted_gps_data:
                data.writelines(line)

    cached_fix = (not packet and isinstance(location, dict)
                  and location.get("gps_lat") is not None and location.get("gps_lon") is not None)
    fix_age_s = None
    if cached_fix:
        try:
            fix_age_s = int(round(float(last_fix.get("age_s"))))
        except (TypeError, ValueError):
            fix_age_s = None

    # Start exif handling
    # load original exif data
    exif_data = piexif.load(image)

    # Embed unit ID in EXIF BodySerialNumber (tag 0xA431)
    device_id = _read_device_id()
    if device_id:
        exif_data["Exif"][piexif.ExifIFD.BodySerialNumber] = device_id.encode("ascii", errors="replace")

    # Add roll/pitch/yaw to UserComment tag only when all three are available
    comment = ""
    if roll is not None and pitch is not None and yaw is not None:
        comment = f"Roll {roll} Pitch {pitch} Yaw {yaw}"
        if heading_std is not None:
            comment += f" HeadingStd {heading_std}"
        if calib_mag is not None:
            comment += f" CalibMag {calib_mag}"
        if quaternion is not None:
            comment += " Quaternion " + ",".join(f"{v:.5f}" for v in quaternion)
        if gravity is not None:
            comment += " Gravity " + ",".join(f"{v:.4f}" for v in gravity)
        if pose_time and (quaternion is not None or gravity is not None):
            comment += f" PoseTime {pose_time}"
    if cached_fix:
        # No live fix: the position written is the last saved one, not where the
        # node is known to be now
        comment = (comment + " " if comment else "") + "GPSFix CACHED"
        if fix_age_s is not None:
            comment += f" AgeS {fix_age_s}"
    if comment:
        exif_data["Exif"][piexif.ExifIFD.UserComment] = piexif.helper.UserComment.dump(comment)

    # Write XMP tags: unit ID always, orientation + quality when available
    xmp_props: dict = {}
    if device_id:
        xmp_props["DeviceID"] = device_id
    if roll is not None and pitch is not None and yaw is not None:
        xmp_props["Roll"] = str(roll)
        xmp_props["Pitch"] = str(pitch)
        # Raw magnetic heading in sensor frame. Mount offset, magnetic declination,
        # and pole correction are applied by the Georeferencing pipeline at
        # processing time using values from the unit config JSON.
        xmp_props["Yaw"] = str(yaw)
        xmp_props["HeadingRawSensorFrame"] = "true"
    if heading_std is not None:
        xmp_props["HeadingStdDev"] = str(heading_std)
    if calib_sys is not None:
        xmp_props["CalibSys"]   = str(calib_sys)
        xmp_props["CalibGyro"]  = str(calib_gyro)
        xmp_props["CalibAccel"] = str(calib_accel)
        xmp_props["CalibMag"]   = str(calib_mag)   # 0-3; heading is trustworthy at 2 or more
    # Orientation without gimbal lock, in the sensor frame (BNO055 order: w,x,y,z;
    # gravity in m/s^2), and when it was read (ISO 8601 with UTC offset)
    if quaternion is not None:
        for axis, v in zip("WXYZ", quaternion):
            xmp_props[f"Quaternion{axis}"] = f"{v:.5f}"
    if gravity is not None:
        for axis, v in zip("XYZ", gravity):
            xmp_props[f"Gravity{axis}"] = f"{v:.4f}"
    if pose_time and (quaternion is not None or gravity is not None):
        xmp_props["PoseReadTime"] = pose_time
    if packet:
        xmp_props["GPSFixSource"] = "live"
    elif cached_fix:
        xmp_props["GPSFixSource"] = "cached"
        if fix_age_s is not None:
            xmp_props["GPSFixAgeSeconds"] = str(fix_age_s)

    if xmp_props:
        xmpfile = XMPFiles(file_path=image, open_forupdate=True)
        try:
            xmp = xmpfile.get_xmp()
            if xmp is None:
                xmp = XMPMeta()
            for prop, val in xmp_props.items():
                xmp.set_property(consts.XMP_NS_DC, prop, val)
            xmpfile.put_xmp(xmp)
        finally:
            xmpfile.close_file()

    # EXIF GPS: a live fix (packet) or the last saved fix (cached_fix). The EXIF
    # position is never left out for a cached fix, but it is marked: its
    # GPSProcessingMethod is CACHED, and its age is in the UserComment and XMP.
    # GPSTrack is not written: it is the course over the ground, meaningless for a
    # fixed camera, and readers took it for the direction the camera points.
    if packet or cached_fix:
        if packet:
            lat, lon = packet.lat, packet.lon
            alt = round(packet.alt) if packet.mode >= 3 else None
            method = b"GPS"
        else:
            lat, lon = float(location["gps_lat"]), float(location["gps_lon"])
            alt = round(location["gps_alt"]) if location.get("gps_alt") is not None else None
            method = b"CACHED"

        # Conversion for exif use
        lat_deg = to_deg(lat,['S','N'])
        lng_deg = to_deg(lon,['W','E'])

        exiv_lat = (change_to_rational(lat_deg[0]),
                    change_to_rational(lat_deg[1]),
                    change_to_rational(lat_deg[2]))

        exiv_lng = (change_to_rational(lng_deg[0]),
                    change_to_rational(lng_deg[1]),
                    change_to_rational(lng_deg[2]))

        gps_ifd = {
                piexif.GPSIFD.GPSVersionID: (2,0,0,0),
                piexif.GPSIFD.GPSLatitudeRef: lat_deg[3],
                piexif.GPSIFD.GPSLatitude: exiv_lat,
                piexif.GPSIFD.GPSLongitudeRef: lng_deg[3],
                piexif.GPSIFD.GPSLongitude: exiv_lng,
                # Undefined-type tag: 8-byte character code, then the text
                piexif.GPSIFD.GPSProcessingMethod: b"ASCII\x00\x00\x00" + method,
        }

        # Altitude is only valid in a 3D fix (mode 3); skip for 2D fix to avoid
        # AttributeError or writing stale/zero altitude into EXIF. A cached fix
        # has one only if the live fix it was saved from did.
        if alt is not None:
            gps_ifd[piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
            gps_ifd[piexif.GPSIFD.GPSAltitude] = change_to_rational(abs(alt))

        # Since we have GPS data, add to Exif
        gps_exif = {"GPS": gps_ifd}
        debug(gps_exif)
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
