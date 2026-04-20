# Battery Level Estimation — Design & Implementation Plan

**GitHub issue:** #46  
**Branch:** `feature/issue-46-battery-estimation`  
**Date:** 2026-04-19

---

## 1. Hardware Context

### Power chain

```
Solar panel
    │  (solar input cable, ~6–20V)
    ▼
Voltaic V50 battery pack
    │  (regulated output)
    ├─ USB-C 5V  ──► WittyPi 4 USB-C input
    └─ 6V system port (optional)
                         │
                    WittyPi 4
                         │  (5V via GPIO header)
                         ▼
                   Raspberry Pi
```

### What WittyPi 4 can measure

| Command | What it reads | Useful for SOC? |
|---|---|---|
| `get_input_voltage` | VIN pin (XH2.54) | **No** — reads regulated 5V or 6V, constant regardless of battery charge |
| `get_output_voltage` | 5V rail to RPi | **No** — regulated by WittyPi DC/DC converter |
| `get_output_current` | Current to RPi | Partial — shows load, not battery level |

### Voltaic V50 characteristics

- **Capacity:** 50 Wh / ~13,500 mAh at 3.7V nominal cell voltage
- **Output:** 5V USB regulated; no raw cell voltage terminal
- **Digital interface:** None — SOC indicator is 4 LEDs only
- **Charging input:** Solar panel (5–20V, up to ~2A) or USB

**Conclusion:** The existing LiPo formula `(V − 3.0) / 1.2 × 100` is physically wrong.
`get_input_voltage` returns ~5.0 V at all charge levels → formula yields 166%, clamped to 100%.

---

## 2. Problems to Fix

| # | Problem | Severity | File |
|---|---|---|---|
| A | Wrong voltage model (LiPo formula on regulated 5V) | Critical | `ticktalk_main.py:1576` |
| B | LoRa path never calls `get_wittypi_status()` — `battery_percent` absent or hardcoded | High | `tools/lora_transmit.py`, callers |
| C | Silent omission when `batt_v == 0.0` — receiver can't distinguish 0% from unavailable | Medium | `ticktalk_main.py:1575` |
| D | Voltage-to-percent logic duplicated inline, not shared | Low | all transmit paths |

---

## 3. Recommended Solution

### 3a. New hardware: Adafruit INA260 (Product #4226)

**Why INA260:**
- I2C (shares existing bus with AHT20/BNO085)
- Measures voltage AND current simultaneously
- ±1.25 mA current resolution, ±1.25 mV voltage resolution
- STEMMA QT connector — no soldering needed
- Adafruit CircuitPython / Python library available (`adafruit_ina260`)

**Placement:**  
Insert INA260 in-line on the power feed from Voltaic V50 output to WittyPi VIN or USB-C input. The INA260 passes power through its shunt resistor; current flows normally.

**Wiring:**
```
Voltaic V50 5V output
    │
    ▼
INA260 Vin+  ──  shunt  ──  INA260 Vin−
                                  │
                             WittyPi USB-C / VIN
INA260 SDA ──► RPi GPIO 2 (I2C1 SDA)
INA260 SCL ──► RPi GPIO 3 (I2C1 SCL)
INA260 GND ──► Common GND
```

### 3b. Coulomb counting algorithm

Since the Voltaic V50 output voltage is regulated and cannot indicate SOC, we integrate current over time:

```
mAh_remaining = mAh_capacity - mAh_discharged_cumulative + mAh_recharged_cumulative
SOC = mAh_remaining / mAh_capacity × 100
```

**State persistence:**  
Write accumulated charge to `/var/lib/watercam/battery_state.json` on each reading. This survives reboots (the RPi is powered off between measurements by WittyPi schedule).

**Known limitations:**
- Requires initial calibration (first boot after full charge, set state to 100%)
- Solar recharge current must also be measured (INA260 on charge path, or assume conservative 0 recharge)
- Coulomb-counting drift accumulates over weeks; periodic full-charge reset needed

