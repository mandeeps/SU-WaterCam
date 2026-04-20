"""
Battery state-of-charge estimation for the UFONet HazMapper WaterCam unit.

Hardware: Voltaic V50 battery pack (50 Wh, ~13 500 mAh at 3.7 V nominal).
The V50 outputs regulated 5 V USB — raw cell voltage is not exposed.
SOC is estimated via coulomb counting using an Adafruit INA260 power monitor
(Product #4226) placed in-line on the power feed from the Voltaic V50 to the
WittyPi 4 input.

Without an INA260 the module returns battery_source="unavailable" and
battery_pct=None so callers can omit the battery channel from transmissions
rather than sending a wrong value.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

STATE_FILE = "/var/lib/watercam/battery_state.json"
VOLTAIC_V50_MAH = 13500.0
INA260_I2C_ADDRESS = 0x40


def get_battery_status() -> dict:
    """Return current battery state-of-charge and supporting diagnostics.

    Returns a dict with keys:
        battery_pct      int | None   — 0-100, or None if unavailable
        battery_source   str          — "ina260" or "unavailable"
        input_voltage_v  float | None — raw INA260 bus voltage (V)
        current_ma       float | None — instantaneous current draw (mA)
        mah_remaining    float | None — coulomb-counted remaining charge
    """
    ina_reading = _read_ina260()
    if ina_reading is None:
        _log_wittypi_vin_diagnostic()
        return {
            "battery_pct": None,
            "battery_source": "unavailable",
            "input_voltage_v": None,
            "current_ma": None,
            "mah_remaining": None,
        }

    voltage_v, current_ma, _ = ina_reading
    state = _load_state()
    elapsed_s = _elapsed_seconds_since(state.get("last_updated_utc"))
    mah_remaining = _update_coulomb_state(state, current_ma, elapsed_s)
    _save_state(state)

    battery_pct = max(0, min(100, int(mah_remaining / VOLTAIC_V50_MAH * 100)))
    logging.info(
        "Battery: %.1f mAh remaining (%.0f%%), %.3f V, %.1f mA",
        mah_remaining, battery_pct, voltage_v, current_ma,
    )
    return {
        "battery_pct": battery_pct,
        "battery_source": "ina260",
        "input_voltage_v": voltage_v,
        "current_ma": current_ma,
        "mah_remaining": mah_remaining,
    }


def calibrate_full_charge() -> None:
    """Reset coulomb counter to 100% (call when pack is known to be fully charged)."""
    state = _load_state()
    state["mah_remaining"] = VOLTAIC_V50_MAH
    state["calibrated"] = True
    state["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    logging.info("Battery coulomb counter reset to full (%.0f mAh)", VOLTAIC_V50_MAH)


# ---------------------------------------------------------------------------
# INA260 hardware interface
# ---------------------------------------------------------------------------

def _read_ina260() -> Optional[tuple[float, float, float]]:
    """Read voltage (V), current (mA), power (mW) from INA260 over I2C.

    Returns None if the INA260 is not detected (hardware not installed).
    """
    try:
        import board  # type: ignore
        import busio  # type: ignore
        import adafruit_ina260  # type: ignore

        i2c = busio.I2C(board.SCL, board.SDA)
        ina = adafruit_ina260.INA260(i2c, address=INA260_I2C_ADDRESS)
        voltage_v = ina.voltage
        current_ma = ina.current
        power_mw = ina.power
        return float(voltage_v), float(current_ma), float(power_mw)
    except Exception as e:
        logging.debug("INA260 not available: %s", e)
        return None


def _log_wittypi_vin_diagnostic() -> None:
    """Log the raw WittyPi VIN reading for diagnostic purposes only.

    This value is ~5 V (regulated) when the Voltaic V50 is connected via
    USB-C and does NOT reflect state of charge. It is logged only so that
    unexpected VIN values (e.g. <4.5 V indicating a cable fault) appear in
    system logs.
    """
    try:
        from tools.wittypi_control import get_wittypi_status
        data = get_wittypi_status()
        vin = data.get("battery_voltage", None)
        if vin is not None:
            logging.debug("WittyPi VIN (diagnostic only, not SOC): %.3f V", vin)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Coulomb counting
# ---------------------------------------------------------------------------

def _update_coulomb_state(state: dict, current_ma: float, elapsed_s: float) -> float:
    """Subtract consumed mAh from state and return updated mah_remaining."""
    if elapsed_s <= 0:
        return state.get("mah_remaining", VOLTAIC_V50_MAH)

    elapsed_h = elapsed_s / 3600.0
    # current_ma is draw from battery; positive = discharging
    mah_consumed = current_ma * elapsed_h
    mah_remaining = state.get("mah_remaining", VOLTAIC_V50_MAH) - mah_consumed
    mah_remaining = max(0.0, min(VOLTAIC_V50_MAH, mah_remaining))

    state["mah_remaining"] = mah_remaining
    state["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    return mah_remaining


def _elapsed_seconds_since(iso_timestamp: Optional[str]) -> float:
    """Return seconds elapsed since iso_timestamp, or 0 if unknown/invalid."""
    if not iso_timestamp:
        return 0.0
    try:
        last = datetime.fromisoformat(iso_timestamp)
        now = datetime.now(timezone.utc)
        delta = (now - last).total_seconds()
        return max(0.0, delta)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        if "mah_remaining" not in state:
            state["mah_remaining"] = VOLTAIC_V50_MAH
        return state
    except FileNotFoundError:
        return {"mah_remaining": VOLTAIC_V50_MAH, "calibrated": False, "last_updated_utc": None}
    except Exception as e:
        logging.warning("Could not load battery state: %s — starting fresh", e)
        return {"mah_remaining": VOLTAIC_V50_MAH, "calibrated": False, "last_updated_utc": None}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logging.warning("Could not save battery state: %s", e)
