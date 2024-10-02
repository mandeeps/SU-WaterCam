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
def take_two_photos():
# Take two photos with Dorhea IR-Cut camera
# one with NIR filter in place and one without
# Set GPIO HIGH to include NIR in the red band and LOW for normal photo
    import pytz
    import logging
    from os import path, makedirs, chdir
    from datetime import datetime
    from picamera2 import Picamera2
    from gpiozero import LED

    logging.basicConfig(filename='debug.log', format='%(asctime)s %(name)-12s %(message)s',
        encoding='utf-8', level=logging.DEBUG)

    try:
        picam2 = Picamera2()
        config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592,1944)})
        picam2.configure(config)
        # picam2.start() -- do not start outside start_and_capture function as this interferes with Flir Lepton! (for some reason I don't understand)
    except Exception:
        logging.error("Camera loading error")

    def take_photo(directory: str, nir: str) -> str:   
        #time = datetime.now().strftime('%Y%m%d-%H%M%S')
        image = path.join(directory, f'{date}-NIR-{nir}.jpg')
        print(f'taking photo: {image}')

        try:
            picam2.start_and_capture_file(image, show_preview=False)
        except Exception:
            logging.error("Camera failed to capture")
        return image

    filepath = f"./images/"
    TIMEZONE = pytz.timezone('US/Eastern') 
    date = datetime.now().strftime('%Y%m%d-%H%M%S')
    directory = path.join(filepath, date)
    # Adjust GPIO as appropriate. We are using GPIO 21, pin 40
    pin = LED(21)
    pin.off()
    print(f"Pin state is: {pin.value}")
    without_nir = take_photo(directory, "OFF")

    pin.on()
    with_nir = take_photo(directory, "ON")

    return [without_nir, with_nir]



@STREAMify
def lepton_record(trigger):
    from shutil import copy
    from os import path, mkdir, remove
    import subprocess # to call external apps
    from datetime import datetime
    import pytz
    import logging 
    filepath = '/home/pi/SU-WaterCam/images/'
    # User configurable values
    TIMEZONE = pytz.timezone('US/Eastern') # Set correct timezone here
    # Local timezone
    time_val = datetime.now().strftime('%Y%m%d-%H%M%S')
    # create new directory for data from this run
    folder = path.join(filepath, f'lepton-{time_val}')
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

        else: print("GPS ERROR")
  
@SQify
def gps_planb():
    return [{"GPS": {}}]    

@SQify
def exif(images, gps_exif, imu_data):
    import piexif
    import piexif.helper
    from libxmp import XMPFiles, consts

    print(f"value of 'image' passed to exif function: {images}")
    print(f"filepath within image list: {images[0][0]}")
    no_nir = images[0][0]
    with_nir= images[0][1]

    def add_exif(fname):
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
    
    if add_exif(fname1) and add_exif(fname2):
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

@SQify
def segformer(filepath):
    import subprocess
    segformer_python = "/home/pi/miniforge3/envs/5band/bin/python"
    segformer_test = "/home/pi/git/segformer_5band/tools/test_no_label.py"

    segformer_coreg = "/home/pi/git/segformer_5band/tools/segment_coreg.py"

    subprocess.Popen([segformer_python, segformer_coreg], cwd="/home/pi/git/segformer_5band")
    return True

