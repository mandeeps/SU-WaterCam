"""
Battery state-of-charge estimation for the UFONet HazMapper WaterCam unit.

Hardware: Voltaic V50 battery pack (50 Wh, ~13 500 mAh at 3.7 V nominal).

Two measurement paths are supported, tried in priority order:

  1. ADS1115 + D+ pin (preferred)
     The Voltaic V50 outputs ~½ cell voltage on its USB-C D+ pin (1.5–2.1 V).
     An Adafruit ADS1115 ADC (Product #1085) reads this directly.
     No drift, no state file, fresh reading every boot.
     Ref: https://blog.voltaicsystems.com/reading-charge-level-of-voltaic-usb-battery-packs/
     BOM: ADS1115 #1085 + USB-C breakout #4090 + STEMMA QT cable.

  2. INA260 coulomb counting (fallback)
     An Adafruit INA260 power monitor (Product #4226) placed in-line on the
     power feed measures current draw. Accumulated mAh is persisted to a state
     file so the counter survives WittyPi-scheduled reboots.
     Requires initial calibration after a known full charge.
     BOM: INA260 #4226 + STEMMA QT cable.

  3. Unavailable
     Neither sensor detected. Returns battery_pct=None; callers omit the
     battery channel from transmissions rather than sending a wrong value.

Wiring — ADS1115 path:
  Voltaic V50 USB-C D+ ──► ADS1115 AIN0
  ADS1115 SDA/SCL ────────► RPi GPIO 2/3 (shared I2C1 bus)

Wiring — INA260 path:
  Voltaic V50 5V out ──► INA260 Vin+ ──shunt──► INA260 Vin− ──► WittyPi
  INA260 SDA/SCL ──────► RPi GPIO 2/3 (shared I2C1 bus)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

# ── ADS1115 constants ──────────────────────────────────────────────────────
ADS1115_I2C_ADDRESS = 0x48

# LiPo cell voltage bounds for the Voltaic V50.
# D+ carries ~½ cell voltage; reconstructed cell voltage is compared to these.
# Tune with empirical D+ readings once the unit is deployed.
CELL_V_MIN = 3.0   # cell voltage at 0% SOC
CELL_V_MAX = 4.2   # cell voltage at 100% SOC

# ── INA260 constants ───────────────────────────────────────────────────────
INA260_I2C_ADDRESS = 0x40
VOLTAIC_V50_MAH = 13500.0
# Stored in the project root so it stays within the home directory on all deployments.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(_PROJECT_ROOT, "battery_state.json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_battery_status() -> dict:
    """Return current battery SOC, trying ADS1115 then INA260 then unavailable.

    Return dict keys:
        battery_pct     int | None   — 0–100, or None when unavailable
        battery_source  str          — "ads1115_dplus" | "ina260" | "unavailable"
        cell_voltage_v  float | None — reconstructed cell voltage (V); ADS1115 path only
        d_plus_v        float | None — raw D+ reading (V); ADS1115 path only
        current_ma      float | None — instantaneous draw (mA); INA260 path only
        mah_remaining   float | None — coulomb-counted charge (mAh); INA260 path only
    """
    # ── Path 1: ADS1115 + D+ ──────────────────────────────────────────────
    d_plus_v = _read_ads1115_dplus()
    if d_plus_v is not None:
        cell_v = d_plus_v * 2.0
        battery_pct = _cell_voltage_to_pct(cell_v)
        logging.info(
            "Battery (ADS1115): %d%% — D+=%.3fV cell≈%.3fV",
            battery_pct, d_plus_v, cell_v,
        )
        return {
            "battery_pct": battery_pct,
            "battery_source": "ads1115_dplus",
            "cell_voltage_v": round(cell_v, 3),
            "d_plus_v": round(d_plus_v, 4),
            "current_ma": None,
            "mah_remaining": None,
        }

    # ── Path 2: INA260 coulomb counting ───────────────────────────────────
    ina_reading = _read_ina260()
    if ina_reading is not None:
        voltage_v, current_ma, _ = ina_reading
        state = _load_state()
        elapsed_s = _elapsed_seconds_since(state.get("last_updated_utc"))
        mah_remaining = _update_coulomb_state(state, current_ma, elapsed_s)
        _save_state(state)
        battery_pct = max(0, min(100, int(mah_remaining / VOLTAIC_V50_MAH * 100)))
        logging.info(
            "Battery (INA260): %d%% — %.1f mAh remaining, %.3fV, %.1f mA",
            battery_pct, mah_remaining, voltage_v, current_ma,
        )
        return {
            "battery_pct": battery_pct,
            "battery_source": "ina260",
            "cell_voltage_v": None,
            "d_plus_v": None,
            "current_ma": round(current_ma, 2),
            "mah_remaining": round(mah_remaining, 1),
        }

    # ── Path 3: unavailable ───────────────────────────────────────────────
    _log_wittypi_vin_diagnostic()
    return {
        "battery_pct": None,
        "battery_source": "unavailable",
        "cell_voltage_v": None,
        "d_plus_v": None,
        "current_ma": None,
        "mah_remaining": None,
    }


def calibrate_full_charge() -> None:
    """Reset INA260 coulomb counter to 100% (call when pack is known fully charged)."""
    state = _load_state()
    state["mah_remaining"] = VOLTAIC_V50_MAH
    state["calibrated"] = True
    state["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)
    logging.info("INA260 coulomb counter reset to full (%.0f mAh)", VOLTAIC_V50_MAH)


# ---------------------------------------------------------------------------
# ADS1115 interface
# ---------------------------------------------------------------------------

def _read_ads1115_dplus() -> Optional[float]:
    """Read D+ voltage (V) from ADS1115 AIN0 at ±2.048V gain.

    Returns None if the ADS1115 is not detected.
    """
    try:
        import board  # type: ignore
        import busio  # type: ignore
        import adafruit_ads1x15.ads1115 as ADS  # type: ignore
        from adafruit_ads1x15.analog_in import AnalogIn  # type: ignore

        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c, address=ADS1115_I2C_ADDRESS)
        ads.gain = 2  # ±2.048V — best resolution for 1.5–2.1V D+ signal
        chan = AnalogIn(ads, ADS.P0)
        return float(chan.voltage)
    except Exception as e:
        logging.debug("ADS1115 not available: %s", e)
        return None


def _cell_voltage_to_pct(cell_v: float) -> int:
    """Convert reconstructed cell voltage to SOC percent, clamped [0, 100]."""
    pct = (cell_v - CELL_V_MIN) / (CELL_V_MAX - CELL_V_MIN) * 100
    return max(0, min(100, int(pct)))


# ---------------------------------------------------------------------------
# INA260 interface
# ---------------------------------------------------------------------------

def _read_ina260() -> Optional[tuple[float, float, float]]:
    """Read voltage (V), current (mA), power (mW) from INA260 over I2C.

    Returns None if the INA260 is not detected.
    """
    try:
        import board  # type: ignore
        import busio  # type: ignore
        import adafruit_ina260  # type: ignore

        i2c = busio.I2C(board.SCL, board.SDA)
        ina = adafruit_ina260.INA260(i2c, address=INA260_I2C_ADDRESS)
        return float(ina.voltage), float(ina.current), float(ina.power)
    except Exception as e:
        logging.debug("INA260 not available: %s", e)
        return None


def _update_coulomb_state(state: dict, current_ma: float, elapsed_s: float) -> float:
    """Subtract consumed mAh from state, update timestamp, return mah_remaining."""
    if elapsed_s > 0:
        mah_consumed = current_ma * (elapsed_s / 3600.0)
        state["mah_remaining"] = max(
            0.0,
            min(VOLTAIC_V50_MAH, state.get("mah_remaining", VOLTAIC_V50_MAH) - mah_consumed),
        )
    state["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    return state["mah_remaining"]


def _elapsed_seconds_since(iso_timestamp: Optional[str]) -> float:
    """Return seconds elapsed since iso_timestamp, or 0 if unknown/invalid."""
    if not iso_timestamp:
        return 0.0
    try:
        last = datetime.fromisoformat(iso_timestamp)
        return max(0.0, (datetime.now(timezone.utc) - last).total_seconds())
    except Exception:
        return 0.0


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("mah_remaining", VOLTAIC_V50_MAH)
        return state
    except FileNotFoundError:
        return {"mah_remaining": VOLTAIC_V50_MAH, "calibrated": False, "last_updated_utc": None}
    except Exception as e:
        logging.warning("Could not load battery state: %s — starting fresh", e)
        return {"mah_remaining": VOLTAIC_V50_MAH, "calibrated": False, "last_updated_utc": None}


def _save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logging.warning("Could not save battery state: %s", e)


# ---------------------------------------------------------------------------
# Diagnostic
# ---------------------------------------------------------------------------

def _log_wittypi_vin_diagnostic() -> None:
    """Log WittyPi VIN for cable-fault detection only — NOT an SOC signal.

    The Voltaic V50 outputs regulated 5V; VIN does not vary with charge level.
    Values below ~4.5V may indicate a power cable fault.
    """
    try:
        from tools.wittypi_control import get_wittypi_status
        data = get_wittypi_status()
        vin = data.get("battery_voltage")
        if vin is not None:
            logging.debug("WittyPi VIN (cable diagnostic only): %.3fV", vin)
    except Exception:
        pass
