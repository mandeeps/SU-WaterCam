### https://github.com/Eagleshot/WittyPi4Python
### credit https://github.com/Eagleshot
# minor edit to add network check before syncing time

'''A python module for interacting with the Witty Pi 4 board'''
from typing import Optional
from subprocess import check_output, STDOUT
from os import environ, path
import glob
import logging
import os
import tempfile

WITTYPI_DIRECTORY = "/home/pi/wittypi"
SCHEDULE_FILE_PATH = f"{WITTYPI_DIRECTORY}/schedule.wpi"
_REPO = path.dirname(path.dirname(path.abspath(__file__)))
# The preset installed by the deployment guide (docs/FIELD_DEPLOYMENT_GUIDE.md)
DEFAULT_SCHEDULE = path.join(_REPO, "config", "wittypi", "watercam_daylight_2h.wpi")


def write_schedule_file(text: str, dest: str = None) -> None:
    """Replace schedule.wpi atomically: temp file in the same directory, fsync,
    rename, fsync the directory. A power cut leaves the old file or the new one,
    never an empty or half-written schedule (which would leave the unit unable
    to wake)."""
    dest = dest or SCHEDULE_FILE_PATH
    dirpath = path.dirname(path.abspath(dest))
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=".schedule.wpi.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(dirpath, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _usable_schedule(p: str) -> bool:
    """A readable, non-empty file with BEGIN, END and at least one ON line."""
    try:
        with open(p, encoding="utf-8") as f:
            words = [line.split()[0] for line in f if line.strip() and not line.lstrip().startswith("#")]
    except (OSError, UnicodeDecodeError, IndexError):
        return False
    return "BEGIN" in words and "END" in words and "ON" in words


def fallback_schedule_sources(wittypi_dir: str = None, repo_default: str = None) -> list:
    """Where a missing schedule.wpi comes back from, most specific first.

    1. <wittypi>/schedule.wpi.deploy: the deployment schedule set aside when a
       unit is switched off or runs a test schedule.
    2. The newest schedule.wpi.deploy-* in <wittypi> or its parent (home), as a
       bench wake test leaves it.
    3. The newest <wittypi>/schedule.wpi.bak-*.
    4. The repo's default daylight preset (DEFAULT_SCHEDULE).
    Test schedules (schedule.wpi.test-*) are never used.
    """
    wittypi_dir = wittypi_dir or WITTYPI_DIRECTORY
    home = path.dirname(path.abspath(wittypi_dir))

    def newest(patterns):
        found = [p for pat in patterns for p in glob.glob(pat)]
        return sorted(found, key=lambda p: os.stat(p).st_mtime if path.exists(p) else 0, reverse=True)

    out = [path.join(wittypi_dir, "schedule.wpi.deploy")]
    out += newest([path.join(wittypi_dir, "schedule.wpi.deploy-*"), path.join(home, "schedule.wpi.deploy*")])
    out += newest([path.join(wittypi_dir, "schedule.wpi.bak-*")])
    out.append(repo_default or DEFAULT_SCHEDULE)
    seen, ordered = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered


def install_fallback_schedule(wittypi_dir: str = None, repo_default: str = None):
    """Install the first usable fallback_schedule_sources() entry as schedule.wpi.
    Returns its path, or None if none is usable or it couldn't be written."""
    wittypi_dir = wittypi_dir or WITTYPI_DIRECTORY
    dest = path.join(wittypi_dir, "schedule.wpi")
    for src in fallback_schedule_sources(wittypi_dir, repo_default):
        if not _usable_schedule(src):
            continue
        try:
            with open(src, encoding="utf-8") as f:
                write_schedule_file(f.read(), dest)
        except OSError as e:
            logging.error("Could not install %s as %s: %s", src, dest, e)
            return None
        logging.warning("No schedule.wpi installed: installed %s", src)
        return src
    return None

