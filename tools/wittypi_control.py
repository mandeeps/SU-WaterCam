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

def _alarm_parts(epoch: float):
    import time
    t = time.localtime(epoch)
    return t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec


def set_shutdown_at(epoch: float) -> bool:
    """Arm the Witty Pi's shutdown alarm for a moment less than 28 days ahead
    (the alarm holds only day-of-month and time)."""
    _require_wittypi()
    return witty_pi_4.set_shutdown_time(*_alarm_parts(epoch))


def set_startup_at(epoch: float) -> bool:
    """Arm the Witty Pi's startup alarm, like set_shutdown_at."""
    _require_wittypi()
    return witty_pi_4.set_startup_time(*_alarm_parts(epoch))

# Emergency mode keeps the unit on, so the shutdown alarm would otherwise be cleared.
# Its software end (emergency_max_hours, checked each iteration in ticktalk_main's
# call_shutdown) needs the graph to keep running; this hardware backstop powers the
# unit down this long after that end if the run hangs instead (#209). A normal end
# re-arms the deployed schedule, which replaces it. The startup alarm is set for
# shortly after, so a unit powered down this way comes back by itself (its
# schedule's next start passed during emergency mode, and the mDot's power
# watchdog may be off) and, finding emergency mode expired, restores the schedule.
EMERGENCY_BACKSTOP_MARGIN_S = 3600
EMERGENCY_BACKSTOP_RESTART_S = 600
_backstop_armed_for = {'deadline': None}


def emergency_backstop_deadline(since, max_hours, now=None):
    """When the backstop should fire: the emergency's end plus the margin, or None
    when emergency mode never expires (max_hours 0). A start time from an earlier,
    already ended emergency (or none) counts from now, so it is never in the past."""
    import time
    now = time.time() if now is None else float(now)
    try:
        hours = float(max_hours)
    except (TypeError, ValueError):
        hours = 24.0
    if hours <= 0:
        return None
    try:
        start = float(since)
    except (TypeError, ValueError):
        start = now
    if start + hours * 3600 <= now or start > now:
        start = now
    return start + hours * 3600 + EMERGENCY_BACKSTOP_MARGIN_S


def arm_emergency_backstop(since=None, max_hours=None):
    """Set the shutdown alarm for the current emergency, and the startup alarm
    shortly after it (or, for an emergency that never expires, clear the shutdown). Reads emergency_since / emergency_max_hours when not
    given. Writes the alarm only when the deadline changes. Returns the deadline."""
    if since is None or max_hours is None:
        from tools.lora_runtime_integration import get_parameter
        since = get_parameter('emergency_since', None) if since is None else since
        max_hours = get_parameter('emergency_max_hours', 24) if max_hours is None else max_hours
    deadline = emergency_backstop_deadline(since, max_hours)
    if deadline is None:
        if _backstop_armed_for['deadline'] is not False:
            clear_shutdown_time()
            _backstop_armed_for['deadline'] = False
        return None
    # within a minute counts as unchanged (each iteration recomputes it)
    last = _backstop_armed_for['deadline']
    if not (isinstance(last, float) and abs(last - deadline) < 60):
        if set_shutdown_at(deadline) and set_startup_at(deadline + EMERGENCY_BACKSTOP_RESTART_S):
            _backstop_armed_for['deadline'] = deadline
    return deadline


def installed_schedule_exists():
    """True if a schedule.wpi is installed in the Witty Pi folder."""
    import os
    try:
        from tools.witty_pi_4 import SCHEDULE_FILE_PATH
    except ImportError:
        from witty_pi_4 import SCHEDULE_FILE_PATH
    return os.path.exists(SCHEDULE_FILE_PATH)

def install_fallback_schedule():
    """Put back a schedule.wpi when none is installed (witty_pi_4.fallback_schedule_sources():
    the backed-up deployment schedule, else the repo's daylight preset). Returns the
    source path, or None."""
    try:
        from tools.witty_pi_4 import install_fallback_schedule as install
    except ImportError:
        from witty_pi_4 import install_fallback_schedule as install
    return install()

def rearm_schedule():
    """Re-arm the Witty Pi's alarms from the installed schedule.wpi (runScript.sh),
    without rewriting the file. Returns the next startup time, or "-"."""
    _require_wittypi()
    return witty_pi_4.apply_schedule()

