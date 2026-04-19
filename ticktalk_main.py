from tt_take_photos import flir, take_two_photos

# Import TickTalk decorators
from ticktalkpython.SQ import SQify, GRAPHify, STREAMify

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

# Import helper functions
from tools.wittypi_control import get_wittypi_status

@SQify
def wittypi_emergency_control(emergency_mode):
    """
    Control WittyPi schedule based on emergency mode status
    - Emergency ON: Clear shutdown schedule to prevent system shutdown
    - Emergency OFF: Regenerate and apply normal schedule
    """
    try:
        from tools.wittypi_control import clear_shutdown_time, set_schedule
        
        if emergency_mode:
            # Emergency mode activated - clear shutdown schedule
            print("🚨 EMERGENCY MODE: Clearing WittyPi shutdown schedule")
            clear_shutdown_time()
            return {
                'status': 'wittypi_emergency_activated',
                'action': 'shutdown_schedule_cleared',
                'message': 'WittyPi shutdown schedule cleared for emergency mode'
            }
        else:
            # Emergency mode deactivated - regenerate normal schedule
            print("✅ EMERGENCY CLEARED: Regenerating WittyPi normal schedule")
            
            # Get schedule parameters from runtime configuration
            schedule_config = get_wittypi_schedule_config()
            start_hour = schedule_config['start_hour']
            start_minute = schedule_config['start_minute']
            interval_length_minutes = schedule_config['interval_length_minutes']
            num_repetitions_per_day = schedule_config['num_repetitions_per_day']
            
            next_startup_time = set_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day)
            
            return {
                'status': 'wittypi_normal_schedule_restored',
                'action': 'schedule_regenerated',
                'next_startup': next_startup_time,
                'schedule': {
                    'start_hour': start_hour,
                    'start_minute': start_minute,
                    'interval_minutes': interval_length_minutes,
                    'repetitions_per_day': num_repetitions_per_day
                },
                'message': f'WittyPi normal schedule restored, next startup: {next_startup_time}'
            }
            
    except ImportError as e:
        print(f"⚠️ WittyPi control not available: {e}")
        return {
            'status': 'wittypi_unavailable',
            'error': f'Import error: {str(e)}',
            'message': 'WittyPi control functions not available'
        }
    except Exception as e:
        print(f"⚠️ Failed to control WittyPi: {e}")
        return {
            'status': 'wittypi_control_failed',
            'error': str(e),
            'message': 'Failed to control WittyPi schedule'
        }

@SQify
def create_sensor_tracker():
    """
    Create a sensor tracker to monitor value changes and only transmit when threshold is exceeded
    """
    try:
        # Initialize sensor tracker with previous values and change thresholds
        tracker = {
            'previous_values': {},
            'change_threshold': 0.05,  # 5% change threshold
            'transmission_history': {},
            'last_transmission': None
        }
        print("✅ Sensor tracker initialized with 5% change threshold")
        return tracker
    except Exception as e:
        print(f"⚠️ Failed to create sensor tracker: {e}")
        return None

@SQify
def check_sensor_changes(tracker, current_sensor_data):
    """
    Check if sensor values have changed by more than 5% from previous values
    Returns dict of sensors that need transmission
    """
    if not tracker or not current_sensor_data:
        return {}
    
    try:
        sensors_to_transmit = {}
        current_time = 'now'  # In real implementation, use datetime.now()
        
        for sensor_name, current_value in current_sensor_data.items():
            # Skip non-numeric values and special fields
            if not isinstance(current_value, (int, float)) or sensor_name in ['timestamp', 'error']:
                continue
            
            previous_value = tracker['previous_values'].get(sensor_name)
            
            if previous_value is None:
                # First time seeing this sensor - always transmit
                sensors_to_transmit[sensor_name] = {
                    'current_value': current_value,
                    'previous_value': None,
                    'change_percent': 100.0,
                    'reason': 'first_reading'
                }
                tracker['previous_values'][sensor_name] = current_value
            else:
                # Calculate percentage change
                if previous_value != 0:
                    change_percent = abs((current_value - previous_value) / previous_value)
                else:
                    change_percent = 100.0 if current_value != 0 else 0.0
                
                # Check if change exceeds 5% threshold
                if change_percent >= tracker['change_threshold']:
                    sensors_to_transmit[sensor_name] = {
                        'current_value': current_value,
                        'previous_value': previous_value,
                        'change_percent': change_percent * 100,
                        'reason': 'threshold_exceeded'
                    }
                    tracker['previous_values'][sensor_name] = current_value
                else:
                    # Update previous value even if not transmitting
                    tracker['previous_values'][sensor_name] = current_value
        
        # Log transmission decisions if any sensors qualify
        if sensors_to_transmit:
            print(f"📊 Sensors qualifying for transmission (5% threshold):")
            for sensor, details in sensors_to_transmit.items():
                print(f"   {sensor}: {details['previous_value']} → {details['current_value']} "
                      f"({details['change_percent']:.1f}% change)")
        else:
            print(f"📊 No sensors qualify for transmission (all changes < 5%)")
        
        return sensors_to_transmit
        
    except Exception as e:
        print(f"⚠️ Failed to check sensor changes: {e}")
        return {}

@SQify
def update_sensor_tracker(tracker, sensor_data, transmission_result):
    """
    Update sensor tracker with transmission results and maintain history
    """
    if not tracker:
        return
    
    try:
        current_time = 'now'  # In real implementation, use datetime.now()
        
        # Update transmission history
        tracker['last_transmission'] = current_time
        
        # Record which sensors were transmitted
        if transmission_result and 'transmitted_sensors' in transmission_result:
            for sensor_name in transmission_result['transmitted_sensors']:
                if sensor_name not in tracker['transmission_history']:
                    tracker['transmission_history'][sensor_name] = []
                
                tracker['transmission_history'][sensor_name].append({
                    'timestamp': current_time,
                    'value': sensor_data.get(sensor_name, 'unknown'),
                    'change_percent': transmission_result.get('change_percent', {}).get(sensor_name, 0)
                })
        
        print(f"✅ Sensor tracker updated with transmission results")
        
    except Exception as e:
        print(f"⚠️ Failed to update sensor tracker: {e}")
    
    return tracker