class WittyPi4:
    '''A class for interacting with the Witty Pi 4 board'''
    def __init__(self):
        logging.info("Initializing Witty Pi 4 interface")

    # Get WittyPi readings
    # See: https://www.baeldung.com/linux/run-function-in-script
    def run_command(self, command: str) -> str:
        '''Run a Witty Pi 4 command'''
        try:
            command = f"cd {WITTYPI_DIRECTORY} && . ./utilities.sh && {command}"
            # utilities.sh calls i2cget/i2cset from /usr/sbin, which isn't on pi's PATH in
            # an ssh shell (systemd services have it), so readings fail without this.
            env = dict(environ, PATH=environ.get("PATH", "/usr/bin:/bin") + ":/usr/sbin:/sbin")
            output = check_output(command, shell=True, executable="/bin/bash", stderr=STDOUT,
                                  universal_newlines=True, timeout=3, env=env)
            return output.strip()
        except Exception as e:
            logging.error("Could not run Witty Pi 4 command: %s", str(e))
            return "ERROR"

    def has_internet(self) -> bool:
        '''True if utilities.sh's has_internet succeeds. It reports through its exit
        status and prints nothing, so run_command()'s output can't tell: it is ""
        on success and "ERROR" on failure, which read the wrong way round.'''
        env = dict(environ, PATH=environ.get("PATH", "/usr/bin:/bin") + ":/usr/sbin:/sbin")
        try:
            check_output(f"cd {WITTYPI_DIRECTORY} && . ./utilities.sh && has_internet", shell=True,
                         executable="/bin/bash", stderr=STDOUT, timeout=10, env=env)
            return True
        except Exception:
            return False

    def sync_time_with_network(self) -> None:
        '''Sync Witty Pi 4 clock with network time'''

        # See: https://www.uugear.com/forums/technial-support-discussion/witty-pi-4-how-to-synchronise-time-with-internet-on-boot/

        if self.has_internet():
            try:
                output = self.run_command("net_to_system && system_to_rtc")
                logging.info("Time synchronized with network: %s", output)
            except Exception as e:
                logging.error("Could not synchronize time with network: %s", str(e))
        else:
            logging.error("No internet detected")

    def get_temperature(self) -> Optional[float]:
        '''Gets the current temperature reading from the Witty Pi 4 in °C; None if it can't be read'''
        try:
            temperature = self.run_command("get_temperature")
            temperature = temperature.split("/", maxsplit = 1)[0] # Remove the Farenheit reading
            temperature = temperature[:-3] # Remove °C
            temperature = float(temperature)
            logging.info("Temperature: %s °C", temperature)
            return temperature
        except Exception as e:
            logging.error("Could not get temperature: %s", str(e))
            return None

    def get_battery_voltage(self) -> Optional[float]:
        '''Gets the battery voltage reading from the Witty Pi 4 in V; None if it can't be read'''
        try:
            battery_voltage = self.run_command("get_input_voltage")
            battery_voltage = float(battery_voltage) # Remove V
            logging.info("Battery voltage: %s V", battery_voltage)
            return battery_voltage
        except Exception as e:
            logging.error("Could not get battery voltage: %s", str(e))
            return None

    def get_internal_voltage(self) -> Optional[float]:
        '''Gets the internal (5V) voltage from the Witty Pi 4 in V; None if it can't be read'''
        try:
            internal_voltage = self.run_command("get_output_voltage")
            internal_voltage = float(internal_voltage)
            logging.info("Output voltage: %s V", internal_voltage)
            return internal_voltage
        except Exception as e:
            logging.error("Could not get Raspberry Pi voltage: %s", str(e))
            return None

    def get_internal_current(self) -> Optional[float]:
        '''Gets the internal (5V) current reading from the Witty Pi 4 in A; None if it can't be read'''
        try:
            internal_current = self.run_command("get_output_current")
            internal_current = float(internal_current)
            logging.info("Output current: %s A", internal_current)
            return internal_current
        except Exception as e:
            logging.error("Could not get Raspberry Pi current: %s", str(e))
            return None

    def get_low_voltage_threshold(self) -> float:
        '''Gets the low threshold from the Witty Pi 4'''
        try:
            low_voltage_threshold = self.run_command("get_low_voltage_threshold")

            if low_voltage_threshold == "disabled":
                logging.info("Low voltage threshold: disabled")
                return 0.0

            low_voltage_threshold = float(low_voltage_threshold[:-1])
            logging.info("Low voltage threshold: %s V", low_voltage_threshold)
            return low_voltage_threshold

        except Exception as e:
            logging.error("Could not get low voltage threshold: %s", str(e))
            return 0.0

    def get_recovery_voltage_threshold(self) -> float:
        '''Gets the recovery threshold from the Witty Pi 4'''
        try:
            recovery_voltage_threshold = self.run_command("get_recovery_voltage_threshold")

            if recovery_voltage_threshold == "disabled":
                logging.info("Recovery voltage threshold: disabled")
                return 0.0

            recovery_voltage_threshold = float(recovery_voltage_threshold[:-1])
            logging.info("Recovery voltage threshold: %s V", recovery_voltage_threshold)
            return recovery_voltage_threshold

        except Exception as e:
            logging.error("Could not get recovery voltage threshold: %s", str(e))
            return 0.0

    def set_low_voltage_threshold(self, voltage: float) -> float:
        '''Sets the low voltage threshold from the Witty Pi 4'''
        try:
            if voltage != self.get_low_voltage_threshold():
                low_voltage_threshold = self.run_command(f"set_low_voltage_threshold {int(voltage*10)}")
                logging.info("Set low voltage threshold to: %s V", voltage)
                return low_voltage_threshold

            logging.info("Low voltage threshold already set to: %s V", voltage)
            return voltage

        except Exception as e:
            logging.error("Could not set low voltage threshold: %s", str(e))
            return 0.0

    # Set recovery voltage threshold
    def set_recovery_voltage_threshold(self, voltage: float) -> float:
        '''Sets the recovery voltage threshold from the Witty Pi 4'''
        try:
            if voltage != self.get_recovery_voltage_threshold():
                recovery_voltage_threshold = self.run_command(f"set_recovery_voltage_threshold {int(voltage*10)}")
                logging.info("Set recovery voltage threshold to: %s V", voltage)
                return recovery_voltage_threshold

            logging.info("Recovery voltage threshold already set to: %s V", voltage)
            return voltage

        except Exception as e:
            logging.error("Could not set recovery voltage threshold: %s", str(e))
            return 0.0

    @staticmethod
    def generate_schedule(start_hour: int, start_minute: int, interval_length_minutes: int, num_repetitions_per_day: int) -> None:
        '''Generate a startup schedule file for Witty Pi 4'''

        # ON window must cover the full capture+process+transmit cycle.
        # Empirically the workflow takes 15–30 min; use 28 min to stay safely
        # within a 30-min interval while leaving 2 min for the WittyPi grace
        # period.  Tune this if the interval_length_minutes changes.
        max_duration_minutes = max(4, interval_length_minutes - 2)

        # Basic validity check of parameters
        if not 0 <= start_hour < 24:
            start_hour = 8

        if not 0 <= start_minute < 60:
            start_minute = 0

        if not 0 < interval_length_minutes <= 1440:
            interval_length_minutes = 30

        if not 0 < num_repetitions_per_day <= 250:
            num_repetitions_per_day = 8

        if ((num_repetitions_per_day * interval_length_minutes) + start_minute + (start_hour * 60)) > 1440:
            num_repetitions_per_day = 1

        # 2037 is the maximum year for WittyPi
        formatted_start_time = f"{start_hour:02d}:{start_minute:02d}"
        schedule = f"BEGIN\t2020-01-01 {formatted_start_time}:00\nEND\t2037-12-31 23:59:59\n"

        for i in range(num_repetitions_per_day):
            schedule += f"ON\tM{max_duration_minutes}\n"

            # Last off is different
            if i < num_repetitions_per_day - 1:
                # Your code here
                schedule += f"OFF\tM{interval_length_minutes - max_duration_minutes}\n"

        # Turn camera off for the rest of the day
        remaining_minutes = 1440 - (num_repetitions_per_day * interval_length_minutes) + (interval_length_minutes - max_duration_minutes)
        remaining_hours = remaining_minutes // 60
        remaining_minutes = remaining_minutes % 60

        schedule += f"OFF\tH{remaining_hours}"
        if remaining_minutes > 0:
            schedule += f" M{remaining_minutes}"

        old_schedule = None
        if path.exists(SCHEDULE_FILE_PATH):
            try:
                with open(SCHEDULE_FILE_PATH, "r", encoding='utf-8') as f:
                    old_schedule = f.read()
            except (OSError, UnicodeDecodeError) as e:
                logging.warning("Could not read %s (%s): rewriting it", SCHEDULE_FILE_PATH, e)
        else:
            logging.warning("Schedule file not found. Writing new schedule file.")
        if old_schedule == schedule:
            logging.info("Schedule did not change.")
        else:
            if old_schedule is not None:
                logging.info("Schedule changed - writing new schedule file.")
            write_schedule_file(schedule, SCHEDULE_FILE_PATH)

    def apply_schedule(self, max_retries: int = 5) -> str:
        '''Apply schedule to Witty Pi 4'''
        if not path.exists(SCHEDULE_FILE_PATH):
            # Nothing to arm, and retrying or syncing the clock can't change that.
            logging.info("No %s installed: no schedule to apply", SCHEDULE_FILE_PATH)
            return "-"
        for retry in range(max_retries):
            try:
                # Apply new schedule
                command = f"cd {WITTYPI_DIRECTORY} && sudo ./runScript.sh"
                output = check_output(command, shell=True, executable="/bin/bash", stderr=STDOUT, universal_newlines=True, timeout=15)
                output = output.split("\n")[1:3]

                if len(output) > 1 and "Schedule next startup at:" in output[1]:
                    logging.info("%s", output[0])
                    logging.info("%s", output[1])
                    next_startup_time = output[1][-19:]
                    return next_startup_time

                logging.warning("Failed to apply schedule: %s", output[0] if output else "(no output)")
                if retry == 0:
                    # A wrong clock is the usual cause (e.g. "interrupted"); fix it once
                    self.sync_time_with_network()

            except Exception as e:
                logging.error("Failed to apply schedule: %s (%s)", str(e), retry)

        # Return error if max retries reached
        return "-"

    def _set_alarm(self, which: str, day: int, hour: int, minute: int, second: int) -> bool:
        if not (1 <= day <= 31 and 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
            raise ValueError(f"bad {which} time {day} {hour}:{minute}:{second}")
        out = self.run_command(f"set_{which}_time {day} {hour} {minute} {second}")
        if out == "ERROR":
            return False
        logging.info("%s time set to day %02d %02d:%02d:%02d", which.capitalize(), day, hour, minute, second)
        return True

    def set_shutdown_time(self, day: int, hour: int, minute: int, second: int) -> bool:
        '''Arm the shutdown alarm (alarm 2) for day-of-month/hour/minute/second in
        the system's local time, as runScript.sh does. True if the command ran.'''
        return self._set_alarm("shutdown", day, hour, minute, second)

    def set_startup_time(self, day: int, hour: int, minute: int, second: int) -> bool:
        '''Arm the startup alarm (alarm 1), like set_shutdown_time.'''
        return self._set_alarm("startup", day, hour, minute, second)

    def clear_shutdown_time(self) -> None:
        '''Clear the shutdown time from the Witty Pi 4'''
        try:
            self.run_command("clear_shutdown_time")
            logging.info("Shutdown time cleared")
        except Exception as e:
            logging.error("Could not clear shutdown time: %s", str(e))

if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)

    witty_pi_4 = WittyPi4()
    witty_pi_4.sync_time_with_network()
    witty_pi_4.get_temperature()
    witty_pi_4.get_battery_voltage()
    witty_pi_4.get_internal_voltage()
    witty_pi_4.get_internal_current()
    witty_pi_4.get_low_voltage_threshold()
    witty_pi_4.get_recovery_voltage_threshold()
    witty_pi_4.set_low_voltage_threshold(3.5)
    witty_pi_4.set_recovery_voltage_threshold(3.7)
    witty_pi_4.generate_schedule(8, 0, 30, 8)
    witty_pi_4.apply_schedule()
