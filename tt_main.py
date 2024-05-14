# TT Python version

from ticktalkpython.SQ import SQify, STREAMify, GRAPHify
from ticktalkpython.Clock import TTClock
from ticktalkpython.Instructions import COPY_TTTIME, READ_TTCLOCK, VALUES_TO_TTTIME
from ticktalkpython.Deadline import TTFinishByOtherwise

@STREAMify
def imu_values(trigger):
    import time
    from time import sleep
    import board
    import adafruit_bno055
    import logging

    logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s', encoding='utf-8', level=logging.DEBUG)
    DATA = '/home/pi/SU-WaterCam/data/data_log.txt'
    i2c = board.I2C()
    sensor = adafruit_bno055.BNO055_I2C(i2c)
    
    sleep(1)
    values = {"Temperature":sensor.temperature, "Accelerometer":sensor.acceleration,
        "Magnetic":sensor.magnetic, "Gyro":sensor.gyro, "Euler":sensor.euler,
        "Quaternion":sensor.quaternion, "Linear":sensor.linear_acceleration,
        "Gravity":sensor.gravity}
   
    # log IMU data to text file
    imu_data = [
                f"Time: {time.asctime(time.localtime(time.time()))}\n",
                f"Accelerometer: {values['Accelerometer']}\n",
                f"Gyro: {values['Gyro']}\n",
                f"Temperature: {values['Temperature']}\n"]

    with open(DATA, 'a', encoding="utf8") as data:
        for line in imu_data:
            data.writelines(line)
    
    yaw, roll, pitch = values['Euler']
    
    results = [roll, pitch, yaw]
    return results

@SQify
def imu_planb():
    return [0,0,0]

@STREAMify
def take_photo(trigger):
    from os import path
    from datetime import datetime
    from picamera2 import Picamera2
    import logging
    from ticktalkpython.Empty import TTEmpty

    filepath = '/home/pi/SU-WaterCam/images/'
    
    # initialize once
    global sq_state
    if sq_state.get('picam', None) is None:
        try:
            picam2 = Picamera2()
            config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
            picam2.configure(config)
            sq_state['picam'] = picam2
        except Exception:
            logging.error("PiCamera loading error")
            return TTEmpty()
        

    time = datetime.now().strftime('%Y%m%d-%H%M%S')
    image = path.join(filepath, f'{time}.jpg')
    print(f'taking photo: {image}')
    try:
        picam2 = sq_state.get('picam')
        picam2.start_and_capture_file(image, show_preview=False)
    except Exception as e:
        logging.error("Camera failed to capture image")
        return TTEmpty()
    else:
        print(f"Took photo: {image}")
        return [image]

@SQify
def image_planb():
    print("image plan B")
    return True 

@STREAMify
def lepton_record(trigger):
    from shutil import copy
    from os import path, mkdir, remove
    import subprocess # to call external apps
    from datetime import datetime
    import pytz
    import logging 
    filepath = '/home/pi/SU-WaterCam/'
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
    try:
        subprocess.run([capture], check=True, cwd=folder) #, timeout=10)
    except subprocess.CalledProcessError as err:
        logging.error(f"Capture error: {err.returncode} \n {err}")
    #except subprocess.TimeoutExpired as err:
    #    logging.error(f"Capture process timeout: {err} \n")

    print('\n saving temperature data...')
    try:
        subprocess.run([lepton], check=True, cwd=folder) #, timeout=10)
    except subprocess.CalledProcessError as err:
        logging.error(f"Capture error: {err.returncode} \n {err}")
    #except subprocess.TimeoutExpired as err:
    #    logging.error(f"Capture process timeout: {err} \n")

    # delete duplicated binaries
    remove(lepton)
    remove(capture)
    return True

@STREAMify
def lepton_track(trigger):
    return 0

@SQify
def lepton_planb():
    # Flir Lepton breakout board 2.0
    # Pin 17 on the breakout is RESET_L
    # We connect this to pin 31 (GPIO 6) on the Raspberry Pi
    print("Lepton plan b")
    from time import sleep
    import RPi.GPIO as GPIO

    pin = 31

    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(pin, GPIO.OUT)

    # set low for 1 second to trigger reset on breakout board
    GPIO.output(pin, GPIO.LOW)
    sleep(5.0)

    # reset to default state
    GPIO.output(pin, GPIO.HIGH)
    GPIO.setup(pin, GPIO.IN)

    print(f"Pin {pin}: {GPIO.input(pin)}")

    return {}

@STREAMify
def gps_data(trigger):
    import gpsd2
    import time
    import piexif
    from fractions import Fraction
    import logging

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

    DATA = '/home/pi/SU-WaterCam/data/data_log.txt'
    gps_data = []
    try:
        gpsd2.connect()
        # get current gps info from gpsd
    except Exception as err:
        logging.error("GPS error! \n")
    else:
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
            gps_exif = [{"GPS": gps_ifd}]
            print(gps_exif)
            return gps_exif
  
@SQify
def gps_planb():
    return [{"GPS": {}}]    

