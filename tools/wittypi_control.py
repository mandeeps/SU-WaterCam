#! /usr/bin/python3

from witty_pi_4 import WittyPi4

witty_pi_4 = WittyPi4()

def sync_time():
    witty_pi_4.sync_time_with_network()

def get_data():
    temperature = witty_pi_4.get_temperature()
    battery_voltage = witty_pi_4.get_battery_voltage()
    internal_voltage = witty_pi_4.get_internal_voltage()
    internal_current = witty_pi_4.get_internal_current()
    return temperature, battery_voltage, internal_voltage, internal_current

def set_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day):
    witty_pi_4.generate_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day)
    next_startup_time = witty_pi_4.apply_schedule()
    return next_startup_time

def clear_shutdown_time():
    witty_pi_4.clear_shutdown_time()

from ticktalkpython.SQ import SQify
from datetime import datetime, timezone

@SQify
def get_wittypi_status():
    """
    Get current WittyPi status including temperature, battery voltage, and internal voltage
    """
    try:
        temperature, battery_voltage, internal_voltage, internal_current = get_data()

        return {
            'status': 'wittypi_data_retrieved',
            'temperature': temperature,
            'battery_voltage': battery_voltage,
            'internal_voltage': internal_voltage,
            'internal_current': internal_current,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
    except ImportError as e:
        return {
            'status': 'wittypi_unavailable',
            'error': f'Import error: {str(e)}',
            'message': 'WittyPi control functions not available'
        }
    except Exception as e:
        return {
            'status': 'wittypi_error',
            'error': str(e),
            'message': 'Failed to get WittyPi status'
        }

if __name__ == "__main__":
    # read optional command line arguments to set schedule if provided  
    import sys
    if len(sys.argv) > 1:
        set_schedule(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        print("No arguments provided")
        print("Usage: python wittypi_control.py <start_hour> <start_minute> <interval_length_minutes> <num_repetitions_per_day>")
        print("Example: python wittypi_control.py 8 0 30 8")
        print("This will set the schedule to start at 8:00, repeat every 30 minutes for 8 times per day")
        print("The schedule will be applied and the next startup time will be printed")
