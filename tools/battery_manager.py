"""
Battery state-of-charge estimation for the UFONet HazMapper WaterCam unit.

Hardware: Voltaic V50 battery pack (50 Wh, ~13 500 mAh at 3.7 V nominal).

Three measurement paths are supported, tried in priority order:

  1. ADS1115 + D+ pin (preferred — most accurate)
     The Voltaic V50 outputs ~½ cell voltage on its USB-C D+ pin (1.5–2.1 V).
     An Adafruit ADS1115 ADC (Product #1085) reads this directly.
     No drift, no state file, fresh reading every boot.
     Ref: https://blog.voltaicsystems.com/reading-charge-level-of-voltaic-usb-battery-packs/
     BOM: ADS1115 #1085 + USB-C breakout #4090 + STEMMA QT cable.

  2. INA260 coulomb counting (fallback — accurate, drifts over time)
     An Adafruit INA260 power monitor (Product #4226) placed in-line on the
     power feed measures current draw. Accumulated mAh is persisted to a state
     file so the counter survives WittyPi-scheduled reboots.
     Requires initial calibration after a known full charge.
     BOM: INA260 #4226 + STEMMA QT cable.

  3. WittyPi output voltage (rough estimate — no extra hardware required)
     The WittyPi 4 reports the voltage it delivers to the Raspberry Pi via
     its 5V GPIO rail. This voltage droops slowly as the Voltaic V50 depletes
     and rises when solar charging restores the pack. The relationship is
     non-linear and load-dependent, so the estimate is coarse but useful for
     detecting critically low charge without additional hardware.
     Calibrate WITTYPI_OUTPUT_V_FULL / WITTYPI_OUTPUT_V_EMPTY against
     observed readings at known charge levels.

  4. Unavailable
     No usable reading available. Returns battery_pct=None; callers omit the
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

# ── WittyPi output-voltage constants ──────────────────────────────────────
# The WittyPi 5V output rail droops as the Voltaic V50 depletes. These
# bounds must be calibrated against observed readings at known charge levels.
# The defaults below are conservative starting points; refine after deployment.
WITTYPI_OUTPUT_V_FULL = 5.10   # output voltage (V) when battery is fully charged
WITTYPI_OUTPUT_V_EMPTY = 4.75  # output voltage (V) when battery is nearly depleted
# Stored in the project root so it stays within the home directory on all deployments.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(_PROJECT_ROOT, "battery_state.json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_battery_status() -> dict:
    """Return current battery SOC, trying each path in priority order.

    Priority: ADS1115 D+ pin → INA260 coulomb counting →
              WittyPi output voltage (rough) → unavailable.

    Return dict keys:
        battery_pct      int | None   — 0–100, or None when unavailable
        battery_source   str          — "ads1115_dplus" | "ina260" |
                                        "wittypi_output" | "unavailable"
        cell_voltage_v   float | None — reconstructed cell voltage (V); ADS1115 only
        d_plus_v         float | None — raw D+ reading (V); ADS1115 only
        current_ma       float | None — instantaneous draw (mA); INA260 only
        mah_remaining    float | None — coulomb-counted charge (mAh); INA260 only
        output_voltage_v float | None — WittyPi 5V rail reading (V); WittyPi path only
        output_current_a float | None — WittyPi output current (A); WittyPi path only
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
            "output_voltage_v": None,
            "output_current_a": None,
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
            "output_voltage_v": None,
            "output_current_a": None,
        }

    # ── Path 3: WittyPi output voltage (rough estimate) ───────────────────
    wittypi_reading = _read_wittypi_output()
    if wittypi_reading is not None:
        output_v, output_a = wittypi_reading
        battery_pct = _wittypi_output_to_pct(output_v)
        logging.info(
            "Battery (WittyPi output, rough): %d%% — %.3fV, %.3fA",
            battery_pct, output_v, output_a,
        )
        return {
            "battery_pct": battery_pct,
            "battery_source": "wittypi_output",
            "cell_voltage_v": None,
            "d_plus_v": None,
            "current_ma": None,
            "mah_remaining": None,
            "output_voltage_v": round(output_v, 3),
            "output_current_a": round(output_a, 3),
        }

    # ── Path 4: unavailable ───────────────────────────────────────────────
    return {
        "battery_pct": None,
        "battery_source": "unavailable",
        "cell_voltage_v": None,
        "d_plus_v": None,
        "current_ma": None,
        "mah_remaining": None,
        "output_voltage_v": None,
        "output_current_a": None,
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
# WittyPi output-voltage interface
# ---------------------------------------------------------------------------

def _read_wittypi_output() -> Optional[tuple[float, float]]:
    """Read output voltage (V) and current (A) from the WittyPi 4.

    Returns (output_voltage_v, output_current_a), or None if unavailable.
    The output voltage is the 5V rail delivered to the Raspberry Pi and droops
    gradually as the Voltaic V50 depletes, providing a rough SOC signal.
    """
    try:
        from tools.wittypi_control import get_wittypi_status
        data = get_wittypi_status()
        if data.get("status") != "wittypi_data_retrieved":
            return None
        output_v = data.get("internal_voltage")
        output_a = data.get("internal_current", 0.0)
        if output_v is None or output_v <= 0:
            return None
        return float(output_v), float(output_a)
    except Exception as e:
        logging.debug("WittyPi output read failed: %s", e)
        return None


def _wittypi_output_to_pct(output_v: float) -> int:
    """Estimate SOC from WittyPi output voltage, clamped [0, 100].

    The mapping is linear between WITTYPI_OUTPUT_V_EMPTY and
    WITTYPI_OUTPUT_V_FULL. Accuracy is limited because the output voltage
    also varies with load current. Treat results as a rough indicator only.
    Calibrate the constants against observed voltages at known charge levels.
    """
    pct = (output_v - WITTYPI_OUTPUT_V_EMPTY) / (WITTYPI_OUTPUT_V_FULL - WITTYPI_OUTPUT_V_EMPTY) * 100
    return max(0, min(100, int(pct)))
