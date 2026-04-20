# Battery Level Estimation — Design & Implementation Plan

**GitHub issue:** #46  
**Branch:** `feature/issue-46-battery-estimation`  
**Date:** 2026-04-19  
**Reference:** https://blog.voltaicsystems.com/reading-charge-level-of-voltaic-usb-battery-packs/

---

## 1. Hardware Context

### Power chain

```
Solar panel
    │  (solar input cable, ~6–20V)
    ▼
Voltaic V50 battery pack
    │  (regulated 5V USB-C output)
    │  └─ D+ pin carries ½ × cell voltage (~1.5–2.1V, varies with SOC)
    ▼
WittyPi 4 (USB-C input)
    │  (5V via GPIO header)
    ▼
Raspberry Pi
```

### What WittyPi 4 can measure

| Command | What it reads | Useful for SOC? |
|---|---|---|
| `get_input_voltage` | VIN pin (XH2.54) | **No** — reads regulated 5V, constant regardless of charge |
| `get_output_voltage` | 5V rail to RPi | **Limited / fallback only** — regulated by WittyPi DC/DC, but rail sag under load is a coarse indicator when no better signal exists (Path 3) |
| `get_output_current` | Current to RPi | No — load current, not battery level |

### Voltaic V50 D+ signal (key finding)

Voltaic V25/V50/V75 battery packs output a **scaled cell voltage on the USB-C D+ data pin**:

```
D+ voltage ≈ cell_voltage / 2
```

| Battery state | Cell voltage | D+ voltage |
|---|---|---|
| Full | 4.2 V | ~2.10 V |
| Empty (cutoff) | 3.0 V | ~1.50 V |
| Observed full (empirical) | ~3.7 V | ~1.85 V |
| Observed empty (empirical) | ~3.08 V | ~1.54 V |

The theoretical range is 1.5–2.1 V. Empirical user measurements show 1.54–1.85 V, suggesting the pack's protection circuit cuts off before full cell voltage is reached. The LiPo formula applied to the reconstructed cell voltage is valid for this signal.

The ADS1115 D+ path is the **primary** SOC source: no accumulated drift, no state file, no power-path interruption. The shipped `battery_manager` retains two fallback paths — INA260 coulomb counting (which does use a persisted state file) and WittyPi output voltage — so the system degrades gracefully when the preferred hardware is absent.

---

## 2. Problems to Fix

| # | Problem | Severity | File |
|---|---|---|---|
| A | Wrong voltage source: LiPo formula applied to regulated 5V WittyPi VIN | Critical | `ticktalk_main.py:1576` |
| B | LoRa path never reads live battery status — `battery_percent` hardcoded or absent | High | `tools/lora_transmit.py`, callers |
| C | Silent omission when reading fails — receiver can't distinguish 0% from unavailable | Medium | `ticktalk_main.py:1575` |
| D | Voltage-to-percent logic inline and duplicated across transmit paths | Low | all transmit paths |

---

## 3. Solution

### Primary: ADS1115 + D+ pin

Read the Voltaic V50's D+ pin using an **Adafruit ADS1115** 16-bit ADC over I2C.

**Why ADS1115 over direct GPIO:**
- Raspberry Pi has no built-in ADC; GPIO pins are digital only
- ADS1115 provides 16-bit resolution at ±2.048 V range → ~62.5 µV/LSB
- Over the ~0.31 V empirical D+ span: ~4,960 usable steps (ample precision)
- Already on I2C bus shared with AHT20 and BNO085; no new bus needed
- Adafruit STEMMA QT — no soldering

**SOC formula:**
```python
cell_v = d_plus_v * 2.0
batt_pct = max(0, min(100, int((cell_v - CELL_V_MIN) / (CELL_V_MAX - CELL_V_MIN) * 100)))
```

Where `CELL_V_MIN = 3.0 V`, `CELL_V_MAX = 4.2 V`. These constants are tunable once empirical D+ readings are collected from the deployed unit.

### Fallback chain when ADS1115 is absent

The shipped `battery_manager` does not immediately declare unavailable when ADS1115/D+ is absent. It tries each path in order:

1. **ADS1115 D+** (preferred) — direct voltage reading, no drift, no state file
2. **INA260 coulomb counting** — accurate fallback; accumulated mAh persisted to `battery_state.json`
3. **WittyPi output voltage** — coarse estimate from the 5V rail sag; no extra hardware required
4. **Unavailable** — `battery_pct = None`, `battery_source = "unavailable"`; channel `02 01` omitted from packets so the receiver treats battery state as unknown

WittyPi VIN (`get_input_voltage`) is **not** used for SOC — it reads a regulated 5V rail and is logged only as a cable-fault diagnostic.

---

## 4. Hardware BOM

| Component | Adafruit Product | Qty | Notes |
|---|---|---|---|
| ADS1115 16-bit ADC breakout | #1085 | 1 | I2C addr 0x48; STEMMA QT |
| USB-C breakout board | #4090 | 1 | Exposes D+, D−, VBUS, GND pins |
| STEMMA QT cable | #4210 | 1 | ADS1115 → RPi I2C header |
| 22 AWG wire | — | ~10 cm | D+ from USB-C breakout to ADS1115 AIN0 |

