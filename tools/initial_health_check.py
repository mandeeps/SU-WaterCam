#!/usr/bin/env python3

"""
Initial system health check for SU-WaterCam

Runs once at startup to validate:
- CPU temperature
- WittyPi input/battery/internal voltages
- GPS location availability
- IMU orientation availability

On any failure, sends a LoRa packet describing the failed checks.

This script is intended to be invoked manually or from a systemd unit/boot script
before the main application loop. It prints each check and any failure reasons,
and exits 0 on success, 1 on failure (after attempting to send the LoRa alert,
unless --no-alert is given).
"""

from typing import Dict, Any, List
import math
import os
import sys

# Run as `python tools/initial_health_check.py`, Python puts tools/ on sys.path, not the
# repo root, so every `from tools.x import` below would fail and read as "unavailable".
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read_cpu_temperature_c() -> float:
    """Return CPU temperature in Celsius, or math.nan if unavailable."""
    try:
        import subprocess
        result = subprocess.run(['vcgencmd', 'measure_temp'], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            temp_str = result.stdout.strip()
            # Expected format: temp=45.2'C
            value = float(temp_str.split('=')[1].split("'")[0])
            return value
    except Exception:
        pass
    return math.nan


def read_wittypi_voltages() -> Dict[str, float]:
    """Return voltages from WittyPi or empty dict if unavailable."""
    try:
        from tools.wittypi_control import get_data
        temperature_c, battery_voltage_v, internal_voltage_v, internal_current_a = get_data()
        return {
            'wittypi_temperature_c': float(temperature_c),
            'wittypi_battery_voltage_v': float(battery_voltage_v),
            'wittypi_internal_voltage_v': float(internal_voltage_v),
            'wittypi_internal_current_a': float(internal_current_a),
        }
    except Exception:
        return {}


def read_gps_location() -> Dict[str, Any]:
    """Return GPS lat/lon/alt dict if available, else empty dict."""
    try:
        from tools.get_gps import get_lat_lon_alt
        gps = get_lat_lon_alt()
        return gps if isinstance(gps, dict) else {}
    except Exception:
        return {}


def read_imu_orientation() -> Dict[str, Any]:
    """Return IMU orientation dict if available, else empty dict."""
    try:
        from tools.bno055_imu import get_orientation
        orientation = get_orientation()
        return orientation if isinstance(orientation, dict) else {}
    except Exception:
        return {}


def evaluate_health(
    cpu_temp_c: float,
    wittypi: Dict[str, float],
    gps: Dict[str, Any],
    imu: Dict[str, Any]
) -> Dict[str, Any]:
    """Compare readings against defaults and return status and reasons."""
    failures: List[str] = []

    # Thresholds (conservative defaults)
    max_cpu_temp_c = 80.0
    # wittypi_battery_voltage_v is the WittyPi VIN (regulated 5V USB from Voltaic V50).
    # It stays near 5V until the pack dies, so the useful fault threshold is a cable/
    # connection check (~4.5V) rather than a cell-voltage threshold.
    min_input_voltage_v = 4.5
    min_internal_voltage_v = 4.7

    # CPU temp
    if math.isnan(cpu_temp_c):
        failures.append('cpu_temp_unavailable')
    elif cpu_temp_c > max_cpu_temp_c:
        failures.append(f'cpu_temp_high_{cpu_temp_c:.1f}C')

    # WittyPi voltages
    # witty_pi_4 returns -273.15 C and 0 V when it can't read the board, not an error
    if not wittypi or wittypi.get('wittypi_temperature_c') == -273.15:
        failures.append('wittypi_unavailable')
    else:
        bv = wittypi.get('wittypi_battery_voltage_v', math.nan)
        iv = wittypi.get('wittypi_internal_voltage_v', math.nan)
        if math.isnan(bv):
            failures.append('wittypi_input_voltage_unavailable')
        elif bv < min_input_voltage_v:
            failures.append(f'wittypi_input_voltage_low_{bv:.2f}V')
        if math.isnan(iv):
            failures.append('internal_voltage_unavailable')
        elif iv < min_internal_voltage_v:
            failures.append(f'internal_voltage_low_{iv:.2f}V')

    # GPS
    if not gps:
        failures.append('gps_unavailable')
    else:
        lat = gps.get('gps_lat') or gps.get('lat') or gps.get('latitude')
        lon = gps.get('gps_lon') or gps.get('lon') or gps.get('longitude')
        if lat is None or lon is None:
            failures.append('gps_invalid')

    # IMU
    if not imu:
        failures.append('imu_unavailable')

    status = 'fail' if failures else 'ok'
    return {
        'status': status,
        'failures': failures,
        'readings': {
            'cpu_temp_c': cpu_temp_c if not math.isnan(cpu_temp_c) else None,
            **({} if not wittypi else wittypi),
            **({} if not gps else gps),
            **({} if not imu else imu),
        }
    }


def send_lora_alert() -> bool:
    """Transmit a minimal LoRa alert signalling health-check failure.

    Sends timestamp, emergency_status=1, and health_status=0.
    Failure details (failures list, readings) are NOT transmitted —
    the LoRa encoder only encodes known channel fields, and failure details
    would exceed the LoRa payload size limit.
    """
    try:
        import time
        from tools.lora_handler_concurrent import get_lora_handler
        handler = get_lora_handler()
        handler.queue_transmit({
            'timestamp': int(time.time()),
            'emergency_status': 1,
            'health_status': 0,
        })
        handler.process_transmit_queue()
        return True
    except Exception:
        return False


def format_report(result: Dict[str, Any]) -> str:
    """Human-readable summary: status, each failure, then every reading."""
    lines = [f"Health check: {result['status'].upper()}"]
    lines += [f"  FAIL  {f}" for f in result['failures']]
    for key, value in result['readings'].items():
        lines.append(f"  {key:28s} {value}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--no-alert', action='store_true',
                        help="don't send the LoRa alert on failure (bench runs)")
    args = parser.parse_args(argv)

    cpu_temp_c = read_cpu_temperature_c()
    wittypi = read_wittypi_voltages()
    gps = read_gps_location()
    imu = read_imu_orientation()

    result = evaluate_health(cpu_temp_c, wittypi, gps, imu)
    print(format_report(result), flush=True)

    if result['status'] == 'fail':
        if not args.no_alert:
            sent = send_lora_alert()
            print(f"LoRa alert {'queued' if sent else 'could not be sent'}")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())