@SQify
def exif(image, gps_exif, imu_data):
    import piexif
    import piexif.helper
    from libxmp import XMPFiles, consts

    print(f"value of 'image' passed to exif function: {image}")
    print(f"filepath within image list: {image[0]}")
    fname = image[0]
    # load original exif data
    try:
        exif_data = piexif.load(fname)
    except Exception:
        print("no image file or no image exif data")
    else:
        # Add roll/pitch/yaw to UserComment tag if they exist
        print(imu_data)
        roll = imu_data[0]
        pitch = imu_data[1]
        yaw = imu_data[2]
        print(f"Roll: {roll} Pitch: {pitch} Yaw: {yaw} \n")
        if roll:
            user_comment = piexif.helper.UserComment.dump(f"Roll {roll} Pitch {pitch} Yaw {yaw}")
            exif_data["Exif"][piexif.ExifIFD.UserComment] = user_comment

        # add gps tag to original exif data
        if gps_exif:
            gpsdict = gps_exif[0]
            exif_data.update(gpsdict)

        # Finish exif handling
        # convert to byte format for writing into file
        exif_bytes = piexif.dump(exif_data)
        # write to disk
        piexif.insert(exif_bytes, fname)

        # Write roll/pitch/yaw to XMP tags for Pix4D
        if roll:
            xmpfile = XMPFiles(file_path=fname, open_forupdate=True)
            xmp = xmpfile.get_xmp()
            xmp.set_property(consts.XMP_NS_DC, 'Roll', str(roll))
            xmp.set_property(consts.XMP_NS_DC, 'Pitch', str(pitch))
            xmp.set_property(consts.XMP_NS_DC, 'Yaw', str(yaw))
            xmpfile.put_xmp(xmp)
            xmpfile.close_file()

    finally:
        return True

@SQify
def tf_classify(file):
    from tflite_runtime.interpreter import Interpreter
    from PIL import Image
    import numpy as np
    import time

    def load_labels(path):
        with open(path, 'r') as f:
            return [line.strip() for i, line in enumerate(f.readlines())]

    def set_input_tensor(interpreter, image):
        tensor_index = interpreter.get_input_details()[0]['index']
        input_tensor = interpreter.tensor(tensor_index)()[0]
        # Convert the PIL Image to a NumPy array and then flatten it
        input_array = np.array(image).flatten()

        # Copy the values to the input tensor
        input_tensor.flat = input_array

    def classify_image(interpreter, image, top_k=1):
        set_input_tensor(interpreter, image)
        interpreter.invoke()
        output_details = interpreter.get_output_details()[0]
        output = np.squeeze(interpreter.get_tensor(output_details['index']))
        scale, zero_point = output_details['quantization']
        output = scale * (output - zero_point)

        ordered = np.argpartition(-output, 1)
        return [(i, output[i]) for i in ordered[:top_k]][0]

    data_folder = "images"
    model_path = "skhan.tflite"
    label_path = "labels.txt"
    interpreter = Interpreter(model_path)
    print("Model Loaded Successfully.")
    interpreter.allocate_tensors()
    _, height, width, _ = interpreter.get_input_details()[0]['shape']
    print("Image Shape (", width, ",", height, ")")

    # Load an image to be classified.
    image = Image.open(file).convert('RGB').resize((width, height))
 
    # Classify the image.
    time1 = time.time()
    label_id, prob = classify_image(interpreter, image)
    time2 = time.time()
    classification_time = np.round(time2-time1, 3)
    print("Classification Time =", classification_time, "seconds.")

    # Read class labels.
    labels = load_labels(label_path)

    # Return the classification label of the image.
    classification_label = labels[label_id]
    print("Image Label is :", classification_label, ", with Accuracy :", np.round(prob*100, 2), "%.")

    return True

@SQify
def tf_classify_planb():
  print('Tensorflow issue')
  return 1 

@GRAPHify
def main(trigger):
    with TTClock.root() as root_clock:
        start_time = READ_TTCLOCK(trigger, TTClock=root_clock)
        N = 30
        stop_time = start_time + (1000000 * N)
        sampling_time = VALUES_TO_TTTIME(start_time, stop_time)
        sample_window = COPY_TTTIME(1, sampling_time)

        timeout_val = 10_000_000
        timestamp = READ_TTCLOCK(lepton_track(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), TTClock=root_clock)


        # take a new photo
        #image = take_photo(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000)

        image = TTFinishByOtherwise(take_photo(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), TTTimeDeadline=timestamp + timeout_val, TTPlanB=image_planb(), TTWillContinue=False)
        
        # Call lepton and capture sequentially to get temperature and IR image
        # from Flir Lepton
        #runlepton = lepton_record(trigger)

        runlepton = TTFinishByOtherwise(lepton_record(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), TTTimeDeadline=timestamp + timeout_val, TTPlanB=TTSingleRunTimeout(lepton_planb(), TTTimeout=10_000_000), TTWillContinue=True)

        # get IMU data
        imu_data = TTFinishByOtherwise(imu_values(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), 
                                       TTTimeDeadline=start_time + timeout_val,
                                       TTPlanB=imu_planb(),
                                       TTWillContinue=True)
        
        gps_exif = TTFinishByOtherwise(gps_data(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000),
                                       TTTimeDeadline=start_time + timeout_val,
                                       TTPlanB=gps_planb(),
                                       TTWillContinue=True)
        
        # Save exif/xmp data to image file
        runexif = exif(image, gps_exif, imu_data)

        # classification test on single file
        run_tf_classify = tf_classify(image)

        #reset = TTSingleRunTimeout(lepton_planb(), TTTimeout=1_000_000)
