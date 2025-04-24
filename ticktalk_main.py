from take_nir_photos import flir, take_two_photos

@SQify
def get_time(trigger):
    from datetime import datetime
    from os import path, makedirs
    date = datetime.now().strftime('%Y%m%d-%H%M')
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
    global sq_state
    sq_state['count'] = sq_state.get('count', 0) + 1

    limit = 10
    if sq_state['count'] == limit:
        print("shutdown")
        # call shutdown

@GRAPHify
def ttmain(trigger):
    with TTClock.root() as root_clock:
       # main(trigger, "/home/pi")
       #x = test(trigger)
       dirname = get_time(trigger)
       x = take_two_photos(trigger, dirname)
       lepton = flir(dirname)
       y = call_shutdown(lepton, x)