@SQify
def multi_coreg(images):
    import cv2
    import os
    import numpy as np
    import SimpleITK as sitk
    import rasterio
    from rasterio.transform import from_origin

    def mutual_information_registration(fixed_image_path, moving_image_path):
        # Read the fixed (optical) and moving (thermal) images
        fixed_image_cv = cv2.imread(fixed_image_path, cv2.IMREAD_UNCHANGED)
        moving_image_cv = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)

        # Resize the thermal image to the same size as the optical image
        if fixed_image_cv.shape[0] > 1000 or fixed_image_cv.shape[1] > 1000:
            scale_percent = 50  # Reduce size by 50%
            width = int(fixed_image_cv.shape[1] * scale_percent / 100)
            height = int(fixed_image_cv.shape[0] * scale_percent / 100)
            dim = (width, height)
            fixed_image_cv_resized = cv2.resize(fixed_image_cv, dim)
            moving_image_cv_resized = cv2.resize(moving_image_cv, dim)
        else:
            moving_image_cv_resized = cv2.resize(moving_image_cv, (fixed_image_cv.shape[1], fixed_image_cv.shape[0]))
            fixed_image_cv_resized = fixed_image_cv

        # Convert the resized images to SimpleITK format
        fixed_image_resized = sitk.GetImageFromArray(cv2.cvtColor(fixed_image_cv_resized, cv2.COLOR_BGR2GRAY).astype(np.float32))
        moving_image_resized = sitk.GetImageFromArray(moving_image_cv_resized.astype(np.float32))

        # Ensure the types are the same
        if fixed_image_resized.GetPixelID() != moving_image_resized.GetPixelID():
            moving_image_resized = sitk.Cast(moving_image_resized, fixed_image_resized.GetPixelID())

        # Initialize the transform
        initial_transform = sitk.CenteredTransformInitializer(
            fixed_image_resized,
            moving_image_resized,
            sitk.Euler2DTransform(),
            sitk.CenteredTransformInitializerFilter.GEOMETRY
        )

        # Set up the image registration method
        registration_method = sitk.ImageRegistrationMethod()

        # Use Mattes Mutual Information as the metric
        registration_method.SetMetricAsMattesMutualInformation(numberOfHistogramBins=50)

        # Set the interpolator to linear
        registration_method.SetInterpolator(sitk.sitkLinear)

        # Use gradient descent optimizer
        registration_method.SetOptimizerAsGradientDescent(
            learningRate=1.0,
            numberOfIterations=100
        )

        # Set the optimizer scales from physical shift
        registration_method.SetOptimizerScalesFromPhysicalShift()

        # Set the initial transform
        registration_method.SetInitialTransform(initial_transform, inPlace=False)

        # Execute the registration
        final_transform = registration_method.Execute(
            sitk.Cast(fixed_image_resized, sitk.sitkFloat32),
            sitk.Cast(moving_image_resized, sitk.sitkFloat32)
        )

        # Print the final metric value and the optimizer's stopping condition
        print("Final metric value: {0}".format(registration_method.GetMetricValue()))
        print("Optimizer's stopping condition, {0}".format(registration_method.GetOptimizerStopConditionDescription()))

        # Resample the thermal image to align with the fixed image using the final transform
        moving_resampled = sitk.Resample(
            moving_image_resized,
            fixed_image_resized,
            final_transform,
            sitk.sitkLinear,
            0.0,
            moving_image_resized.GetPixelID()
        )

        # Apply colormap to the thermal image to show thermal variations
        moving_resampled_np = sitk.GetArrayFromImage(moving_resampled)
        moving_resampled_np = cv2.normalize(moving_resampled_np, None, 0, 255, cv2.NORM_MINMAX)
        moving_resampled_np = 255 - moving_resampled_np  # Invert the thermal image
        moving_resampled_colored = cv2.applyColorMap(moving_resampled_np.astype(np.uint8), cv2.COLORMAP_JET)

        # Combine the thermal and optical images into a single 5-band image
        overlay = fixed_image_cv_resized.astype(np.float32)
        output = moving_resampled_colored.astype(np.float32)
        cv2.addWeighted(overlay, 0.5, output, 0.5, 0, output)

        four_band_with_thermal = np.dstack((overlay, moving_resampled_np))

        # Ensure the combined image is within the correct range
        output = np.clip(output, 0, 255).astype(np.uint8)

        #crop
        x_min, y_min, x_max, y_max = find_bounding_box(moving_resampled_colored)
        cropped_output = output[y_min:y_max, x_min:x_max]

        height, width, _ = output.shape

        return final_transform, output, cropped_output, four_band_with_thermal  #four_band_with_thermal_cropped

# crop
    def find_bounding_box(image):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        retval, thresh = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY)
        contours, hierarchy = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if len(contours) == 0:
            # No contours found, return the full image
            return 0, 0, image.shape[1], image.shape[0]
        x_min, y_min, x_max, y_max = np.inf, np.inf, -np.inf, -np.inf

        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            x_min = min(x_min, x)
            y_min = min(y_min, y)
            x_max = max(x_max, x + w)
            y_max = max(y_max, y + h)
        return int(x_min), int(y_min), int(x_max), int(y_max)

# Path to the directory
    base_dir = f"images[0][0]"

# Get a list of all folder in the base directory
    subdirectories = [os.path.join(base_dir, d) for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]

