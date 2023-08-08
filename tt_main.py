# TT Python version

from ticktalkpython.SQ import SQify, STREAMify, GRAPHify
from ticktalkpython.Clock import TTClock
from ticktalkpython.Instructions import COPY_TTTIME, READ_TTCLOCK, VALUES_TO_TTTIME

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

@SQify
def imu_values(trigger):
    from time import sleep
    import board
    import adafruit_bno055

    i2c = board.I2C()
    sensor = adafruit_bno055.BNO055_I2C(i2c)
    
    sleep(1)
    values = {"Temperature":sensor.temperature, "Accelerometer":sensor.acceleration,
        "Magnetic":sensor.magnetic, "Gyro":sensor.gyro, "Euler":sensor.euler,
        "Quaternion":sensor.quaternion, "Linear":sensor.linear_acceleration,
        "Gravity":sensor.gravity}
    return values

@SQify
def take_photo(trigger, filepath: str):
    from os import path
    from datetime import datetime
    from picamera2 import Picamera2

    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
    picam2.configure(config)

    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(filepath, f'{time}.jpg')
    print(f'taking photo: {image}')
    picam2.start_and_capture_file(image, show_preview=False)

    return image

@SQify
def lepton_record(trigger, filepath: str):
    from shutil import copy
    from os import path, mkdir, remove
    import subprocess # to call external apps
    from datetime import datetime
    import pytz

    # User configurable values
    TIMEZONE = pytz.timezone('US/Eastern') # Set correct timezone here
    # Local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M%S')
    # create new directory for data from this run
    folder = path.join(filepath, f'data/lepton-{time_val}')
    mkdir(folder)
    # copy lepton binary into newly created directory to save data there
    source = path.join(filepath, 'lepton')
    lepton = path.join(folder, 'lepton')
    print(lepton)
    copy(source, lepton)
    # do the same for the capture binary
    source = path.join(filepath, 'capture')
    capture = path.join(folder, 'capture')
    copy(source, capture)
    print(capture)
    # call capture and lepton binaries to save image and temperature data
    print('saving thermal photo...')
    subprocess.run([capture], check=True, cwd=folder, timeout=10)
    print('\n saving temperature data...')
    subprocess.run([lepton], check=True, cwd=folder, timeout=10)

    # delete duplicated binaries
    remove(lepton)
    remove(capture)


def gps_data(trigger):
 import gpsd2
     gpsd2.connect()
           # get current gps info from gpsd
            gps_data = []
            packet = gpsd2.get_current()
            if packet:    
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

                # save to text file
                with open(DATA, 'a', encoding="utf8") as data:
                    for line in gps_data:
                        data.writelines(line)

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
  
  

@GRAPHify
def main(trigger):
    import time
    from fractions import Fraction
    import piexif
    import piexif.helper
    from libxmp import XMPFiles, consts

    # setup
    DIRNAME = '/home/pi/SU-WaterCam/images/'
    BASEDIR = '/home/pi/SU-WaterCam/'

    # data record
    DATA = '/home/pi/SU-WaterCam/data/data_log.txt'
    last_print = time.monotonic()

    # How often we take a photo
    INTERVAL = 5
    # How many photos to take per run
    LIMIT = 10

    with TTClock.root() as root_clock:
            # take a new photo
            image = take_photo(trigger, DIRNAME)
            # Call lepton and capture sequentially to get temperature and IR image
            # from Flir Lepton
            lepton_record(trigger, BASEDIR)

            # get IMU data
            imu_data = imu_values(trigger)
            print(imu_data)
            # log IMU data to text file
            imu = [f"\nFile: {image}\n",
                    f"Time: {time.asctime(time.localtime(time.time()))}\n",
                    f"Accelerometer: {imu_data['Accelerometer']}\n",
                    f"Gyro: {imu_data['Gyro']}\n",
                    f"Temperature: {imu_data['Temperature']}\n"]

            with open(DATA, 'a', encoding="utf8") as data:
                for line in imu:
                    data.writelines(line)
            yaw, roll, pitch = imu_data['Euler']

            # Start exif handling
            # load original exif data
            exif_data = piexif.load(image)
            # Add roll/pitch/yaw to UserComment tag if they exist
            if roll:
                user_comment = piexif.helper.UserComment.dump(f"Roll {roll} Pitch {pitch} Yaw {yaw}")
                exif_data["Exif"][piexif.ExifIFD.UserComment] = user_comment

               # add gps tag to original exif data
                exif_data.update(gps_exif)

            # Finish exif handling
            # convert to byte format for writing into file
            exif_bytes = piexif.dump(exif_data)
            # write to disk
            piexif.insert(exif_bytes, image)

            # Write roll/pitch/yaw to XMP tags for Pix4D
            if roll:
                xmpfile = XMPFiles(file_path=image, open_forupdate=True)
                xmp = xmpfile.get_xmp()
                xmp.set_property(consts.XMP_NS_DC, 'Roll', str(roll))
                xmp.set_property(consts.XMP_NS_DC, 'Pitch', str(pitch))
                xmp.set_property(consts.XMP_NS_DC, 'Yaw', str(yaw))
                xmpfile.put_xmp(xmp)
                xmpfile.close_file()
        
from logging import root
from ticktalkpython.SQ import SQify, STREAMify, GRAPHify
from ticktalkpython.Clock import TTClock
from ticktalkpython.Instructions import COPY_TTTIME, READ_TTCLOCK, VALUES_TO_TTTIME
import tt_imu

@GRAPHify
def main(trigger):
    A_1 = 1
    with TTClock.root() as root_clock:
        start_time = READ_TTCLOCK(trigger, TTClock=root_clock)
        N = 30
        # Setup the stop-tick of the STREAMify's firing rule
        stop_time = start_time + (1000000 * N) # sample for N seconds

        # create a sampling interval by copying the start and stop tick from
        # TTToken values to the token time interval
        sampling_time = VALUES_TO_TTTIME(start_time, stop_time)

        # copy the sampling interval to the input values to the STREAMify
        # node; these input values will be treated as sticky tokens, and
        # define the duration over which STREAMify'd nodes must run
        sample = COPY_TTTIME(1, sampling_time)

        # do the sampling with streamify'd SQs. Only one of the inputs needs
        # the special sampling time interval (but it wouldn't hurt if all did)
        # because the other const values have infinite timestamps
        euler = tt_imu.get(sample,
                                  1,
                                  1,
                                  TTClock=root_clock,
                                  TTPeriod=500000,
                                  TTPhase=0,
                                  TTDataIntervalWidth=100000)

        #return euler

        #result = tt_imu.get(trigger, TTClock=root_clock, TTPeriod=500000, TTPhase=1)