**Capacity constant:**  
`VOLTAIC_V50_MAH = 13500` (50 Wh ÷ 3.7V nominal = 13,513 mAh; round to 13500)

### 3c. Fallback path (no new hardware)

Without the INA260:
- Report `battery_source = "unavailable"`
- Transmit raw WittyPi input voltage for diagnostic purposes
- Do NOT apply LiPo formula to regulated voltage
- Omit battery_percent channel from packet (receiver treats as unknown)

---

## 4. Software Architecture

### New module: `tools/battery_manager.py`

```
battery_manager
├── get_battery_status() → dict
│     ├── battery_pct: int | None
│     ├── battery_source: "ina260" | "unavailable"
│     ├── input_voltage_v: float        (raw WittyPi VIN, diagnostic)
│     ├── current_ma: float | None      (INA260, if available)
│     └── mah_remaining: float | None   (coulomb counter, if available)
│
├── _read_ina260() → (voltage_v, current_ma, power_mw) | None
├── _update_coulomb_state(current_ma, elapsed_s) → mah_remaining
├── _load_state() → dict                (reads battery_state.json)
└── _save_state(state: dict) → None     (writes battery_state.json)
```

**State file schema** (`/var/lib/watercam/battery_state.json`):
```json
{
  "mah_discharged": 1234.5,
  "mah_recharged": 56.2,
  "last_updated_utc": "2026-04-19T12:00:00Z",
  "calibrated": true
}
```

### Changes to existing files

| File | Change |
|---|---|
| `tools/wittypi_control.py` | Add `get_input_voltage_raw()` wrapper — return raw voltage only, no percent conversion |
| `ticktalk_main.py` (IP path, line ~1573) | Replace inline formula with `battery_manager.get_battery_status()` |
| `ticktalk_main.py` (LoRa data collect, line ~245) | Wire `battery_manager.get_battery_status()` into `data` dict |
| `tools/lora_transmit.py` | Read `battery_percent` from `data` as before; add defensive check for `None` |
| channel encoding (all paths) | Skip `02 01` channel if `battery_pct is None`; add comment explaining why |

---

## 5. Implementation Steps

- [x] Create GitHub issue #46
- [x] Create branch `feature/issue-46-battery-estimation`
- [ ] **Step 1** — Write `tools/battery_manager.py`
  - INA260 read path (with `try/except` for hardware-not-present)
  - Coulomb counting with persistent state file
  - Fallback that returns `None` percent with source tag
- [ ] **Step 2** — Fix IP uplink path in `ticktalk_main.py`
  - Replace inline LiPo formula with `battery_manager.get_battery_status()`
  - Handle `battery_pct = None` explicitly
- [ ] **Step 3** — Wire LoRa data collection path
  - Add `battery_manager.get_battery_status()` call in the WittyPi data block (~line 245)
  - Store result in `data['battery_percent']` and `data['battery_source']`
- [ ] **Step 4** — Update `lora_transmit.py` to skip channel if percent is None
- [ ] **Step 5** — Tests
  - Unit tests for coulomb counting math
  - Unit tests for INA260 fallback (mock hardware absent)
  - Integration test for packet encoding with None battery
- [ ] **Step 6** — Update `docs/KNOWN_ISSUES.md` (close battery entry)
- [ ] **Step 7** — Hardware procurement note in README

---

## 6. Out of Scope

- Measuring solar recharge current (would require second INA260 on charge path — future work)
- Automatic full-charge calibration trigger (requires detecting when Voltaic V50 is at 100%; no digital signal available — future work)
- Replacing Voltaic V50 with a pack that has a digital fuel gauge interface

---

## 7. Acceptance Criteria

1. `battery_pct` in transmitted packets reflects actual coulomb-counted SOC when INA260 is present
2. When INA260 is absent, battery channel is omitted from packet (not silently wrong)
3. `battery_source` field always present in status dict
4. No LiPo voltage formula applied to regulated supply voltage anywhere in codebase
5. LoRa path transmits live `battery_percent` from `battery_manager`, not hardcoded value
6. All tests pass; INA260 absence does not crash the system