# Loop through each folder and process the image pairs
    for subdirectory in subdirectories:
        # Get the fixed (optical) and moving (thermal) image filenames
        nir_on_image_path = None
        nir_off_image_path = None
        moving_image_filename = None
        print(subdirectory)

        for filename in os.listdir(subdirectory):
            if filename.endswith('-NIR-OFF.jpg'):
                nir_off_image_path=os.path.join(subdirectory, filename)
            elif filename.endswith('-NIR-ON.jpg'):
                nir_on_image_path = os.path.join(subdirectory, filename)
            elif filename.endswith('.pgm'):
                moving_image_filename = filename

        moving_image_path = os.path.join(subdirectory, moving_image_filename)

        # Convert the PGM image to JPG with proper normalization
        image = cv2.imread(moving_image_path, cv2.IMREAD_UNCHANGED)
        image_normalized = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
        image_normalized = image_normalized.astype(np.uint8)
        jpg_path = os.path.splitext(moving_image_path)[0] + '.jpg'
        cv2.imwrite(jpg_path, image_normalized)

        # Save the output images
        output_filename = f"registered.jpg"
        cropped_output_filename = f"cropped.jpg"
        final_image_filename=f"4_band_with_thermal.tif"
        #final_cropped_filename = f"4_band_with_thermal_cropped.tif"

        if not os.path.exists(output_filename) or not os.path.exists(cropped_output_filename):
            #Perform the registration and get the final transform
            transform, output, cropped_output, four_band = mutual_information_registration(nir_off_image_path, jpg_path)

            cv2.imwrite(os.path.join(subdirectory, output_filename), output)
            cv2.imwrite(os.path.join(subdirectory, cropped_output_filename), cropped_output)
            cv2.imwrite(os.path.join(subdirectory, final_image_filename), four_band)
            #cv2.imwrite(os.path.join(subdirectory, final_cropped_filename), four_band_cropped)
            W=four_band.shape[1]
            H=four_band.shape[0]

            # extract NIR and add as band
            NI = cv2.imread(nir_on_image_path)
            NIR = cv2.cvtColor(NIR, cv2.COLOR_BGR2RGB)
            red_channel_NIR = NIR[:, :, 0]
            without_NIR = cv2.imread(nir_off_image_path)
            without_NIR = cv2.cvtColor(without_NIR, cv2.COLOR_BGR2RGB)
            red_channel = without_NIR[:, :, 0]
            Nir_band = cv2.subtract(red_channel_NIR, red_channel)
            Nir_band_resized = cv2.resize(Nir_band, (W,H))
            cv2.imwrite(os.path.join(subdirectory, "NIR band.png"), Nir_band_resized)

            final_five_band = np.dstack((four_band, Nir_band_resized))
            final_five_band = np.clip(final_five_band, 0, 255).astype(np.uint8)
            final_five_band_reordered = np.transpose(final_five_band, (2, 0, 1))


            transform = from_origin(0, 100, 1, 1)  # Adjust as needed
            final_path = os.path.join(subdirectory, "final_5_band.tif")

            # Save the final 5-band image as a TIFF
            with rasterio.open(final_path, 'w', driver='GTiff', 
                               height=final_five_band_reordered.shape[1],
                               width=final_five_band_reordered.shape[2],
                               count=final_five_band_reordered.shape[0],
                               dtype=final_five_band_reordered.dtype,
                               transform=transform) as dst:
                dst.write(final_five_band_reordered)

            print("final_five_band dtype:", final_five_band_reordered.dtype)

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


        # take new photos
        #image = take_photo(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000)

        #        image = TTFinishByOtherwise(take_photo(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), TTTimeDeadline=timestamp + timeout_val, TTPlanB=image_planb(), TTWillContinue=False)
        images = TTFinishByOtherwise(take_two_photo(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), TTTimeDeadline=timestamp + timeout_val, TTPlanB=image_planb(), TTWillContinue=False)

        # Call lepton and capture sequentially to get temperature and IR image
        # from Flir Lepton
        #runlepton = lepton_record(trigger)

        lepton = TTFinishByOtherwise(lepton_record(sample_window, TTClock=root_clock, TTPeriod=3000000, TTPhase=0, TTDataIntervalWidth=250000), TTTimeDeadline=timestamp + timeout_val, TTPlanB=TTSingleRunTimeout(lepton_planb(), TTTimeout=10_000_000), TTWillContinue=True)

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
        add_exif = exif(images, gps_exif, imu_data)

        # classification test on single file
        #run_tf_classify = tf_classify(image)

        multi_coreg(images, lepton)

        # Segformer 5 band
        segformer(images[0][0])

        #reset = TTSingleRunTimeout(lepton_planb(), TTTimeout=1_000_000)