@SQify
def lora_token_with_tracker(bitmap, sensor_tracker):
    """
    Enhanced LoRa transmission function that uses sensor tracker to only transmit changed values
    """
    from ticktalkpython.Clock import TTClock
    from ticktalkpython.TTToken import TTToken
    from ticktalkpython.Time import TTTime
    import pickle

    from tools.lora_handler_concurrent import get_lora_handler, get_config_value, transmit_data, transmit_binary, compressed_encoding

    from ticktalkpython.Tag import TTTag
    from ticktalkpython import NetworkInterfaceLoRa
    from tools.bno055_imu import get_orientation
    from tools.aht20_temperature import get_aht20
    from tools.get_gps import get_location_with_retry
    from tools.lora_runtime_integration import get_parameter
    # Helper functions are now imported at module level

    from pympler import asizeof
    from sys import getsizeof

    # LoRa transmission is always enabled

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
    
    # Always attempt to collect GPS data
    try:
        gps, packet = get_location_with_retry()
        if gps:
            data.update(gps)
    except Exception as e:
        print(f"⚠️ Failed to get GPS data: {e}")
    
    # Get WittyPi voltage measurements for battery status
    try:
        from tools.wittypi_control import get_wittypi_status
        wittypi_data = get_wittypi_status()
        if wittypi_data.get('status') == 'wittypi_data_retrieved':
            data.update({
                'wittypi_temperature': wittypi_data.get('temperature', 0.0),
                'wittypi_battery_voltage': wittypi_data.get('battery_voltage', 0.0),
                'wittypi_internal_voltage': wittypi_data.get('internal_voltage', 0.0)
            })
            print(f"🔋 WittyPi data added: temp={wittypi_data.get('temperature', 0.0)}°C, battery={wittypi_data.get('battery_voltage', 0.0)}V, internal={wittypi_data.get('internal_voltage', 0.0)}V")
        else:
            print(f"⚠️ WittyPi data unavailable: {wittypi_data.get('status', 'unknown')}")
    except Exception as e:
        print(f"⚠️ Failed to get WittyPi data: {e}")
    
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

    # Check sensor changes using tracker if available (unless always_transmit_sensors is set)
    always_transmit_sensors = get_parameter('always_transmit_sensors', False)
    if always_transmit_sensors:
        print(f"📡 always_transmit_sensors=True: bypassing sensor change check")
    sensors_to_transmit = {}
    if sensor_tracker and not always_transmit_sensors:
        try:
            # Call check_sensor_changes directly since it's in the same module
            sensors_to_transmit = check_sensor_changes(sensor_tracker, data)
            print(f"📊 Sensor change check: {len(sensors_to_transmit)} sensors qualify for transmission")
        except Exception as e:
            print(f"⚠️ Failed to check sensor changes: {e}")
            # Fall back to transmitting all data if tracker fails
            sensors_to_transmit = {k: {'current_value': v, 'reason': 'tracker_failed'} for k, v in data.items()}

    try:
        handler = get_lora_handler()
    except Exception as e:
        print(f"⚠️ Failed to get LoRa handler: {e}")
        return bitmap

    # Transmit sensor data if: no tracker, always_transmit_sensors, or changes detected
    transmission_result = {'transmitted_sensors': [], 'change_percent': {}}
    
    if always_transmit_sensors or not sensor_tracker or sensors_to_transmit:
        try:
            # Filter data to only include sensors that changed significantly
            if sensor_tracker and sensors_to_transmit:
                filtered_data = {k: v for k, v in data.items() if k in sensors_to_transmit}
                transmission_result['transmitted_sensors'] = list(sensors_to_transmit.keys())
                transmission_result['change_percent'] = {k: v.get('change_percent', 0) for k, v in sensors_to_transmit.items()}
                print(f"📡 Transmitting {len(filtered_data)} sensors with significant changes")
            else:
                filtered_data = data
                transmission_result['transmitted_sensors'] = list(data.keys())
                print(f"📡 Transmitting all sensor data (no tracker, always_transmit_sensors, or all sensors qualify)")
            
            handler.queue_transmit(filtered_data)
            handler.process_transmit_queue()
            
        except Exception as e:
            print(f"⚠️ Failed to transmit sensor data: {e}")
    else:
        print(f"📊 No sensor changes detected - skipping sensor data transmission")

    # Transmit TTToken with full encoded sensor data embedded (preserve headers)
    try:
        enc_data = compressed_encoding(data)
        print(f"🔧 Encoded sensor data: {len(enc_data)} bytes")
        
        # Create TTToken with full encoded sensor data (not compressed)
        token_1 = TTToken(enc_data, time_1, False,
        TTTag(context, sq_name, 4, recipient_device))
        
        # Create LoRa message to preserve headers, but modify it to keep full data
        lora_msg = NetworkInterfaceLoRa.TTLoRaMessage(token_1, recipient_device)
        
        # Instead of calling encode_token() which compresses, we'll manually construct
        # the packet with headers + full sensor data
        try:
            # Get the header information from the LoRa message
            header_data = lora_msg.generate_header_values()
            
            # Combine headers with the full encoded sensor data
            from bitstring import BitArray
            byte_payload = BitArray()
            
            # Add headers (this preserves routing information)
            header_entries = ['sq', 'port', 'context', 'device', 'start_tick', 'stop_tick']
            for header_entry_name in header_entries:
                if header_entry_name in header_data:
                    byte_payload += header_data[header_entry_name]
            
            # Add the full encoded sensor data (not compressed)
            byte_payload += BitArray(enc_data)
            
            # Convert to bytes and transmit
            full_packet = byte_payload.tobytes()
            packet = full_packet.hex()
            handler.queue_binary_transmit(packet)
            handler.process_transmit_queue()
            
            print(f"✅ TTToken transmitted with {len(enc_data)} bytes of sensor data + headers")
            print(f"📊 Total packet size: {len(full_packet)} bytes (headers + sensor data)")
            
        except Exception as header_error:
            print(f"⚠️ Header preservation failed, falling back to direct transmission: {header_error}")
            # Fallback: transmit just the sensor data directly
            token_bytes = enc_data
            packet = token_bytes.hex()
            handler.queue_binary_transmit(packet)
            handler.process_transmit_queue()
            print(f"✅ TTToken transmitted with {len(enc_data)} bytes of sensor data (fallback)")
            
    except Exception as e:
        print(f"⚠️ Failed to transmit TTToken with sensor data: {e}")

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
    
    # Update sensor tracker with transmission results
    if sensor_tracker:
        try:
            # Call the function directly to avoid scope issues
            if not sensor_tracker:
                return bitmap
            
            current_time = 'now'  # In real implementation, use datetime.now()
            
            # Update transmission history
            sensor_tracker['last_transmission'] = current_time
            
            # Record which sensors were transmitted
            if transmission_result and 'transmitted_sensors' in transmission_result:
                for sensor_name in transmission_result['transmitted_sensors']:
                    if sensor_name not in sensor_tracker['transmission_history']:
                        sensor_tracker['transmission_history'][sensor_name] = []
                    
                    sensor_tracker['transmission_history'][sensor_name].append({
                        'timestamp': current_time,
                        'value': data.get(sensor_name, 'unknown'),
                        'change_percent': transmission_result.get('change_percent', {}).get(sensor_name, 0)
                    })
            
            print(f"✅ Sensor tracker updated with transmission results")
            
        except Exception as e:
            print(f"⚠️ Failed to update sensor tracker: {e}")
    
    return bitmap

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

