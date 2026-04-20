"""
Battery state-of-charge estimation for the UFONet HazMapper WaterCam unit.

Hardware: Voltaic V50 battery pack (50 Wh).
Method: read the D+ pin on the Voltaic V50 USB-C output port, which carries
approximately half the internal cell voltage. This signal varies linearly with
SOC and is read by an Adafruit ADS1115 16-bit ADC (Product #1085) over I2C.

Reference: https://blog.voltaicsystems.com/reading-charge-level-of-voltaic-usb-battery-packs/

Wiring:
  Voltaic V50 USB-C D+ ──► ADS1115 AIN0
  ADS1115 SDA/SCL ────────► RPi GPIO 2/3 (shared I2C1 bus)

Without an ADS1115, returns battery_source="unavailable" and battery_pct=None
so callers omit the battery channel from transmissions rather than sending a
wrong value derived from the regulated 5V WittyPi VIN.
"""

import logging
from typing import Optional

ADS1115_I2C_ADDRESS = 0x48

# LiPo cell voltage bounds for the Voltaic V50.
# D+ carries ~½ cell voltage; reconstructed cell voltage is compared to these.
# Tune with empirical D+ readings from the deployed unit if needed.
CELL_V_MIN = 3.0   # cell voltage at 0% SOC
CELL_V_MAX = 4.2   # cell voltage at 100% SOC


def get_battery_status() -> dict:
    """Return current battery state-of-charge from the Voltaic V50 D+ pin.

    Returns a dict with keys:
        battery_pct     int | None   — 0–100, or None when unavailable
        battery_source  str          — "ads1115_dplus" or "unavailable"
        cell_voltage_v  float | None — reconstructed cell voltage (V)
        d_plus_v        float | None — raw D+ pin reading (V)
    """
    d_plus_v = _read_ads1115_dplus()
    if d_plus_v is None:
        _log_wittypi_vin_diagnostic()
        return {
            "battery_pct": None,
            "battery_source": "unavailable",
            "cell_voltage_v": None,
            "d_plus_v": None,
        }

    cell_v = d_plus_v * 2.0
    battery_pct = _cell_voltage_to_pct(cell_v)
    logging.info(
        "Battery: %d%% (D+=%.3fV, cell≈%.3fV)", battery_pct, d_plus_v, cell_v
    )
    return {
        "battery_pct": battery_pct,
        "battery_source": "ads1115_dplus",
        "cell_voltage_v": round(cell_v, 3),
        "d_plus_v": round(d_plus_v, 4),
    }


# ---------------------------------------------------------------------------
# ADS1115 hardware interface
# ---------------------------------------------------------------------------

def _read_ads1115_dplus() -> Optional[float]:
    """Read D+ voltage (V) from ADS1115 AIN0 at ±2.048V gain.

    Returns None if the ADS1115 is not detected (hardware not installed).
    """
    try:
        import board  # type: ignore
        import busio  # type: ignore
        import adafruit_ads1x15.ads1115 as ADS  # type: ignore
        from adafruit_ads1x15.analog_in import AnalogIn  # type: ignore

        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c, address=ADS1115_I2C_ADDRESS)
        ads.gain = 2  # ±2.048V range — best resolution for 1.5–2.1V D+ signal
        chan = AnalogIn(ads, ADS.P0)
        return float(chan.voltage)
    except Exception as e:
        logging.debug("ADS1115 not available: %s", e)
        return None


# ---------------------------------------------------------------------------
# SOC calculation
# ---------------------------------------------------------------------------

def _cell_voltage_to_pct(cell_v: float) -> int:
    """Convert reconstructed cell voltage to SOC percent, clamped [0, 100]."""
    pct = (cell_v - CELL_V_MIN) / (CELL_V_MAX - CELL_V_MIN) * 100
    return max(0, min(100, int(pct)))


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------

def _log_wittypi_vin_diagnostic() -> None:
    """Log the WittyPi VIN reading for cable-fault detection only.

    The Voltaic V50 outputs regulated 5V; this reading does NOT indicate SOC.
    Unexpected values (e.g. <4.5V) may indicate a power cable fault.
    """
    try:
        from tools.wittypi_control import get_wittypi_status
        data = get_wittypi_status()
        vin = data.get("battery_voltage")
        if vin is not None:
            logging.debug("WittyPi VIN (cable diagnostic only, not SOC): %.3fV", vin)
    except Exception:
        pass
