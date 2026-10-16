"""WiFi only when it's needed: off while LoRa works, like cellular.

At the start of each wake cycle apply() turns the WiFi radio off when LoRa is
healthy (tools/lora_health.py) and on when it isn't, so the IP uplink can use
WiFi before cellular. NetworkManager keeps the radio state across boots, so a
unit that found LoRa healthy also boots with WiFi off next time.

WiFi stays on:
- during a remote access session that uses WiFi (18 99 WiFi only, or 18 9A
  WiFi then cellular; tools/remote_debug.py),
- while the unit counts as moved from its mount (tools/gps_home.py), so the
  alert has every route out,
- with the runtime parameter wifi_when_lora_healthy true (bench units),
- when this wake will probably make an IP fallback upload (tools/ip_fallback.py):
  the server asked for one (17 97), or LoRa has held bitmaps back too long. On
  early, WiFi can join during the capture, before cellular would be tried.
"""
import os
import subprocess
from typing import Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE_STATE = os.path.join(REPO, "data", "remote_debug.json")


def _on_a_pi() -> bool:
    """Only a node's radio is switched: never a workstation running the tests."""
    try:
        with open("/proc/device-tree/model") as f:
            return "Raspberry Pi" in f.read()
    except OSError:
        return False


def radio(on: bool) -> bool:
    """Turn the WiFi radio on or off (user pi controls NetworkManager)."""
    if not _on_a_pi():
        return False
    try:
        r = subprocess.run(["nmcli", "radio", "wifi", "on" if on else "off"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def radio_is_on() -> Optional[bool]:
    if not _on_a_pi():
        return None
    try:
        r = subprocess.run(["nmcli", "radio", "wifi"], capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "enabled" if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _session_active() -> bool:
    """A remote session that uses WiFi (not a cellular-only one) is running."""
    try:
        import json
        with open(REMOTE_STATE) as f:
            st = json.load(f)
        return bool(st.get("active")) and st.get("mode", "cellular") != "cellular"
    except (OSError, ValueError):
        return False


def _moved() -> bool:
    try:
        from tools.gps_home import load
        return bool(load().get("moved"))
    except Exception:
        return False


def _keep_on_param() -> bool:
    try:
        from tools.lora_runtime_integration import get_parameter
        return bool(get_parameter('wifi_when_lora_healthy', False))
    except Exception:
        return False


def _ip_fallback_due() -> bool:
    try:
        from tools import ip_fallback
        from tools.lora_runtime_integration import get_parameter
        return ip_fallback.wifi_wanted(
            after_hours=get_parameter('ip_fallback_after_hours', ip_fallback.DEFAULT_AFTER_HOURS),
            min_interval_hours=get_parameter('ip_fallback_min_interval_hours',
                                             ip_fallback.DEFAULT_MIN_INTERVAL_HOURS))
    except Exception:
        return False


def ensure_on(wait_s: float = 20) -> bool:
    """Radio on for an IP fallback upload decided mid-wake; waits up to wait_s for
    a saved network to show up. True if it was turned on here."""
    if radio_is_on() is not False:
        return False
    ok = radio(True)
    print("📶 WiFi on: IP fallback upload" + ("" if ok else " (nmcli failed)"))
    if ok:
        import time
        from tools.transmit_ip import wifi_connected, wifi_in_range
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline and not (wifi_connected() or wifi_in_range()):
            time.sleep(2)
    return ok


def apply(health: dict) -> str:
    """Set the radio for this wake; returns why, for the log."""
    if _session_active():
        want, why = True, "remote access session"
    elif _keep_on_param():
        want, why = True, "wifi_when_lora_healthy is set"
    elif _moved():
        want, why = True, "unit moved from its mount"
    elif health.get("healthy") and _ip_fallback_due():
        want, why = True, "IP fallback upload due"
    elif health.get("healthy"):
        want, why = False, "LoRa is healthy"
    else:
        want, why = True, f"LoRa not healthy ({health.get('reason', '?')})"
    if radio_is_on() != want:
        ok = radio(want)
        print(f"📶 WiFi {'on' if want else 'off'}: {why}" + ("" if ok else " (nmcli failed)"))
    return f"WiFi {'on' if want else 'off'}: {why}"
