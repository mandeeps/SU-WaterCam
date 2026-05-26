#! /usr/bin/python3

# witty_pi_4 may live in tools/ (not on sys.path when imported as a package)
# or in the system/venv site-packages; try both before giving up.
try:
    from witty_pi_4 import WittyPi4 as _WittyPi4Class
except ImportError:
    try:
        from tools.witty_pi_4 import WittyPi4 as _WittyPi4Class
    except ImportError:
        _WittyPi4Class = None

witty_pi_4 = _WittyPi4Class() if _WittyPi4Class is not None else None


def _require_wittypi():
    if witty_pi_4 is None:
        raise ImportError("witty_pi_4 not available")


def sync_time():
    _require_wittypi()
    witty_pi_4.sync_time_with_network()

def get_data():
    _require_wittypi()
    temperature = witty_pi_4.get_temperature()
    battery_voltage = witty_pi_4.get_battery_voltage()
    internal_voltage = witty_pi_4.get_internal_voltage()
    internal_current = witty_pi_4.get_internal_current()
    return temperature, battery_voltage, internal_voltage, internal_current

def set_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day):
    _require_wittypi()
    witty_pi_4.generate_schedule(start_hour, start_minute, interval_length_minutes, num_repetitions_per_day)
    next_startup_time = witty_pi_4.apply_schedule()
    return next_startup_time

def clear_shutdown_time():
    _require_wittypi()
    witty_pi_4.clear_shutdown_time()

from ticktalkpython.SQ import SQify
from datetime import datetime, timezone

@SQify
def get_wittypi_status():
    """
    Get current WittyPi status including temperature, battery voltage,
    internal voltage, and internal current.
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
