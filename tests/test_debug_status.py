#!/usr/bin/env python3
"""
Tests for the remote debug-status command (LoRa downlink 50/01).

Run directly to print a full report for this machine.
"""

import json

import pytest

MDOT_MAX_PAYLOAD = 242   # bytes, at the default data rate


class TestGetLoraStatusSharesSingleton:
    """Regression: get_lora_status() previously imported lora_handler_concurrent
    and lora_runtime_integration bare (no "tools." prefix), which resolves to
    a second, independent copy of each module with its own singleton/lock —
    self-conflicting with the real one over the serial-port flock whenever a
    debug-status request (LoRa channel 50/01) ran alongside the live
    ticktalk_main.py process. Confirmed on UFO010 in production: the debug
    handler's LoRaHandler() construction stole the flock, and every
    subsequent transmit cycle reported "LoRa serial port already owned by
    another process" for the rest of that boot.

    Since the LoRa daemon/IPC redesign (docs/LORA_HANDLER_MULTIPROCESS_ISSUE.md),
    get_lora_handler() itself returns a lightweight LoRaHandlerClient (or
    None) rather than constructing a real, flock-guarded LoRaHandler -- the
    original cross-module-duplication scenario can no longer occur through
    this path (a client has no serial port/flock to conflict over; only
    tools/lora_daemon.py ever constructs a real handler). This test now
    confirms get_lora_status() reaches the real, qualified
    tools.lora_handler_concurrent.get_lora_handler() -- not a bare-import
    duplicate that would silently miss test/production monkeypatches -- by
    substituting a sentinel and checking it's reflected in the status.
    """

    def test_reports_handler_from_qualified_import(self, monkeypatch):
        import tools.lora_handler_concurrent as lhc
        from tools.debug_status_command import get_lora_status

        sentinel_client = object()
        monkeypatch.setattr(lhc, "get_lora_handler", lambda: sentinel_client)

        status = get_lora_status()

        assert status["status"] == "lora_available"
        assert status["lora_handler_available"] is True

def test_debug_status_command():
    """The report is generated, and its compact LoRa form fits one uplink."""
    pytest.importorskip("psutil")
    from tools.debug_status_command import generate_debug_status, handle_debug_status_command

    debug_status = generate_debug_status()
    for key in ("timestamp", "system_info", "uptime", "cpu_info",
                "memory_info", "disk_info", "system_load", "sensor_status"):
        assert key in debug_status, key

    result = handle_debug_status_command()
    assert result["status"] == "debug_status_generated", result.get("error")
    assert result["lora_size_bytes"] <= MDOT_MAX_PAYLOAD


@pytest.mark.parametrize("command", ["5001", "50011", "50010", "50012"])
def test_digit_form_downlink_gets_a_hex_encoded_reply(command):
    """A '5001...' downlink returns the report hex-encoded, as the mDot path needs."""
    pytest.importorskip("psutil")
    from tools.lora_debug_integration import process_debug_command

    response = process_debug_command(command)
    assert response is not None and response["status"] == "success", response
    payload = bytes.fromhex(response["data"])        # every character must be hex
    assert len(payload) == response["size_bytes"] <= MDOT_MAX_PAYLOAD
    json.loads(payload.decode("utf-8"))               # and it decodes back to the report


@pytest.mark.parametrize("command", ["2001", "500", "", "6001"])
def test_other_commands_are_not_debug_requests(command):
    from tools.lora_debug_integration import process_debug_command
    assert process_debug_command(command) is None


def print_system_details(debug_status):
    """Print detailed system information."""
    print("\n🔍 Detailed System Information:")
    print("-" * 40)
    
    # System info
    sys_info = debug_status['system_info']
    print(f"🖥️  System: {sys_info.get('system', 'unknown')} {sys_info.get('release', 'unknown')}")
    print(f"🏗️  Architecture: {sys_info.get('machine', 'unknown')}")
    print(f"🐍 Python: {sys_info.get('python_version', 'unknown')}")
    
    # CPU info
    cpu_info = debug_status['cpu_info']
    print(f"💻 CPU: {cpu_info.get('cpu_count', 0)} cores")
    if cpu_info.get('cpu_freq_mhz'):
        print(f"⚡ CPU Frequency: {cpu_info['cpu_freq_mhz']} MHz")
    
    # Memory info
    mem_info = debug_status['memory_info']
    print(f"🧠 Memory: {mem_info.get('used_mb', 0):.1f}MB / {mem_info.get('total_mb', 0):.1f}MB ({mem_info.get('percent_used', 0):.1f}%)")
    
    # Disk info
    disk_info = debug_status['disk_info']
    print(f"💾 Disk: {disk_info.get('used_gb', 0):.1f}GB / {disk_info.get('total_gb', 0):.1f}GB ({disk_info.get('percent_used', 0):.1f}%)")
    
    # Network info
    net_info = debug_status['network_info']
    print(f"🌐 Network: {net_info.get('bytes_sent', 0)} bytes sent, {net_info.get('bytes_recv', 0)} bytes received")
    
    # Process info
    proc_info = debug_status['process_info']
    print(f"🔄 Processes: {proc_info.get('total_processes', 0)} total")
    
    # LoRa status
    lora_status = debug_status['lora_status']
    print(f"📡 LoRa: {'Available' if lora_status.get('lora_handler_available') else 'Unavailable'}")
    
    # WittyPi status
    wittypi_status = debug_status['wittypi_status']
    if wittypi_status.get('available'):
        print(f"🔋 WittyPi: Available (Temp: {wittypi_status.get('temperature_c', 0)}°C, Battery: {wittypi_status.get('battery_voltage_v', 0)}V)")
    else:
        print(f"🔋 WittyPi: Unavailable")
    
    # Sensor status
    sensor_status = debug_status['sensor_status']
    print(f"📊 Sensors:")
    for sensor, status in sensor_status.items():
        available = status.get('available', False)
        print(f"   {sensor}: {'Available' if available else 'Unavailable'}")

if __name__ == "__main__":
    from tools.debug_status_command import generate_debug_status
    print_system_details(generate_debug_status())
