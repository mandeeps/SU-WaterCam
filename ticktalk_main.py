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
    from coreg_multiple import coreg
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
    print("\n reset lepton \n")
    from gpiozero import DigitalOutputDevice, DigitalInputDevice
    from time import sleep

    reset_pin = 6
    reset = DigitalOutputDevice(reset_pin, active_high=True, initial_value=True)
    reset.off()
    sleep(1.0)
    reset.on()
    reset.close()
    reset_input = DigitalInputDevice(reset_pin)
    print(f"Pin {reset_pin}: {reset_input.value}")

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
        
        coreg_state = coregistration(dirname, lepton, photo, TTPersistent=True)
        segformer_state = segformer(dirname, coreg_state, TTPersistent=True)

        y = call_shutdown(segformer_state)
