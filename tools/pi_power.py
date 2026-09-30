"""
Raspberry Pi supply health from the firmware's throttled register.

`vcgencmd get_throttled` reports what the Pi itself measured at its own 5V
input -- the only reading on the Pi side of the WittyPi and the GPIO
extension header. The WittyPi measures its output on its own side of the
header, so a voltage drop across the header contacts never shows up in
battery_pct (which is derived from WittyPi output voltage); it does show up
here.

Register bits (raw value, as printed by vcgencmd):
    0  under-voltage now              16  under-voltage has occurred
    1  ARM frequency capped now       17  ARM frequency capping has occurred
    2  throttled now                  18  throttling has occurred
    3  soft temperature limit now     19  soft temperature limit has occurred

The "has occurred" bits are sticky since boot, so a reading taken at
transmit time also captures a brownout during boot inrush or capture.

Over LoRa the flags are packed into one byte to save airtime:
    low nibble  = bits 0-3   (now)
    high nibble = bits 16-19 (since boot)
"""

import subprocess
from typing import Optional

UNDERVOLTAGE_NOW = 1 << 0
UNDERVOLTAGE_OCCURRED = 1 << 16


def get_throttled() -> Optional[int]:
    """Return the raw throttled register, or None when it cannot be read.

    None (not 0) on failure, so callers omit the channel rather than
    transmitting a false "all clear".
    """
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        # Expected form: "throttled=0x50005"
        _, _, value = out.partition("=")
        return int(value, 16)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def pack_throttled_u8(throttled: int) -> int:
    """Pack the raw register into one byte: now-bits low, since-boot bits high."""
    return (throttled & 0x0F) | (((throttled >> 16) & 0x0F) << 4)


def unpack_throttled_u8(packed: int) -> int:
    """Inverse of pack_throttled_u8 -- rebuild the raw register layout."""
    return (packed & 0x0F) | (((packed >> 4) & 0x0F) << 16)
