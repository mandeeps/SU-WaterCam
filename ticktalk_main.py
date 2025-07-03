from tt_take_photos import flir, take_two_photos

@STREAMify
def get_time(trigger):
    from datetime import datetime
    from os import path, makedirs
    date = datetime.now().strftime('%Y%m%d-%H%M%S')
    directory = path.join("/home/pi/SU-WaterCam/images", date)
    if not path.exists(directory):
        makedirs(directory)

    return directory

@SQify
def coregistration(dirname, lepton_state, photo_state):
    from tools.coreg_multiple import coreg
    print("\n running coreg \n")
    filepath = coreg(dirname)
    print(f"\n {filepath} \n")
    return True

@SQify
def segformer(filepath, coreg_state): # operate on coregistered image file
    import subprocess
    segformer_python = "/home/pi/miniforge3/envs/5band/bin/python"

    segformer_coreg = "/home/pi/git/segformer_5band/segment_tiff_5band.py"
    segmented_file = filepath + "/final_5_band.tiff"
    subprocess.Popen([segformer_python, segformer_coreg, segmented_file], cwd="/home/pi/git/segformer_5band").wait()
    return segmented_file

@SQify
def call_shutdown(state):
    import sys

    global sq_state
    sq_state['count'] = sq_state.get('count', 0) + 1
    
    print(f"\n Iteration: {sq_state['count']} \n")
    
    limit = 5
    if sq_state['count'] == limit:
        print("\n shutdown \n")
        # using an /etc/doas.conf configured for user pi
        call("doas /usr/sbin/shutdown", shell=True) # shutdown Pi
        sys.exit("shutdown") # exit program

@SQify
def flir_planb():
    from tools.lepton_reset_gpiozero import reset
    print("\n reset lepton \n")
    reset()

@SQify
def compress_bitmap(segmented_file):
    # handle calling compression function once that performs reasonably on a Pi 4
    return True

@SQify
def lora_token(bitmap):
    from ticktalkpython.Clock import TTClock
    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Time import TTTime
    import pickle
    from tools.lora_transmit import transmit
    from tools.lora_transmit import compressed_encoding
    from ticktalkpython.Tag import TTTag
    from ticktalkpython import NetworkInterfaceLoRa
    from tools.bno055_imu import get_orientation
    from tools.aht20_temperature import get_aht20
    from tools.get_gps import get_lat_lon

    root_clock = TTClock.root()
    # Create a time-tagged token using that interval and the derived clock
    time_1 = TTTime(root_clock, 2, 1024)
    recipient_device = 0xFF
    context = 1
    sq_name = 4

    data = get_orientation()
    data.update(get_aht20())
    gps = get_lat_lon()
    if gps:
        data.update(get_lat_lon())
    # add other values (device health, battery, gps, flood status, etc.,)

    packet = compressed_encoding(data)

    token_1 = TTToken(data, time_1, False,
    TTTag(context, sq_name, 4, recipient_device))

    lora_msg = NetworkInterfaceLoRa.TTLoRaMessage(token_1, recipient_device)
    encoded_msg = lora_msg.encode_token()
    data = encoded_msg.hex()
    transmit(f"AT+SENDB={data}\r\n".encode())

@GRAPHify
def ttmain(trigger):
    with TTClock.root() as root_clock:

        token, dirname = get_time(trigger, TTClock=root_clock, TTPeriod=10_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)
        photo = take_two_photos(trigger, dirname, TTPersistent=True)

        lepton_file = flir(dirname) #, TTClock=root_clock, TTPeriod=1_000_000, TTPhase=0, TTDataInterval=400_000)

        deadline_time = READ_TTCLOCK(token, TTClock=root_clock) + 5_000_000

        lepton = TTFinishByOtherwise(lepton_file, TTTimeDeadline=deadline_time, TTPlanB=flir_planb(), TTWillContinue=False)

        coreg_state = coregistration(dirname, lepton, photo, TTPersistent=True)
        seg_result = segformer(dirname, coreg_state, TTPersistent=True)
        bitmap = compress_bitmap(seg_result)
        lora_return = lora_token(bitmap)
        y = call_shutdown(lora_return)
