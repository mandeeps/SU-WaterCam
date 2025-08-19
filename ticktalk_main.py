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
    print(f"\n running coreg on {dirname}\n")
    filepath = coreg(dirname)
    print(f"\n {filepath} images registered \n")
    return True

@SQify
def segformer(filepath, coreg_state): # operate on coregistered image file
    import subprocess
    segformer_python = "/home/pi/miniforge3/envs/5band/bin/python"

    segformer_coreg = "/home/pi/segformer_5band/segment_tiff_5band.py"
    segmented_file = filepath + "/final_5_band.tiff"
    subprocess.Popen([segformer_python, segformer_coreg, segmented_file], cwd="/home/pi/segformer_5band").wait()
    return filepath + "/final_5_band_segmentation.png"

@SQify
def call_shutdown(state):
    import sys
    from subprocess import call

    global sq_state
    sq_state['count'] = sq_state.get('count', 0) + 1
    
    print(f"\n Iteration: {sq_state['count']} \n")
    
    limit = 3
    if sq_state['count'] == limit:
        print("\n shutdown \n")
        # using an /etc/doas.conf configured for user pi
        #call("doas /usr/sbin/shutdown", shell=True) # shutdown Pi
        sys.exit("shutdown") # exit program

@SQify
def flir_planb(dummy):
    from tools.lepton_reset_gpiozero import reset
    print("\n reset lepton \n")
    reset()

@SQify
def compress_bitmap(segmented_file):
    from tools.compress_segmented import compress_image
    print(f"Compressing {segmented_file} for transmission")
    bitmap_dict = compress_image(segmented_file)
    print(f"Completed compressing {segmented_file}")
    return bitmap_dict['compressed_data'] # this is the byte data

@SQify
def lora_token(bitmap):
    from ticktalkpython.Clock import TTClock
    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Time import TTTime
    import pickle

    from tools.lora_handler_concurrent import get_lora_handler, get_config_value, transmit_data, transmit_binary, compressed_encoding

    from ticktalkpython.Tag import TTTag
    from ticktalkpython import NetworkInterfaceLoRa
    from tools.bno055_imu import get_orientation
    from tools.aht20_temperature import get_aht20
    from tools.get_gps import get_lat_lon

    from pympler import asizeof
    from sys import getsizeof

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


    handler = get_lora_handler()

#    print(f" \n Size of Unencoded Data object asizeof: {asizeof.asizeof(data)} \n")
#    print(f" \n Size of Unencoded Data object getsizeof: {getsizeof(data)} \n")

    # check transmission without TT token
    handler.queue_transmit(data)
    handler.process_transmit_queue()

    # test transmission of Token containing encoded data
    enc_data = compressed_encoding(data)
#    print(f" \n Size of Encoded Data object: {asizeof.asizeof(enc_data)} \n")
#    print(f" \n Size of Encoded Data object getsizeof: {getsizeof(enc_data)} \n")
    token_1 = TTToken(enc_data, time_1, False,
    TTTag(context, sq_name, 4, recipient_device))
    lora_msg = NetworkInterfaceLoRa.TTLoRaMessage(token_1, recipient_device)
    encoded_msg = lora_msg.encode_token()
    packet = encoded_msg.hex()
    handler.queue_binary_transmit(packet)
    handler.process_transmit_queue()
#    print(f" \n Size of Tokenized Data object: {asizeof.asizeof(packet)} \n")
#    print(f" \n Size of Tokenized data object getsizeof: {getsizeof(packet)} \n")

    token_2 = TTToken(bitmap, time_1, False,
    TTTag(context, sq_name, 4, recipient_device))
    lora_msg2 = NetworkInterfaceLoRa.TTLoRaMessage(token_2, recipient_device)
    encoded_msg2 = lora_msg2.encode_token()
    packet2 = encoded_msg2.hex()
    handler.queue_binary_transmit(packet2)

#    print(f" \n Size of Bitmap object: {asizeof.asizeof(bitmap)} \n")
#    print(f" \n Size of Bitmap object getsizeof: {getsizeof(bitmap)} \n")
    
    handler.queue_binary_transmit(bitmap)

    print(f" \n Size of Tokenized Bitmap object: {asizeof.asizeof(packet2)} \n")
    print(f" \n Size of Tokenized Bitmap object getsizeof: {getsizeof(packet2)} \n")
    handler.process_transmit_queue()


@SQify
def lora_listener():
    from time import sleep
    from lora_handler_concurrent import get_lora_handler
    # This should start the listening thread
    handler = get_lora_handler()
    
    from lora_handler_concurrent import get_config_value

    while True:
        # Get current configuration (automatically updated by incoming messages)
        area_threshold = get_config_value('area_threshold', 10)
        monitoring_freq = get_config_value('monitoring_frequency', 60)
        emergency_mode = get_config_value('emergency_mode', False)
        
        print(f"Current config: Area={area_threshold}%, Freq={monitoring_freq}min, Emergency={emergency_mode}")
        
        # Adjust behavior based on configuration
        if emergency_mode:
            # Increase monitoring frequency
            sleep_time = 30
        else:
            sleep_time = monitoring_freq * 60
        
        sleep(sleep_time)

@GRAPHify
def ttmain(trigger):
    with TTClock.root() as root_clock:

        token, dirname = get_time(trigger, TTClock=root_clock, TTPeriod=60_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)

       
        photo = take_two_photos(trigger, dirname, TTPersistent=True)

        lepton_file = flir(dirname) #, TTClock=root_clock, TTPeriod=1_000_000, TTPhase=0, TTDataInterval=400_000)

        deadline_time = READ_TTCLOCK(token, TTClock=root_clock) + 5_000_000

        lepton = TTFinishByOtherwise(lepton_file, TTTimeDeadline=deadline_time, TTPlanB=TTSingleRunTimeout(flir_planb(token), TTTimeout=3_000_000), TTWillContinue=False)

        coreg_state = coregistration(dirname, lepton, photo, TTPersistent=True)
        seg_result = segformer(dirname, coreg_state, TTPersistent=True)
        bitmap = compress_bitmap(seg_result)
        lora_return = lora_token(bitmap)

# listen for incoming lora packets
#        received = lora_listener(TTPersistent=True)
 
        y = call_shutdown(lora_return)
