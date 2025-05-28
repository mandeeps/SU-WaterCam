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

@GRAPHify
def ttmain(trigger):
    with TTClock.root() as root_clock:
# main(trigger, "/home/pi") #x = test(trigger)

        dirname = get_time(trigger, TTClock=root_clock, TTPeriod=10_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)
        photo = take_two_photos(trigger, dirname, TTPeriod=10_000_000, TTPersistent=True)
        lepton = flir(dirname, TTPeriod=10_000_000, TTPersistent=True)
        
        coreg_state = coregistration(dirname, lepton, photo)
        segformer_state = segformer(dirname, coreg_state)

        y = call_shutdown(segformer_state)
