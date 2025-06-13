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
    segformer_test = "/home/pi/git/segformer_5band/tools/test_no_label.py"

    segformer_coreg = "/home/pi/git/segformer_5band/tools/segment_coreg.py"

    subprocess.Popen([segformer_python, segformer_test], cwd="/home/pi/git/segformer_5band")
    return True

@SQify
def call_shutdown(state):
    import sys

    global sq_state
    sq_state['count'] = sq_state.get('count', 0) + 1
    
    print(f"\n Iteration: {sq_state['count']} \n")
    
    limit = 3
    if sq_state['count'] == limit:
        print("\n shutdown \n")
        sys.exit("shutdown") # schedule system shutdown and exit program

@SQify
def flir_planb():
    from tools.lepton_reset_gpiozero import reset
    print("\n reset lepton \n")
    reset()

@SQify
def lora_token():
    from ticktalkpython.Clock import TTClock
    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Time import TTTime
    import pickle
    from tools.lora_transmit import transmit

    root_clock = TTClock.root()
    # Create a time-tagged token using that interval and the derived clock
    time_1 = TTTime(root_clock, 2, 1024)

    from tools.bno055_imu import get_orientation
    from tools.aht20_temperature import get_aht20

    data = get_orientation()
    data.update(get_aht20())

    token_1 = TTToken(2, None)

    payload = pickle.dumps(token_1)
    payload_hex = payload.hex()
    deserialized_token = pickle.loads(payload)

    transmit(f"AT+SENDB={payload_hex}\r\n".encode())


@GRAPHify
def ttmain(trigger):
    with TTClock.root() as root_clock:
# main(trigger, "/home/pi") #x = test(trigger)

        token, dirname = get_time(trigger, TTClock=root_clock, TTPeriod=10_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)
        photo = take_two_photos(trigger, dirname, TTPersistent=True)

        lepton_file = flir(dirname) #, TTClock=root_clock, TTPeriod=1_000_000, TTPhase=0, TTDataInterval=400_000)

        deadline_time = READ_TTCLOCK(token, TTClock=root_clock) + 5_000_000

        lepton = TTFinishByOtherwise(lepton_file, TTTimeDeadline=deadline_time, TTPlanB=flir_planb(), TTWillContinue=False)

        # flir(dirname, TTPeriod=10_000_000, TTPersistent=True)
        lora_return = lora_token()
        
        coreg_state = coregistration(dirname, lepton, photo, TTPersistent=True)
        segformer_state = segformer(dirname, coreg_state, TTPersistent=True)

        y = call_shutdown(segformer_state)
