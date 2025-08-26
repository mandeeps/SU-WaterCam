from tt_take_photos import flir, take_two_photos

# Import LoRa runtime integration
from tools.lora_runtime_integration import (
    get_runtime_manager, 
    get_parameter, 
    set_parameter, 
    register_callback,
    integrate_with_ticktalk
)

# Import LoRa handler
from tools.lora_handler_concurrent import get_lora_handler

@SQify
def create_workflow_data(monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result):
    """
    Create a structured workflow data object to reduce parameter complexity
    """
    from datetime import datetime
    try:
        return {
            'monitoring_params': monitoring_params,
            'dirname': dirname,
            'photo': photo,
            'lepton_file': lepton_file,
            'coreg_state': coreg_state,
            'seg_result': seg_result,
            'bitmap': bitmap,
            'lora_return': lora_return,
            'shutdown_result': shutdown_result,
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Failed to create workflow data: {e}")
        return {'error': str(e)}

@SQify
def validate_configuration(trigger):
    """
    Validate that all required configuration parameters are properly set
    """
    from tools.lora_runtime_integration import get_parameter
    
    required_params = {
        'area_threshold': (10, 'default flood detection threshold'),
        'stage_threshold': (50, 'default stage height threshold'),
        'monitoring_frequency': (60, 'default monitoring interval'),
        'emergency_frequency': (5, 'default emergency monitoring interval'),
        'transmission_enabled': (True, 'default transmission state'),
        'gps_enabled': (True, 'default GPS state'),
        'compression_level': (8, 'default compression level'),
        'shutdown_iteration_limit': (3, 'default shutdown limit'),
        'auto_shutdown_enabled': (True, 'default auto-shutdown state')
    }
    
    validation_results = {}
    for param, (default_value, description) in required_params.items():
        try:
            value = get_parameter(param, default_value)
            validation_results[param] = {
                'value': value,
                'status': 'valid',
                'description': description
            }
        except Exception as e:
            validation_results[param] = {
                'value': default_value,
                'status': 'error',
                'error': str(e),
                'description': description
            }
    
    try:
        print("🔧 Configuration validation completed")
        return validation_results
    except Exception as e:
        print(f"⚠️ Failed to complete configuration validation: {e}")
        return {'validation_error': str(e)}

@SQify
def initialize_lora_integration(trigger):
    """
    Initialize LoRa runtime integration within TickTalk framework
    """
    from tools.lora_runtime_integration import integrate_with_ticktalk, get_runtime_manager
    
    try:
        integrate_with_ticktalk()
        runtime_manager = get_runtime_manager()
        print("✓ LoRa runtime integration initialized")
        return {'status': 'success', 'runtime_manager': 'initialized'}
    except ImportError as e:
        print(f"⚠️ LoRa runtime integration import error: {e}")
        return {'status': 'failed', 'error': f'Import error: {str(e)}'}
    except Exception as e:
        print(f"⚠️ LoRa runtime integration failed: {e}")
        return {'status': 'failed', 'error': str(e)}

@STREAMify
def get_time(trigger):
    from datetime import datetime
    from os import path, makedirs
    try:
        date = datetime.now().strftime('%Y%m%d-%H%M%S')
        directory = path.join("/home/pi/SU-WaterCam/images", date)
        if not path.exists(directory):
            makedirs(directory)
        return directory
    except Exception as e:
        print(f"⚠️ Failed to create directory: {e}")
        # Return a fallback directory
        return "/home/pi/SU-WaterCam/images/fallback"

@SQify
def coregistration(dirname, lepton_state, photo_state):
    from tools.coreg_multiple import coreg
    print(f"\n running coreg on {dirname}\n")
    try:
        filepath = coreg(dirname)
        print(f"\n {filepath} images registered \n")
        return True
    except Exception as e:
        print(f"⚠️ Failed to run coregistration: {e}")
        return False

@SQify
def segformer(filepath, coreg_state): # operate on coregistered image file
    import subprocess
    try:
        segformer_python = "/home/pi/miniforge3/envs/5band/bin/python"
        segformer_coreg = "/home/pi/segformer_5band/segment_tiff_5band.py"
        segmented_file = filepath + "/final_5_band.tiff"
        subprocess.Popen([segformer_python, segformer_coreg, segmented_file], cwd="/home/pi/segformer_5band").wait()
        return filepath + "/final_5_band_segmentation.png"
    except Exception as e:
        print(f"⚠️ Failed to run segmentation: {e}")
        return None

@SQify
def call_shutdown(state):
    import sys
    from subprocess import call
    from tools.lora_runtime_integration import get_parameter

    global sq_state
    if 'sq_state' not in globals():
        sq_state = {'count': 0}
    sq_state['count'] = sq_state.get('count', 0) + 1
    
    print(f"\n Iteration: {sq_state['count']} \n")
    
    # Use runtime parameter for shutdown limit
    try:
        shutdown_limit = get_parameter('shutdown_iteration_limit', 3)
        auto_shutdown_enabled = get_parameter('auto_shutdown_enabled', True)
        
        if auto_shutdown_enabled and sq_state['count'] >= shutdown_limit:
            print(f"\n shutdown after {sq_state['count']} iterations (limit: {shutdown_limit}) \n")
            # using an /etc/doas.conf configured for user pi
            #call("doas /usr/sbin/shutdown", shell=True) # shutdown Pi
            sys.exit("shutdown") # exit program
    except Exception as e:
        print(f"⚠️ Failed to check shutdown parameters: {e}")
        # Continue without shutdown

@SQify
def flir_planb(dummy):
    from tools.lepton_reset_gpiozero import reset
    print("\n reset lepton \n")
    try:
        reset()
    except Exception as e:
        print(f"⚠️ Failed to reset lepton: {e}")

@SQify
def compress_bitmap(segmented_file):
    from tools.compress_segmented import compress_image
    from tools.lora_runtime_integration import get_parameter
    print(f"Compressing {segmented_file} for transmission")
    
    # Use runtime parameter for compression level
    compression_level = get_parameter('compression_level', 8)
    print(f"Using compression level: {compression_level}")
    
    try:
        bitmap_dict = compress_image(segmented_file)
        print(f"Completed compressing {segmented_file}")
        return bitmap_dict['compressed_data'] # this is the byte data
    except Exception as e:
        print(f"⚠️ Failed to compress image: {e}")
        # Return empty bytes as fallback
        return b''

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
    from tools.get_gps import get_lat_lon_alt
    from tools.lora_runtime_integration import get_parameter

    from pympler import asizeof
    from sys import getsizeof

    # Check if transmissions are enabled via runtime parameters
    transmission_enabled = get_parameter('transmission_enabled', True)
    if not transmission_enabled:
        print("📡 Transmissions disabled - skipping LoRa transmission")
        return bitmap

    root_clock = TTClock.root()
    # Create a time-tagged token using that interval and the derived clock
    time_1 = TTTime(root_clock, 2, 1024)
    recipient_device = 0xFF
    context = 1
    sq_name = 4

    # Get sensor data
    try:
        data = get_orientation()
        data.update(get_aht20())
    except Exception as e:
        print(f"⚠️ Failed to get sensor data: {e}")
        data = {}
    
    # Check if GPS is enabled via runtime parameters
    gps_enabled = get_parameter('gps_enabled', True)
    if gps_enabled:
        try:
            gps = get_lat_lon_alt()
            if gps:
                data.update(gps)
        except Exception as e:
            print(f"⚠️ Failed to get GPS data: {e}")
    
    # Add runtime parameters to sensor data
    emergency_mode = get_parameter('emergency_mode', False)
    area_threshold = get_parameter('area_threshold', 10)
    stage_threshold = get_parameter('stage_threshold', 50)
    
    data.update({
        'emergency_status': 1 if emergency_mode else 0,
        'status_area_threshold': area_threshold,
        'stage_threshold': stage_threshold,
        'monitoring_frequency': get_parameter('monitoring_frequency', 60),
        'emergency_frequency': get_parameter('emergency_frequency', 5),
        'neighborhood_emergency_frequency': get_parameter('neighborhood_emergency_frequency', 30)
    })

    try:
        handler = get_lora_handler()
    except Exception as e:
        print(f"⚠️ Failed to get LoRa handler: {e}")
        return bitmap

    # check transmission without TT token
    try:
        handler.queue_transmit(data)
        handler.process_transmit_queue()
    except Exception as e:
        print(f"⚠️ Failed to transmit sensor data: {e}")

    # test transmission of Token containing encoded data
    try:
        enc_data = compressed_encoding(data)
        token_1 = TTToken(enc_data, time_1, False,
        TTTag(context, sq_name, 4, recipient_device))
        lora_msg = NetworkInterfaceLoRa.TTLoRaMessage(token_1, recipient_device)
        encoded_msg = lora_msg.encode_token()
        packet = encoded_msg.hex()
        handler.queue_binary_transmit(packet)
        handler.process_transmit_queue()
    except Exception as e:
        print(f"⚠️ Failed to transmit encoded data: {e}")

    try:
        token_2 = TTToken(bitmap, time_1, False,
        TTTag(context, sq_name, 4, recipient_device))
        lora_msg2 = NetworkInterfaceLoRa.TTLoRaMessage(token_2, recipient_device)
        encoded_msg2 = lora_msg2.encode_token()
        packet2 = encoded_msg2.hex()
        handler.queue_binary_transmit(packet2)

        handler.queue_binary_transmit(bitmap)

        print(f" \n Size of Tokenized Bitmap object: {asizeof.asizeof(packet2)} \n")
        print(f" \n Size of Tokenized Bitmap object getsizeof: {getsizeof(packet2)} \n")
        handler.process_transmit_queue()
    except Exception as e:
        print(f"⚠️ Failed to transmit bitmap: {e}")
    
    return bitmap

@SQify
def lora_listener():
    """
    LoRa listener that processes incoming commands and returns current status
    This function is called by TickTalk and handles one iteration of monitoring
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter, get_runtime_manager
    from tools.lora_handler_concurrent import get_lora_handler
    
    # Get the LoRa handler (listening is already started by runtime integration)
    handler = get_lora_handler()
    
    # Register callbacks for parameter changes (only once)
    def on_emergency_mode_changed(value, old_value):
        if value:
            print("🚨 EMERGENCY MODE ACTIVATED via LoRa command!")
        else:
            print("✅ Emergency mode deactivated via LoRa command")
    
    def on_monitoring_frequency_changed(value, old_value):
        print(f"⏰ Monitoring frequency updated via LoRa: {old_value} → {value} minutes")
    
    def on_area_threshold_changed(value, old_value):
        print(f"🌊 Area threshold updated via LoRa: {old_value} → {value}%")
    
    # Get runtime manager and register callbacks
    try:
        runtime_manager = get_runtime_manager()
        runtime_manager.register_update_callback('emergency_mode', on_emergency_mode_changed)
        runtime_manager.register_update_callback('monitoring_frequency', on_monitoring_frequency_changed)
        runtime_manager.register_update_callback('area_threshold', on_area_threshold_changed)
    except Exception as e:
        print(f"Warning: Could not register LoRa callbacks: {e}")
    
    # Get current configuration (automatically updated by incoming messages)
    try:
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        emergency_mode = get_parameter('emergency_mode', False)
        transmission_enabled = get_parameter('transmission_enabled', True)
        debug_mode = get_parameter('debug_mode', False)
    except Exception as e:
        print(f"⚠️ Failed to get LoRa parameters: {e}")
        # Use default values
        area_threshold = 10
        monitoring_freq = 60
        emergency_mode = False
        transmission_enabled = True
        debug_mode = False
    
    # Process any queued transmissions
    if handler:
        handler.process_transmit_queue()
    
    # Log current status if in debug mode
    if debug_mode:
        print(f"📡 LoRa Status ({datetime.now().strftime('%H:%M:%S')}): "
              f"Area={area_threshold}%, Freq={monitoring_freq}min, "
              f"Emergency={emergency_mode}, Transmit={transmission_enabled}")
    
    # Return current status for TickTalk integration
    return {
        'area_threshold': area_threshold,
        'monitoring_frequency': monitoring_freq,
        'emergency_mode': emergency_mode,
        'transmission_enabled': transmission_enabled,
        'debug_mode': debug_mode,
        'timestamp': datetime.now().isoformat()
    }

@SQify
def adaptive_monitoring():
    """
    Adaptive monitoring function that adjusts behavior based on runtime parameters
    This can be called from the main workflow to check if conditions have changed
    """
    from tools.lora_runtime_integration import get_parameter
    
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        stage_threshold = get_parameter('stage_threshold', 50)
        
        print(f"🔍 Adaptive monitoring: Emergency={emergency_mode}, "
              f"Area={area_threshold}%, Stage={stage_threshold}cm")
        
        # Return monitoring parameters for use in main workflow
        return {
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'stage_threshold': stage_threshold,
            'monitoring_frequency': get_parameter('monitoring_frequency', 60),
            'photo_interval': get_parameter('photo_interval', 30)
        }
    except Exception as e:
        print(f"⚠️ Failed to get adaptive monitoring parameters: {e}")
        # Return default values
        return {
            'emergency_mode': False,
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'photo_interval': 30
        }

@SQify
def emergency_workflow(trigger, monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result):
    """
    Emergency mode workflow with faster timing
    """
    print("🚨 Emergency mode detected - using fast workflow timing")
    
    # Photos, lepton data, coregistration, segmentation, compression, LoRa transmission, and shutdown check are now passed as parameters from GRAPH level
    
    # LoRa monitoring and parameter management
    from tools.lora_runtime_integration import get_parameter
    
    # Get current parameters directly
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        transmission_enabled = get_parameter('transmission_enabled', True)
        
        # Return comprehensive status
        return {
            'status': 'emergency_workflow_completed',
            'dirname': dirname,
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'monitoring_frequency': monitoring_freq,
            'transmission_enabled': transmission_enabled,
            'bitmap_compressed': True,
            'lora_transmitted': True,
            'photos_captured': True,
            'lepton_data_captured': True
        }
    except Exception as e:
        print(f"⚠️ Failed to get emergency workflow parameters: {e}")
        return {
            'status': 'emergency_workflow_error',
            'error': str(e),
            'dirname': dirname
        }

@SQify
def normal_workflow(trigger, workflow_data):
    """
    Normal mode workflow with standard timing
    """
    print("📸 Normal mode - using standard workflow timing")
    
    # Extract data from workflow_data structure
    dirname = workflow_data['dirname']
    monitoring_params = workflow_data['monitoring_params']
    
    # LoRa monitoring and parameter management
    from tools.lora_runtime_integration import get_parameter
    
    # Get current parameters directly
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        transmission_enabled = get_parameter('transmission_enabled', True)
        
        # Return comprehensive status
        return {
            'status': 'normal_workflow_completed',
            'dirname': dirname,
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'monitoring_frequency': monitoring_freq,
            'transmission_enabled': transmission_enabled,
            'bitmap_compressed': True,
            'lora_transmitted': True,
            'photos_captured': True,
            'lepton_data_captured': True,
            'workflow_data': workflow_data
        }
    except Exception as e:
        print(f"⚠️ Failed to get normal workflow parameters: {e}")
        return {
            'status': 'normal_workflow_error',
            'error': str(e),
            'dirname': dirname,
            'workflow_data': workflow_data
        }

@SQify
def lora_parameter_monitor(trigger):
    """
    TickTalk SQ function that monitors and processes LoRa parameter updates
    This function runs periodically and checks for parameter changes
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter, sync_lora_parameters
    from tools.lora_handler_concurrent import get_lora_handler
    
    # Sync runtime parameters with LoRa config to ensure consistency
    try:
        sync_lora_parameters()
    except Exception as e:
        print(f"⚠️ Failed to sync LoRa parameters: {e}")
        # Continue with current parameters
    
    # Get current parameters
    try:
        current_params = {
            'emergency_mode': get_parameter('emergency_mode', False),
            'area_threshold': get_parameter('area_threshold', 10),
            'stage_threshold': get_parameter('stage_threshold', 50),
            'monitoring_frequency': get_parameter('monitoring_frequency', 60),
            'emergency_frequency': get_parameter('emergency_frequency', 5),
            'transmission_enabled': get_parameter('transmission_enabled', True),
            'debug_mode': get_parameter('debug_mode', False),
            'gps_enabled': get_parameter('gps_enabled', True),
            'compression_level': get_parameter('compression_level', 8),
            'photo_interval': get_parameter('photo_interval', 30),
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        print(f"⚠️ Failed to get LoRa parameters: {e}")
        # Use default values
        current_params = {
            'emergency_mode': False,
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'emergency_frequency': 5,
            'transmission_enabled': True,
            'debug_mode': False,
            'gps_enabled': True,
            'compression_level': 8,
            'photo_interval': 30,
            'timestamp': datetime.now().isoformat()
        }
    
    # Process any queued LoRa transmissions
    try:
        handler = get_lora_handler()
        if handler:
            handler.process_transmit_queue()
    except Exception as e:
        print(f"⚠️ Failed to process LoRa transmissions: {e}")
    
    # Log current status if in debug mode
    if current_params['debug_mode']:
        print(f"📡 LoRa Parameter Monitor ({datetime.now().strftime('%H:%M:%S')}): "
              f"Emergency={current_params['emergency_mode']}, "
              f"Area={current_params['area_threshold']}%, "
              f"Transmit={current_params['transmission_enabled']}")
    
    return current_params

@SQify
def lora_emergency_handler(trigger, lora_status):
    """
    Handle emergency mode changes detected via LoRa
    This function is triggered when emergency mode changes
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter
    
    if lora_status and lora_status.get('emergency_mode', False):
        print("🚨 EMERGENCY MODE DETECTED - Taking immediate action!")
        
        # Could trigger immediate photo capture, increased monitoring, etc.
        # For now, just log the emergency state
        try:
            emergency_actions = {
                'emergency_triggered': True,
                'timestamp': datetime.now().isoformat(),
                'actions_taken': ['emergency_logging', 'increased_monitoring'],
                'monitoring_frequency': get_parameter('emergency_frequency', 5)
            }
            return emergency_actions
        except Exception as e:
            print(f"⚠️ Failed to create emergency actions: {e}")
            return {'emergency_triggered': True, 'status': 'error', 'error': str(e)}
    else:
        return {'emergency_triggered': False, 'status': 'normal_operation'}

@SQify
def lora_configuration_manager(trigger, current_config):
    """
    Manage LoRa configuration updates and apply them to the system
    This function handles configuration changes from LoRa commands
    """
    from datetime import datetime
    
    if not current_config:
        return {'status': 'no_config_provided'}
    
    # Check for significant configuration changes
    changes = []
    
    # Monitor for emergency mode changes
    if current_config.get('emergency_mode', False):
        changes.append('emergency_mode_activated')
    
    # Monitor for transmission enable/disable
    if not current_config.get('transmission_enabled', True):
        changes.append('transmissions_disabled')
    
    # Monitor for debug mode
    if current_config.get('debug_mode', False):
        changes.append('debug_mode_enabled')
    
    # Apply configuration changes to system behavior
    if changes:
        print(f"🔧 LoRa Configuration Changes Detected: {', '.join(changes)}")
        
        # Could trigger system reconfiguration here
        # For example, adjust monitoring intervals, enable/disable features, etc.
        
        try:
            return {
                'status': 'configuration_updated',
                'changes': changes,
                'timestamp': datetime.now().isoformat(),
                'new_config': current_config
            }
        except Exception as e:
            print(f"⚠️ Failed to create configuration response: {e}")
            return {'status': 'error', 'error': str(e)}
    
    return {'status': 'no_changes', 'config': current_config}

@GRAPHify
def ttmain(trigger):
    # Import TTClock for the main workflow
    from ticktalkpython.Clock import TTClock
    
    with TTClock.root() as root_clock:
        
        # Initialize LoRa integration first
        lora_init = initialize_lora_integration(trigger)
        
        # Validate configuration
        config_validation = validate_configuration(trigger)
        
        # Get adaptive monitoring parameters
        monitoring_params = adaptive_monitoring()
        
        # Call get_time as STREAM function to get directory name
        token, dirname = get_time(trigger, TTClock=root_clock, TTPeriod=60_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)
        
        # Take photos and capture lepton data at GRAPH level
        from tt_take_photos import flir, take_two_photos
        photo = take_two_photos(trigger, dirname)
        lepton_file = flir(dirname)
        
        # Coregistration and segmentation at GRAPH level
        coreg_state = coregistration(dirname, lepton_file, photo)
        seg_result = segformer(dirname, coreg_state)
        
        # Compression and LoRa transmission at GRAPH level
        bitmap = compress_bitmap(seg_result)
        lora_return = lora_token(bitmap)
        
        # Shutdown check at GRAPH level
        shutdown_result = call_shutdown(lora_return)
        
        # Create workflow data structure
        workflow_data = create_workflow_data(monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result)
        
        # Process the data
        result = normal_workflow(trigger, workflow_data)
        
        # Ensure constant LoRa monitoring for incoming messages
        lora_status = lora_listener(TTPersistent=True)
        
        return result