def apply_emergency_schedule(emergency_mode: bool) -> dict:
    """Clear or restore the WittyPi hardware shutdown schedule for emergency mode.

    Emergency ON: move the shutdown alarm to an hour after emergency mode should
    end (arm_emergency_backstop), so the device stays on but a hung run is still
    powered down; with no time limit, clear it. Emergency OFF: re-arm the installed
    schedule.wpi. If none is installed, install the backed-up deployment
    schedule or the repo's daylight preset (install_fallback_schedule()), and
    only if that fails generate one from runtime_config.json.

    Plain-bool, not @SQify-decorated, so it can be called directly from any
    process (this repo's ticktalk_main.py graph node, or the LoRa daemon's
    own emergency-mode callback) without going through TickTalk's token
    machinery -- see docs/LORA_HANDLER_MULTIPROCESS_ISSUE.md for why the
    daemon needs to call this directly, in-process, for instant reactivity.
    """
    try:
        if emergency_mode:
            # The schedule's shutdown would end emergency mode: replace it with the
            # backstop, which fires only if the run hangs past emergency mode's end
            deadline = arm_emergency_backstop()
            if deadline is None:
                print("🚨 EMERGENCY MODE: no time limit, WittyPi shutdown schedule cleared")
                return {
                    'status': 'wittypi_emergency_activated',
                    'action': 'shutdown_schedule_cleared',
                    'message': 'WittyPi shutdown schedule cleared for emergency mode (no time limit)'
                }
            import time
            when = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(deadline))
            print(f"🚨 EMERGENCY MODE: WittyPi shutdown alarm moved to {when} (backstop)")
            return {
                'status': 'wittypi_emergency_activated',
                'action': 'shutdown_backstop_set',
                'backstop': when,
                'message': f'WittyPi shutdown moved to {when}, an hour after emergency mode ends, in case the run hangs'
            }
        else:
            _backstop_armed_for['deadline'] = None
            # Re-arm the schedule the unit was deployed with. This used to
            # regenerate schedule.wpi from the wittypi_* config values (default
            # 08:00, eight 30-minute slots), silently replacing an installed
            # schedule such as config/wittypi/watercam_daylight_2h.wpi.
            if installed_schedule_exists():
                print("✅ EMERGENCY CLEARED: Re-arming the installed WittyPi schedule")
                next_startup_time = rearm_schedule()
                return {
                    'status': 'wittypi_normal_schedule_restored',
                    'action': 'schedule_rearmed',
                    'next_startup': next_startup_time,
                    'message': f'WittyPi schedule re-armed, next startup: {next_startup_time}'
                }

            # No schedule installed: without one the unit would never wake
            # again. Put back the deployment schedule (a backup of it, else the
            # repo's daylight preset), and generate one from config only if none
            # of those can be installed.
            try:
                source = install_fallback_schedule()
            except Exception as e:
                print(f"⚠️ Could not install a fallback schedule: {e}")
                source = None
            if source:
                print(f"✅ EMERGENCY CLEARED: no schedule.wpi installed, installed {source}")
                next_startup_time = rearm_schedule()
                return {
                    'status': 'wittypi_normal_schedule_restored',
                    'action': 'schedule_installed',
                    'source': source,
                    'next_startup': next_startup_time,
                    'message': f'No schedule installed; installed {source}, next startup: {next_startup_time}'
                }

            # Read the parameters via a local import -- module globals are not
            # available when TTPython executes compiled pickles.
            print("✅ EMERGENCY CLEARED: no schedule.wpi and no fallback, generating one")
            from tools.lora_runtime_integration import get_parameter
            start_hour = get_parameter('wittypi_start_hour', 8)
            start_minute = get_parameter('wittypi_start_minute', 0)
            interval_length_minutes = get_parameter('wittypi_interval_minutes', 30)
            num_repetitions_per_day = get_parameter('wittypi_repetitions_per_day', 8)

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
                'message': f'No schedule installed; generated one, next startup: {next_startup_time}'
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

from datetime import datetime, timezone

def get_wittypi_status():
    """
    Get current WittyPi status including temperature, battery voltage,
    internal voltage, and internal current.

    A reading the board didn't give is None (tools/witty_pi_4.py), never a
    made-up value; callers skip it. If none came back the status is
    'wittypi_read_failed', not 'wittypi_data_retrieved'.
    """
    try:
        temperature, battery_voltage, internal_voltage, internal_current = get_data()

        if all(v is None for v in (temperature, battery_voltage, internal_voltage, internal_current)):
            return {
                'status': 'wittypi_read_failed',
                'message': 'No Witty Pi reading could be read',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }

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
