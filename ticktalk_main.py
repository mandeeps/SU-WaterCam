from take_nir_photos import flir, take_two_photos

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
def coreg(dirname):
    return None # return filename of coregistered image for use by SegFormer

@SQify
def segformer(filepath): # operate on coregistered image file
    import subprocess

    return None

@SQify
def call_shutdown(flir_state, photo_state):
    import sys

    global sq_state
    sq_state['count'] = sq_state.get('count', 0) + 1
    
    print(f"\n Iteration: {sq_state['count']} \n")
    
    limit = 10
    if sq_state['count'] == limit:
        print("\n shutdown \n")
        sys.exit("shutdown") # schedule system shutdown and exit program

@GRAPHify
def ttmain(trigger):
    with TTClock.root() as root_clock:
# main(trigger, "/home/pi") #x = test(trigger)
        interval = 10_000_000

        dirname = get_time(trigger, TTClock=root_clock, TTPeriod=10_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)
        x = take_two_photos(trigger, dirname, TTPeriod=10_000_000, TTPersistent=True)
        lepton = flir(dirname, TTPeriod=10_000_000, TTPersistent=True)
        y = call_shutdown(lepton, x)