# get_wittypi_status moved to tools/wittypi_control.py

@SQify
def get_wittypi_schedule_config():
    """
    Get WittyPi schedule configuration from runtime parameters
    """
    try:
        from tools.lora_runtime_integration import get_parameter
        
        # Get schedule parameters with sensible defaults
        start_hour = get_parameter('wittypi_start_hour', 8)
        start_minute = get_parameter('wittypi_start_minute', 0)
        interval_length_minutes = get_parameter('wittypi_interval_minutes', 30)
        num_repetitions_per_day = get_parameter('wittypi_repetitions_per_day', 8)
        
        return {
            'start_hour': start_hour,
            'start_minute': start_minute,
            'interval_length_minutes': interval_length_minutes,
            'num_repetitions_per_day': num_repetitions_per_day
        }
        
    except Exception as e:
        # Return default values if runtime parameters fail
        return {
            'start_hour': 8,
            'start_minute': 0,
            'interval_length_minutes': 30,
            'num_repetitions_per_day': 8
        }

@SQify
def manual_wittypi_control(action, **kwargs):
    """
    Manual WittyPi control for testing and direct control
    Actions: 'clear_schedule', 'set_schedule', 'get_status', 'sync_time'
    """
    try:
        from tools.wittypi_control import clear_shutdown_time, set_schedule, get_data, sync_time
        
        if action == 'clear_schedule':
            clear_shutdown_time()
            return {
                'status': 'schedule_cleared',
                'action': 'clear_schedule',
                'message': 'WittyPi shutdown schedule cleared'
            }
            
        elif action == 'set_schedule':
            # Extract schedule parameters
            start_hour = kwargs.get('start_hour', 8)
            start_minute = kwargs.get('start_minute', 0)
            interval_length_minutes = kwargs.get('interval_length_minutes', 30)
            num_repetitions_per_day = kwargs.get('num_repetitions_per_day', 8)
            
            next_startup_time = set_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day)
            
            return {
                'status': 'schedule_set',
                'action': 'set_schedule',
                'next_startup': next_startup_time,
                'schedule': {
                    'start_hour': start_hour,
                    'start_minute': start_minute,
                    'interval_minutes': interval_length_minutes,
                    'repetitions_per_day': num_repetitions_per_day
                },
                'message': f'Schedule set, next startup: {next_startup_time}'
            }
            
        elif action == 'get_status':
            temperature, battery_voltage, internal_voltage = get_data()
            return {
                'status': 'data_retrieved',
                'action': 'get_status',
                'temperature': temperature,
                'battery_voltage': battery_voltage,
                'internal_voltage': internal_voltage
            }
            
        elif action == 'sync_time':
            sync_time()
            return {
                'status': 'time_synced',
                'action': 'sync_time',
                'message': 'WittyPi time synchronized with network'
            }
            
        else:
            return {
                'status': 'invalid_action',
                'error': f'Unknown action: {action}',
                'valid_actions': ['clear_schedule', 'set_schedule', 'get_status', 'sync_time']
            }
            
    except ImportError as e:
        return {
            'status': 'wittypi_unavailable',
            'error': f'Import error: {str(e)}',
            'message': 'WittyPi control functions not available'
        }
    except Exception as e:
        return {
            'status': 'control_failed',
            'error': str(e),
            'message': f'Failed to execute action: {action}'
        }

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
        # transmission_enabled and gps_enabled removed (always-on policy)
        
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
    from tools.lora_runtime_integration import get_parameter, set_parameter

    # Get current iteration count from runtime parameters
    try:
        current_count = get_parameter('iteration_count', 0)
        new_count = current_count + 1
        set_parameter('iteration_count', new_count)
        print(f"\n Iteration: {new_count} \n")
    except Exception as e:
        print(f"⚠️ Failed to update iteration count: {e}")
        new_count = 1
    
    # Check if emergency mode is active - if so, ignore shutdown limit
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        if emergency_mode:
            print(f"🚨 EMERGENCY MODE ACTIVE - Ignoring shutdown limit, continuing data collection")
            return "emergency_mode_active"
    except Exception as e:
        print(f"⚠️ Failed to check emergency mode: {e}")
    
    # Use runtime parameter for shutdown limit (only if not in emergency mode)
    try:
        shutdown_limit = get_parameter('shutdown_iteration_limit', 3)
        auto_shutdown_enabled = get_parameter('auto_shutdown_enabled', True)
        
        print(f"🔍 Shutdown check: count={new_count}, limit={shutdown_limit}, enabled={auto_shutdown_enabled}")
        
        if auto_shutdown_enabled and new_count >= shutdown_limit:
            print(f"\n🚨 SHUTDOWN TRIGGERED: {new_count} iterations >= {shutdown_limit} limit\n")
            # using an /etc/doas.conf configured for user pi
            #call("doas /usr/sbin/shutdown", shell=True) # shutdown Pi
            print("🔄 Executing sys.exit('shutdown')...")
            sys.exit("shutdown")
        else:
            print(f"✅ Continuing: count={new_count} < limit={shutdown_limit} or shutdown disabled")
    except Exception as e:
        print(f"⚠️ Failed to check shutdown parameters: {e}")
        # Continue without shutdown
    
    return "continue"

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
    from tools.get_gps import get_location_with_retry
    from tools.lora_runtime_integration import get_parameter

    from pympler import asizeof
    from sys import getsizeof

    # LoRa transmission is always enabled

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
    
    # Always attempt to collect GPS data
    try:
        gps, packet = get_location_with_retry()
        if gps:
            data.update(gps)
            print(f"🔍 GPS data: {gps}")
            print(f"🔍 GPS packet: {packet}")
    except Exception as e:
        print(f"⚠️ Failed to get GPS data: {e}")
    
    # Get WittyPi voltage measurements for battery status
    try:
        from tools.wittypi_control import get_wittypi_status
        wittypi_data = get_wittypi_status()
        if wittypi_data.get('status') == 'wittypi_data_retrieved':
            data.update({
                'wittypi_temperature': wittypi_data.get('temperature', 0.0),
                'wittypi_battery_voltage': wittypi_data.get('battery_voltage', 0.0),
                'wittypi_internal_voltage': wittypi_data.get('internal_voltage', 0.0)
            })
            print(f"🔋 WittyPi data added: temp={wittypi_data.get('temperature', 0.0)}°C, battery={wittypi_data.get('battery_voltage', 0.0)}V, internal={wittypi_data.get('internal_voltage', 0.0)}V")
        else:
            print(f"⚠️ WittyPi data unavailable: {wittypi_data.get('status', 'unknown')}")
    except Exception as e:
        print(f"⚠️ Failed to get WittyPi data: {e}")
    
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

    # Transmit TTToken with full encoded sensor data embedded (preserve headers)
    try:
        enc_data = compressed_encoding(data)
        print(f"🔧 Encoded sensor data: {len(enc_data)} bytes")
        
        # Create TTToken with full encoded sensor data (not compressed)
        token_1 = TTToken(enc_data, time_1, False,
        TTTag(context, sq_name, 4, recipient_device))
        
        # Create LoRa message to preserve headers, but modify it to keep full data
        lora_msg = NetworkInterfaceLoRa.TTLoRaMessage(token_1, recipient_device)
        
        # Instead of calling encode_token() which compresses, we'll manually construct
        # the packet with headers + full sensor data
        try:
            # Get the header information from the LoRa message
            header_data = lora_msg.generate_header_values()
            
            # Combine headers with the full encoded sensor data
            from bitstring import BitArray
            byte_payload = BitArray()
            
            # Add headers (this preserves routing information)
            header_entries = ['sq', 'port', 'context', 'device', 'start_tick', 'stop_tick']
            for header_entry_name in header_entries:
                if header_entry_name in header_data:
                    byte_payload += header_data[header_entry_name]
            
            # Add the full encoded sensor data (not compressed)
            byte_payload += BitArray(enc_data)
            
            # Convert to bytes and transmit
            full_packet = byte_payload.tobytes()
            packet = full_packet.hex()
            handler.queue_binary_transmit(packet)
            handler.process_transmit_queue()
            
            print(f"✅ TTToken transmitted with {len(enc_data)} bytes of sensor data + headers")
            print(f"📊 Total packet size: {len(full_packet)} bytes (headers + sensor data)")
            
        except Exception as header_error:
            print(f"⚠️ Header preservation failed, falling back to direct transmission: {header_error}")
            # Fallback: transmit just the sensor data directly
            token_bytes = enc_data
            packet = token_bytes.hex()
            handler.queue_binary_transmit(packet)
            handler.process_transmit_queue()
            print(f"✅ TTToken transmitted with {len(enc_data)} bytes of sensor data (fallback)")
            
    except Exception as e:
        print(f"⚠️ Failed to transmit TTToken with sensor data: {e}")

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
            # Control WittyPi to clear shutdown schedule
            try:
                wittypi_result = wittypi_emergency_control(True)
                print(f"📋 WittyPi Control: {wittypi_result.get('message', 'Unknown status')}")
            except NameError as e:
                print(f"⚠️ WittyPi emergency control function not available: {e}")
            except Exception as e:
                print(f"⚠️ Failed to control WittyPi during emergency: {e}")
        else:
            print("✅ Emergency mode deactivated via LoRa command")
            # Control WittyPi to restore normal schedule
            try:
                wittypi_result = wittypi_emergency_control(False)
                print(f"📋 WittyPi Control: {wittypi_result.get('message', 'Unknown status')}")
            except NameError as e:
                print(f"⚠️ WittyPi emergency control function not available: {e}")
            except Exception as e:
                print(f"⚠️ Failed to control WittyPi during emergency deactivation: {e}")
    
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
        debug_mode = get_parameter('debug_mode', False)
    except Exception as e:
        print(f"⚠️ Failed to get LoRa parameters: {e}")
        # Use default values
        area_threshold = 10
        monitoring_freq = 60
        emergency_mode = False
        debug_mode = False
    
    # Process any queued transmissions
    if handler:
        handler.process_transmit_queue()
    
    # Log current status if in debug mode
    if debug_mode:
        print(f"📡 LoRa Status ({datetime.now().strftime('%H:%M:%S')}): "
              f"Area={area_threshold}%, Freq={monitoring_freq}min, "
              f"Emergency={emergency_mode}, Transmit=True")
    
    # Return current status for TickTalk integration
    return {
        'area_threshold': area_threshold,
        'monitoring_frequency': monitoring_freq,
        'emergency_mode': emergency_mode,
        'transmission_enabled': True,
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
    Emergency mode workflow with continuous operation and bypassed shutdown
    """
    print("🚨 Emergency mode detected - bypassing shutdown limits, continuing data collection")
    
    # Photos, lepton data, coregistration, segmentation, compression, LoRa transmission, and shutdown check are now passed as parameters from GRAPH level
    
    # LoRa monitoring and parameter management
    from tools.lora_runtime_integration import get_parameter, set_parameter
    
    # Get current parameters directly
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        area_threshold = get_parameter('area_threshold', 10)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        
        # Force emergency mode parameters during emergency
        if emergency_mode:
            # Set faster monitoring frequency for emergency
            set_parameter('monitoring_frequency', 5)  # 5 minutes instead of 60
            set_parameter('emergency_frequency', 1)   # 1 minute for critical monitoring
            print("🚨 Emergency parameters set: Fast monitoring (5min), continuous transmission")
        
        # Return comprehensive status with emergency indicators
        return {
            'status': 'emergency_workflow_completed',
            'dirname': dirname,
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'monitoring_frequency': get_parameter('monitoring_frequency', 5),  # Use updated value
            'emergency_frequency': get_parameter('emergency_frequency', 1),   # Use updated value
            'bitmap_compressed': True,
            'lora_transmitted': True,
            'photos_captured': True,
            'lepton_data_captured': True,
            'shutdown_bypassed': True,
            'emergency_priority': 'high'
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
        
        # Return comprehensive status
        return {
            'status': 'normal_workflow_completed',
            'dirname': dirname,
            'emergency_mode': emergency_mode,
            'area_threshold': area_threshold,
            'monitoring_frequency': monitoring_freq,
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
def emergency_status_monitor(trigger):
    """
    Monitor and report emergency status for system visibility
    """
    from datetime import datetime
    from tools.lora_runtime_integration import get_parameter
    
    try:
        emergency_mode = get_parameter('emergency_mode', False)
        monitoring_freq = get_parameter('monitoring_frequency', 60)
        emergency_freq = get_parameter('emergency_frequency', 5)
        
        if emergency_mode:
            print(f"🚨 EMERGENCY STATUS ({datetime.now().strftime('%H:%M:%S')}):")
            print(f"   - Emergency Mode: ACTIVE")
            print(f"   - Monitoring Frequency: {monitoring_freq} minutes")
            print(f"   - Emergency Frequency: {emergency_freq} minutes")
            print(f"   - Transmission: ENABLED")
            print(f"   - Shutdown: BYPASSED (continuing operation)")
        else:
            print(f"✅ Normal Operation ({datetime.now().strftime('%H:%M:%S')}):")
            print(f"   - Emergency Mode: INACTIVE")
            print(f"   - Monitoring Frequency: {monitoring_freq} minutes")
            print(f"   - Transmission: ENABLED")
        
        return {
            'emergency_mode': emergency_mode,
            'monitoring_frequency': monitoring_freq,
            'emergency_frequency': emergency_freq,
            'timestamp': datetime.now().isoformat(),
            'status': 'emergency_active' if emergency_mode else 'normal_operation'
        }
    except Exception as e:
        print(f"⚠️ Failed to monitor emergency status: {e}")
        return {'error': str(e), 'timestamp': datetime.now().isoformat()}

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
            'debug_mode': get_parameter('debug_mode', False),
            
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
            'debug_mode': False,
            
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
              f"Transmit=True")
    
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
    # transmission_enabled removed (always-on policy)
    
    # Monitor for debug mode
    if current_config.get('debug_mode', False):
        changes.append('debug_mode_enabled')
    
    # Apply configuration changes to system behavior
    if changes:
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
            return {'status': 'error', 'error': str(e)}
    
    return {'status': 'no_changes', 'config': current_config}

@SQify
def emergency_mode_logger(trigger, emergency_mode, shutdown_bypassed):
    """
    Log emergency mode status for system visibility
    This function handles logging that cannot be done in GRAPH functions
    """
    if emergency_mode and shutdown_bypassed:
        # This function can be called from the main workflow to log emergency status
        return {
            'status': 'emergency_mode_logged',
            'emergency_mode': True,
            'shutdown_bypassed': True,
            'message': 'EMERGENCY MODE: Continuing data collection and transmission'
        }
    return {
        'status': 'normal_operation',
        'emergency_mode': emergency_mode,
        'shutdown_bypassed': shutdown_bypassed
    }

@SQify
def get_emergency_mode_status(trigger):
    """
    Get emergency mode status - wrapper for get_parameter that can be called from GRAPH functions
    """
    from tools.lora_runtime_integration import get_parameter
    emergency_mode = get_parameter('emergency_mode', False)
    return {
        'emergency_mode': emergency_mode,
        'status': 'emergency_active' if emergency_mode else 'normal_operation'
    }


@SQify
def check_lora_availability(trigger):
    """
    Check if LoRa functionality is available
    """
    try:
        from tools.lora_runtime_integration import get_lora_runtime_integration
        runtime = get_lora_runtime_integration()
        return runtime.is_lora_available()
    except Exception as e:
        return False

@SQify
def handle_emergency_mode_logic(trigger, emergency_status, shutdown_result):
    """
    Handle emergency mode logic - consolidated wrapper to avoid dependency issues
    """
    # Extract emergency mode status
    if isinstance(emergency_status, dict):
        emergency_mode = emergency_status.get('emergency_mode', False)
    else:
        emergency_mode = False
    
    # Extract shutdown bypassed status
    if isinstance(shutdown_result, dict):
        shutdown_bypassed = shutdown_result.get('shutdown_bypassed', False)
    else:
        shutdown_bypassed = False
    
    # Assume LoRa is available for now to avoid dependency issues
    # In a real implementation, this would check LoRa availability
    lora_available = True
    
    if emergency_mode and shutdown_bypassed:
        # Emergency mode activated - LoRa transmission is always enabled
        try:
            # Simple logging without external function dependency
            print(f"🚨 EMERGENCY MODE LOG: emergency_mode={emergency_mode}, shutdown_bypassed={shutdown_bypassed}")
            
            return {
                'emergency_mode': True,
                'shutdown_bypassed': True,
                'action_taken': 'emergency_mode_activated',
                'lora_available': True
            }
        except Exception as e:
            # If LoRa integration fails, still activate emergency mode
            print(f"⚠️ EMERGENCY MODE LOG (LoRa failed): emergency_mode={emergency_mode}, shutdown_bypassed={shutdown_bypassed}, error={e}")
            
            return {
                'emergency_mode': True,
                'shutdown_bypassed': True,
                'action_taken': 'emergency_mode_activated_no_lora',
                'lora_available': False,
                'error': str(e)
            }
    else:
        print(f"ℹ️ EMERGENCY MODE LOG: emergency_mode={emergency_mode}, shutdown_bypassed={shutdown_bypassed}")
        return {
            'emergency_mode': emergency_mode,
            'shutdown_bypassed': shutdown_bypassed,
            'action_taken': 'no_action_needed',
            'lora_available': lora_available
        }

@SQify
def process_workflow_by_emergency_mode(trigger, emergency_logic_result, workflow_data):
    """
    Process workflow based on emergency mode - wrapper to avoid conditional statements in GRAPH functions
    """
    # Extract emergency mode status
    emergency_mode = emergency_logic_result.get('emergency_mode', False)
    
    if emergency_mode:
        # Emergency mode - return emergency status
        print("🚨 Emergency workflow placeholder - emergency mode active")
        return {
            'status': 'emergency_workflow_placeholder',
            'emergency_mode': True,
            'message': 'Emergency mode active - continuing data collection',
            'workflow_data': workflow_data,
            'timestamp': 'now'
        }
    else:
        # Normal mode - return normal status
        print("ℹ️ Normal workflow placeholder - normal operation")
        return {
            'status': 'normal_workflow_placeholder',
            'emergency_mode': False,
            'message': 'Normal operation - standard workflow',
            'workflow_data': workflow_data,
            'timestamp': 'now'
        }

@SQify
def get_sensor_tracking_stats(tracker):
    """
    Get statistics about sensor tracking and transmission patterns
    """
    if not tracker:
        return {'error': 'No tracker available'}
    
    try:
        stats = {
            'total_sensors_tracked': len(tracker['previous_values']),
            'sensors_with_history': len(tracker['transmission_history']),
            'last_transmission': tracker['last_transmission'],
            'change_threshold': tracker['change_threshold'] * 100,  # Convert to percentage
            'sensor_details': {}
        }
        
        # Add details for each tracked sensor
        for sensor_name in tracker['previous_values']:
            current_value = tracker['previous_values'][sensor_name]
            transmission_count = len(tracker['transmission_history'].get(sensor_name, []))
            
            stats['sensor_details'][sensor_name] = {
                'current_value': current_value,
                'transmission_count': transmission_count,
                'last_transmitted': tracker['transmission_history'].get(sensor_name, [{}])[-1].get('timestamp') if tracker['transmission_history'].get(sensor_name) else None
            }
        
        return stats
        
    except Exception as e:
        print(f"⚠️ Failed to get sensor tracking stats: {e}")
        return {'error': str(e)}

@SQify
def reset_sensor_tracker(tracker):
    """
    Reset sensor tracker to clear all previous values and history
    Useful for system restart or manual reset
    """
    if not tracker:
        return {'error': 'No tracker available'}
    
    try:
        tracker['previous_values'] = {}
        tracker['transmission_history'] = {}
        tracker['last_transmission'] = None
        
        print("✅ Sensor tracker reset - all previous values cleared")
        return {'status': 'reset_successful'}
        
    except Exception as e:
        print(f"⚠️ Failed to reset sensor tracker: {e}")
        return {'error': str(e)}

@SQify
def log_sensor_tracking_stats(sensor_tracker):
    """
    Log sensor tracking statistics - wrapper to avoid print() in GRAPH functions
    """
    if not sensor_tracker:
        return {'status': 'no_tracker', 'message': 'No sensor tracker available'}
    
    try:
        # Define the logic inline to avoid dependency on get_sensor_tracking_stats function
        if not sensor_tracker:
            tracking_stats = {'error': 'No tracker available'}
        else:
            try:
                stats = {
                    'total_sensors_tracked': len(sensor_tracker.get('previous_values', {})),
                    'sensors_with_history': len(sensor_tracker.get('transmission_history', {})),
                    'last_transmission': sensor_tracker.get('last_transmission'),
                    'change_threshold': sensor_tracker.get('change_threshold', 0.05) * 100,  # Convert to percentage
                    'sensor_details': {}
                }
                
                # Add details for each tracked sensor
                for sensor_name in sensor_tracker.get('previous_values', {}):
                    current_value = sensor_tracker['previous_values'][sensor_name]
                    transmission_count = len(sensor_tracker.get('transmission_history', {}).get(sensor_name, []))
                    
                    stats['sensor_details'][sensor_name] = {
                        'current_value': current_value,
                        'transmission_count': transmission_count,
                        'last_transmitted': sensor_tracker.get('transmission_history', {}).get(sensor_name, [{}])[-1].get('timestamp') if sensor_tracker.get('transmission_history', {}).get(sensor_name) else None
                    }
                
                tracking_stats = stats
                
            except Exception as e:
                print(f"⚠️ Failed to get sensor tracking stats: {e}")
                tracking_stats = {'error': str(e)}
        
        total_sensors = tracking_stats.get('total_sensors_tracked', 0)
        print(f"📊 Sensor tracking stats: {total_sensors} sensors tracked")
        
        return {
            'status': 'logged_successfully',
            'total_sensors': total_sensors,
            'stats': tracking_stats
        }
    except Exception as e:
        print(f"⚠️ Failed to log sensor tracking stats: {e}")
        return {'status': 'error', 'error': str(e)}

@SQify
def ip_uplink_transmit(bitmap, _sensor_tracker):
    """Send a subset of sensor readings and the flood bitmap to the FastAPI server over IP.

    Encodes the following channels as channel-coded hex blocks and POSTs to
    /ip/uplink: device_ts, battery_pct (from WittyPi), GPS lat/lon, temperature,
    humidity, flood_detect (inferred from bitmap), flood_bitmap, and the five
    status-report parameters.  IMU data is not included.

    Disabled by default — set ip_upload.enabled=true in runtime_config.json to
    activate.  Runs after the LoRa path in the wake cycle; both paths share the
    same sensor snapshot but neither affects the other's outcome.

    Returns a status dict (never raises) so a failure here never stops the main
    workflow.
    """
    import struct
    import time as _time
    from tools.transmit_ip import IPTransmitter
    from tools.lora_runtime_integration import get_parameter

    tx = IPTransmitter()

    if not tx.enabled:
        print("📡 IP uplink disabled (ip_upload.enabled=false) — skipping")
        tx.close()
        return {"status": "disabled", "success": False}

    # Use a short timeout for the reachability probe — this runs on every wake
    # cycle and a full timeout_s (default 15 s) would add significant dead time
    # when the server is down.
    if not tx.is_reachable(timeout_s=5):
        print(f"⚠️ IP uplink: server unreachable at {tx.server_url}")
        tx.close()
        return {"status": "unreachable", "success": False}

    try:
        # ── Collect sensor data ────────────────────────────────────────────────
        # IMU orientation is not transmitted (no 03 01 channel encoded below);
        # do not read it here to avoid unnecessary hardware I/O and failure points.
        data = {}

        try:
            from tools.aht20_temperature import get_aht20
            data.update(get_aht20())
        except Exception as e:
            print(f"⚠️ IP uplink: AHT20 unavailable: {e}")

        try:
            from tools.get_gps import get_location_with_retry
            gps, _ = get_location_with_retry()
            if gps:
                data.update(gps)
        except Exception as e:
            print(f"⚠️ IP uplink: GPS unavailable: {e}")

        try:
            from tools.wittypi_control import get_wittypi_status
            wittypi_data = get_wittypi_status()
            if wittypi_data.get('status') == 'wittypi_data_retrieved':
                data['wittypi_battery_voltage'] = wittypi_data.get('battery_voltage', 0.0)
        except Exception as e:
            print(f"⚠️ IP uplink: WittyPi unavailable: {e}")

        data['area_threshold']                    = get_parameter('area_threshold', 10)
        data['stage_threshold']                   = get_parameter('stage_threshold', 50)
        data['monitoring_frequency']              = get_parameter('monitoring_frequency', 60)
        data['emergency_frequency']               = get_parameter('emergency_frequency', 5)
        data['neighborhood_emergency_frequency']  = get_parameter('neighborhood_emergency_frequency', 30)

        # ── Build channel list ─────────────────────────────────────────────────
        channels = []
        ts_now = int(_time.time())

        # 00 01 — device timestamp (8-byte uint64)
        channels.append({"code": "00 01", "payload_hex": struct.pack(">Q", ts_now).hex()})

        # 02 01 — battery percent derived from WittyPi voltage (LiPo: 3.0V=0%, 4.2V=100%)
        batt_v = data.get('wittypi_battery_voltage', 0.0)
        if batt_v and batt_v > 0:
            batt_pct = max(0, min(100, int((batt_v - 3.0) / 1.2 * 100)))
            channels.append({"code": "02 01", "payload_hex": struct.pack(">I", batt_pct).hex()})

        # 04 01 — GPS block (lat int32 microdeg, lon int32 microdeg)
        lat = data.get('gps_lat')
        lon = data.get('gps_lon')
        if lat is not None and lon is not None:
            try:
                channels.append({
                    "code": "04 01",
                    "payload_hex": struct.pack(">ii",
                        int(round(lat * 1_000_000)),
                        int(round(lon * 1_000_000)),
                    ).hex(),
                })
            except struct.error as e:
                print(f"⚠️ IP uplink: GPS encoding error (lat={lat}, lon={lon}): {e}")

        # 05 01 — temperature (int16, value × 100)
        temp = data.get('temperature_celsius')
        if temp is not None:
            try:
                channels.append({"code": "05 01",
                                  "payload_hex": struct.pack(">h", int(round(temp * 100))).hex()})
            except struct.error as e:
                print(f"⚠️ IP uplink: temperature encoding error (temp={temp}): {e}")

        # 06 01 — humidity (uint8, 0–100 %)
        hum = data.get('relative_humidity')
        if hum is not None:
            channels.append({"code": "06 01",
                             "payload_hex": struct.pack(">B", max(0, min(100, int(round(hum))))).hex()})

        # 07 17 — flood detect (inferred from bitmap content)
        flood_detected = bool(bitmap and any(b != 0 for b in bitmap))
        channels.append({"code": "07 17",
                         "payload_hex": struct.pack(">I", int(flood_detected)).hex()})

        # 08 18 — flood bitmap (variable length, only if non-empty)
        if bitmap:
            channels.append({"code": "08 18", "payload_hex": bytes(bitmap).hex()})

        # 09 xx — status reports; clamped to uint32 range so struct.pack never raises
        _u32 = lambda v: max(0, min(0xFFFFFFFF, int(v)))
        channels.append({"code": "09 19",
                         "payload_hex": struct.pack(">I", _u32(data['area_threshold'])).hex()})
        channels.append({"code": "09 29",
                         "payload_hex": struct.pack(">I", _u32(data['stage_threshold'])).hex()})
        channels.append({"code": "09 39",
                         "payload_hex": struct.pack(">I", _u32(data['monitoring_frequency'])).hex()})
        channels.append({"code": "09 49",
                         "payload_hex": struct.pack(">I", _u32(data['emergency_frequency'])).hex()})
        channels.append({"code": "09 59",
                         "payload_hex": struct.pack(">I",
                             _u32(data['neighborhood_emergency_frequency'])).hex()})

        # ── Transmit ──────────────────────────────────────────────────────────
        result = tx.send_uplink(channels, device_ts=ts_now)

        if result["success"]:
            print(f"✅ IP uplink OK: {len(channels)} channels sent "
                  f"(attempt {result.get('attempts', '?')})")
        else:
            print(f"⚠️ IP uplink failed after {result.get('attempts', '?')} attempt(s): "
                  f"{result.get('error', 'unknown error')}")
            if tx.fallback_to_lora:
                print("📡 LoRa fallback is enabled — data may also be sent via the LoRa path")

        return {
            "status": "ok" if result["success"] else "failed",
            "channels_sent": len(channels),
            "result": result,
        }
    except Exception as exc:
        print(f"⚠️ IP uplink: unexpected error: {exc}")
        return {"status": "error", "success": False, "error": str(exc)}
    finally:
        tx.close()


@SQify
def ip_downlink_poll_and_apply(_lora_init):
    """Poll /ip/downlink/{device_id} for queued server commands and apply them.

    The server queues parameter-update commands when a human operator (or weather
    automation) sends a downlink via the dashboard.  This function retrieves the
    oldest pending command, decodes the parts list, and applies each recognised
    parameter change to the runtime config via set_parameter().

    Runs once at the start of each wake cycle (after LoRa init) so the device
    picks up any commands that arrived while it was sleeping.

    Disabled by default — requires ip_upload.enabled=true in runtime_config.json.
    """
    from tools.transmit_ip import IPTransmitter, apply_downlink_command
    from tools.lora_runtime_integration import set_parameter

    tx = IPTransmitter()

    if not tx.enabled:
        tx.close()
        return {"status": "disabled"}

    if not tx.is_reachable(timeout_s=2):
        tx.close()
        print("⚠️ IP downlink poll skipped: server unreachable")
        return {"status": "unreachable"}

    poll_result = tx.poll_downlink()
    tx.close()

    if not poll_result["success"]:
        print(f"⚠️ IP downlink poll failed: {poll_result.get('error')}")
        return {"status": "poll_failed", "error": poll_result.get("error")}

    cmd = poll_result.get("command")
    if not cmd:
        print("📬 IP downlink: no pending commands")
        return {"status": "no_command"}

    print(f"📬 IP downlink: received command (queue_id={cmd.get('queue_id')})")

    dispatch = apply_downlink_command(cmd, set_param_fn=set_parameter)

    applied = dispatch["applied"]
    if applied:
        print(f"✅ IP downlink: applied {len(applied)} parameter(s): {', '.join(applied)}")
    else:
        print(f"⚠️ IP downlink: no recognised parameters in command "
              f"(hex={cmd.get('hex_payload', '')})")

    return {
        "status": "applied",
        "queue_id": dispatch["queue_id"],
        "applied_params": applied,
        "hex_payload": cmd.get("hex_payload"),
    }


@GRAPHify
def ttmain(trigger):
    # Import TTClock for the main workflow
    from ticktalkpython.Clock import TTClock
    
    with TTClock.root() as root_clock:
        
        # Initialize LoRa integration first
        lora_init = initialize_lora_integration(trigger)

        # Poll for queued server commands over IP and apply them before the
        # capture cycle begins, so any threshold/frequency changes take effect
        # this iteration.  No-ops silently when ip_upload.enabled=false.
        ip_downlink = ip_downlink_poll_and_apply(lora_init)

        # Validate configuration
        config_validation = validate_configuration(trigger)
        
        # Get adaptive monitoring parameters
        monitoring_params = adaptive_monitoring()
        
        # Call get_time as STREAM function to get directory name
        token, dirname = get_time(trigger, TTClock=root_clock, TTPeriod=60_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000)
        
        # Take photos and capture lepton data at GRAPH level
        from tt_take_photos import flir, take_two_photos
        photo = take_two_photos(trigger, dirname)
        deadline_time = READ_TTCLOCK(token, TTClock=root_clock) + 5_000_000
        
        lepton_file = flir(dirname)
        lepton = TTFinishByOtherwise(lepton_file, TTTimeDeadline=deadline_time, TTPlanB=TTSingleRunTimeout(flir_planb(token), TTTimeout=3_000_000), TTWillContinue=False) 
        
        #lepton_file = TTFinishByOtherwise(
        #    flir(dirname, TTClock=root_clock, TTPeriod=60_000_000, TTPhase=0, TTDataIntervalWidth=1_000_000),
        #    TTTimeDeadline=deadline_time,
        #    TTPlanB=TTSingleRunTimeout(flir_planb(token), TTTimeout=10_000_000),
        #    TTWillContinue=True
        #)
        
        # Coregistration and segmentation at GRAPH level
        coreg_state = coregistration(dirname, lepton_file, photo)
        seg_result = segformer(dirname, coreg_state)
        
        # Compression and LoRa transmission at GRAPH level
        bitmap = compress_bitmap(seg_result)
        
        # Create sensor tracker for monitoring value changes
        sensor_tracker = create_sensor_tracker()
        
        # Use sensor tracker for intelligent LoRa transmission
        lora_return = lora_token_with_tracker(bitmap, sensor_tracker)

        # IP uplink — sends the same sensor snapshot + bitmap to the FastAPI
        # server over WiFi/cellular.  Runs sequentially after lora_return.
        # No-ops silently when disabled; a failure here does not affect LoRa.
        ip_return = ip_uplink_transmit(bitmap, sensor_tracker)

        # Shutdown check at GRAPH level
        shutdown_result = call_shutdown(lora_return)
        
        # Create workflow data structure
        workflow_data = create_workflow_data(monitoring_params, dirname, photo, lepton_file, coreg_state, seg_result, bitmap, lora_return, shutdown_result)
        
        # Check if emergency mode is active and bypass shutdown if needed
        emergency_status = get_emergency_mode_status(trigger)
        emergency_logic_result = handle_emergency_mode_logic(trigger, emergency_status, shutdown_result)
        
        # Process the data based on emergency mode
        result = process_workflow_by_emergency_mode(trigger, emergency_logic_result, workflow_data)
        
        # Get sensor tracking statistics for monitoring
        log_result = log_sensor_tracking_stats(sensor_tracker)
        
        # Ensure constant LoRa monitoring for incoming messages
        lora_status = lora_listener(TTPersistent=True)
        
        return result

# Test function removed - not needed for main application