**Wiring:**
```
Voltaic V50 USB-C output
    │
    ▼
USB-C breakout board
    ├─ VBUS (5V) ──► WittyPi USB-C input (power path unchanged)
    ├─ GND ──────► Common GND
    └─ D+ ───────► ADS1115 AIN0

ADS1115
    ├─ AIN0 ──► D+ (as above)
    ├─ GND ───► Common GND
    ├─ SDA ───► RPi GPIO 2 (I2C1 SDA)  [shared with AHT20, BNO085]
    └─ SCL ───► RPi GPIO 3 (I2C1 SCL)
```

The USB-C breakout sits in-line on the USB-C cable from the Voltaic V50 to the WittyPi. VBUS/GND carry power as normal; D+ is tapped for ADC reading only (high-impedance, no load on the signal).

---

## 5. Software Architecture

### `tools/battery_manager.py`

```
get_battery_status() → dict
    battery_pct:      int | None      (0–100, or None if unavailable)
    battery_source:   str             ("ads1115_dplus" | "ina260" |
                                       "wittypi_output" | "unavailable")
    cell_voltage_v:   float | None    (reconstructed cell voltage, V; ADS1115 only)
    d_plus_v:         float | None    (raw D+ reading, V; ADS1115 only)
    current_ma:       float | None    (instantaneous draw, mA; INA260 only)
    mah_remaining:    float | None    (coulomb-counted charge, mAh; INA260 only)
    output_voltage_v: float | None    (WittyPi 5V rail reading, V; WittyPi path only)
    output_current_a: float | None    (WittyPi output current, A; WittyPi path only)

Priority fallback chain:
  1. ADS1115 D+ (preferred — no drift, no state file)
  2. INA260 coulomb counting (fallback — accurate, drifts; state in battery_state.json)
  3. WittyPi output voltage (rough estimate — no extra hardware)
  4. Unavailable — battery_pct=None, battery channel omitted from packets

_read_ads1115_dplus() → float | None
    Read AIN0 from ADS1115 at ±2.048V gain.
    Caches the AnalogIn instance at module scope; resets on hardware error.
    Returns voltage in V, or None if hardware absent.

_read_ina260() → tuple[float, float, float] | None
    Read voltage, current, power from INA260.
    Caches the INA260 instance at module scope; resets on hardware error.
    Returns None if hardware absent.

_read_wittypi_output() → tuple[float, float] | None
    Read internal_voltage and internal_current from WittyPi 4.
    Returns None if unavailable.

_cell_voltage_to_pct(cell_v: float) → int
    Apply LiPo formula clamped [0, 100].

_wittypi_output_to_pct(output_v: float) → int
    Linear mapping between WITTYPI_OUTPUT_V_EMPTY and WITTYPI_OUTPUT_V_FULL.

_save_state(state) → None
    Atomic write (temp file + os.replace) to prevent JSON corruption on power loss.
```

### Changes to existing files

| File | Change |
|---|---|
| `ticktalk_main.py` (×2 LoRa data blocks) | Replace WittyPi battery_voltage block with `battery_manager.get_battery_status()` |
| `ticktalk_main.py` (IP uplink, ~line 1573) | Replace inline LiPo formula with `battery_manager.get_battery_status()` |
| `tools/lora_transmit.py` | Guard `battery_percent` with `is not None` check (already done) |
| `tools/lora_handler_concurrent.py` | Same None guard (already done) |

---

## 6. Implementation Steps

- [x] Create GitHub issue #46
- [x] Create branch `feature/issue-46-battery-estimation`
- [x] Write `tools/battery_manager.py` (initial INA260 version)
- [x] Fix IP uplink and LoRa paths in `ticktalk_main.py`
- [x] Fix `lora_transmit.py` and `lora_handler_concurrent.py` None guard
- [x] Write `tests/test_battery_manager.py` (49 tests passing)
- [x] Add WittyPi output-voltage fallback path (Path 3)
- [x] Add INA260 coulomb-counting fallback path (Path 2)
- [x] Cache ADS1115/INA260 sensor instances at module scope to avoid repeated I2C init
- [x] Atomic state-file write (`os.replace`) to prevent JSON corruption on power loss
- [x] Add `battery_state.json` to `.gitignore`
- [x] Fix GPS key lookup in `initial_health_check.py` (`gps_lat`/`gps_lon`)
- [ ] Deploy hardware and collect empirical D+ readings to tune `CELL_V_MIN`/`CELL_V_MAX`
- [ ] Update `docs/KNOWN_ISSUES.md`

---

## 7. Out of Scope

- Measuring solar recharge current (D+ reads instantaneous SOC; recharge is implicit)
- Distinguishing charging vs discharging state (would need D− or CC pin monitoring)
- Automatic wake-on-low-battery (WittyPi low-voltage threshold handles this via VIN)

---

## 8. Acceptance Criteria

1. `battery_pct` in packets reflects D+ derived cell SOC when ADS1115 is present
2. Falls back to INA260 coulomb counting when ADS1115 absent
3. Falls back to WittyPi output voltage when both ADS1115 and INA260 absent
4. When all paths unavailable, battery channel omitted from packet (not silently wrong)
5. `battery_source` field always present in status dict
6. No LiPo formula applied to regulated WittyPi VIN voltage anywhere in codebase
7. LoRa and IP paths both use `battery_manager.get_battery_status()`
8. All tests pass; absence of any hardware does not crash the system
9. State file writes are atomic; JSON is never left corrupt after power loss
