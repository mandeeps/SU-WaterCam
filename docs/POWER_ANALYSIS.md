# WaterCam Power Budget

**Updated:** 2026-10-06, from bench measurements on units 005 and 006 running
current `main` (006 at stock clocks on the packaged kernel since 2026-10-06),
and the field investigation in
[UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md).

Every figure below is marked **measured** or **estimate**. The estimates are
there to size things roughly, not to be relied on; the last section lists
what still needs measuring.

---

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 4B | Some units run `arm_freq=2000`; stock is 1800 (see [Peaks](#peaks-matter-more-than-averages)) |
| Witty Pi 4 | Power switching, RTC and schedule; always powered |
| Dorhea IR-Cut camera, FLIR Lepton 3.5 | Optical/NIR and thermal capture |
| MultiTech mDot | LoRaWAN, Class C (listens continuously), powered from the Witty Pi's always-on 3.3 V rail |
| Quectel EC25 | LTE modem, on IP-transport units only (006 has one) |
| AHT20, BNO055/BNO085 | Negligible draw |
| Voltaic V50 | 48 Wh usable; A ports 2 A each (3 A combined); solar input capped at about 2 A |
| Voltaic 20 W 6 V ETFE panel | Peaks at 23 W, but the V50 accepts only about 10 W of it |

---

## Measured draw

All figures are the Witty Pi's output (Vout × Iout), from the power logger
(`tools/powerlog.py`, one sample a minute) unless noted.

| State | Power | Source |
|---|---|---|
| Idle, booted, no work | **2.0 W** (0.40 A at 4.90 V) | measured: 005, median of 1,076 samples, no modem |
| Idle, 006 (has the LTE modem) | **2.6 W** (0.53 A at 4.89 V) | measured: 006, median of 2,792 samples. This is a difference between units, not the modem's draw: see [The LTE modem](#the-lte-modem) |
| Two captures, from the first photo to the shutdown request | **3.3–3.8 W** mean, **75–87 mWh** in 73–87 s | measured: five real ticktalk wakes on 006, 2026-10-06, sampled at 5 Hz |
| Peaks during a real wake | **7.5–8 W** (1.50–1.67 A) | measured: same five wakes, stock clocks |
| Brief peaks under synthetic load (camera, inference, 4-core stress, modem) | **8–10 W** (1.8–2.2 A) | measured: 006 load tests at 2000 MHz |
| Off: Witty Pi standby plus the mDot listening | **about 0.05–0.1 W** | estimate: not yet measured |

The one-minute logger is too coarse to integrate a wake, so the 2026-10-06
figures come from a 5 Hz sampler of the same Witty Pi registers. The sampler
itself adds some load, which is in every one of those figures equally.

### The LTE modem

*Measured* on 006, 2026-10-06: five real ticktalk wakes (shutdown intercepted,
so the unit stayed on) with the modem attached or in low power (`AT+CFUN=4`
through ModemManager), uploading over WiFi or cellular.

| Run | Two captures | Mean | Peak |
|---|---|---|---|
| Modem attached, WiFi upload (×2) | 78–79 mWh | 3.7–3.8 W | 1.59–1.67 A |
| Modem in low power, WiFi upload (×2) | 76–87 mWh | 3.6–3.7 W | 1.50–1.63 A |
| Modem attached, cellular upload | 75 mWh | 3.3 W | 1.59 A |
| Cellular on its own: attach, reach the server, back to low power (×2) | 9–53 mWh | 2.8–3.9 W | 1.0 A |

- **An attached, idle modem draws less than the Witty Pi resolves** (about
  0.05 W). Idle was 3.22 W with it attached or in low power, and low power
  didn't lower the peaks either. GPS kept working in low power.
- **Re-attaching took 3 s once and 51 s the next time.**
- **WiFi may cost more than the modem:** with WiFi disconnected, idle fell to
  about 2.6–2.9 W. That comes from a single session and needs confirming.
- **Cellular costs data, not power:** 126–474 KB for a wake over cellular, of
  which our own traffic is about 10 KB; the rest is Tailscale
  ([CELLULAR_DATA.md](CELLULAR_DATA.md)). This is why cellular is the last resort
  ([IP_TRANSMISSION.md](IP_TRANSMISSION.md#transport-order-lora-wifi-cellular)).

---

## One wake, with current software

| Step | Time | Source |
|---|---|---|
| Boot (kernel and userspace) | 11 s | measured: `systemd-analyze` on 005 and 006 |
| Next-wake alarm armed, `ticktalk` starts | about 15 s after boot | measured on 006 when testing #97 |
| Wait for the first capture | none since 2026-10-07: it starts as soon as the sensors are up (`TTStartOnArrival`, `tools/wait_for_sensors.py`); before, it waited for the next minute boundary, 0–60 s, 30 s on average | from the code |
| Two captures 60 s apart (`shutdown_iteration_limit` 2). Each runs photo, co-registration, segmentation (1.6 s via the daemon), compression and transmit, and finishes within about 15 s. | about 75 s | measured: 005 and 006 |
| Shutdown request after the last capture | `ticktalk` start to shutdown request: **1 min 48 s** | measured: 005 and 006, 2026-10-04 |
| Shutdown | about 10 s | estimate |

**A wake lasts about 2 minutes, about 0.12 Wh** (0.11–0.13 Wh).
*Partly measured:* the two captures are 75–87 mWh (above). Boot and shutdown
are estimated at about 35 mWh, as they weren't sampled. Until 2026-10-07 the
first capture also waited for the next minute boundary, at about 3.3 W: about
28 mWh on average, which made a wake about 0.14 Wh.

The Witty Pi schedule's ON window (10 or 15 min) is only a maximum: the Pi
shuts itself down when the cycle ends. Energy depends on the number of wakes,
not the length of the window.

Segmentation used to dominate. Before the SegFormer daemon it took about 40 s
per image (about 95 s on a throttled unit), and the old version of this doc
assumed 2–3 minutes. See
[SEGFORMER_OPTIMIZATION.md](SEGFORMER_OPTIMIZATION.md).

---

## Daily use by schedule

*Estimates.* The off-state draw (0.05–0.1 W, unmeasured) is 1.2–2.4 Wh/day on
its own, so it matters as much as the wakes do.

| Schedule | Wakes/day | Wakes | Off-state | Total |
|---|---|---|---|---|
| `watercam_on15_off1h45_offnight.wpi` (every 2 h, 06:00–22:00) | 9 | 1.1 Wh | 1.2–2.4 Wh | **about 2.5–3.5 Wh/day** |
| `watercam_10minutes_per_hour.wpi` | 24 | 2.9 Wh | 1.2–2.4 Wh | **about 4–5.5 Wh/day** |

**These are best cases.** The schedules assume every wake ends with a clean
shutdown.

- **When one doesn't,** the Pi can stay up for the whole ON window or longer, at 2–2.6 W.
- **9 full 15-minute windows** come to about 6 Wh plus the off-state.
- **In the field, 006's use was roughly 12–15 Wh/day** in June–August, on older software. Only 20–37 wakes a month ended with a logged clean shutdown. See [UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md#energy-budget).

### Emergency mode

Emergency mode keeps the unit on: `call_shutdown()` ignores the iteration
limit, and the Witty Pi's shutdown schedule is cleared. It captures every
`photo_interval` for as long as it lasts.

- **Draw:** about 3–4.5 W continuously, idle plus a capture each minute, more with the modem. *Estimate.*
- **Per day:** about 70–110 Wh.
- **On the V50 alone:** 48 Wh lasts about **11–16 hours** with no sun.
- **Solar can't sustain it:** a clear day's 20–30 Wh doesn't cover it. Emergency mode needs a time limit (see below).

---

## Solar input and storage

- **The input is capped at about 10 W.** The 20 W panel peaks at 23 W (5.21 V, 4.46 A), but each V50 solar input accepts at most 2 A. *From the spec.*
- **Clear day:** about 20–30 Wh. **Overcast:** about 3–6 Wh. *Estimates* for Syracuse, NY (43°N), June–September; winter yield hasn't been measured.
- **Storage:** 48 Wh usable.
  - At 3–4 Wh/day that's about **12–16 days with no sun**, best case.
  - At the field figure of 12–15 Wh/day, about **3 days**.

On current software, energy is not usually the limit. 006's failure came from:
- **brownouts at wake time**, under fast current peaks
- **the Witty Pi stranding the unit:** once it has been unpowered past its alarm time, it never wakes the Pi again

The fixes are #97 (arm the next wake before the load starts) and #98 (after a power cut, sleep until the battery recovers).

---

## Peaks matter more than averages

- **Clocks:** stock clocks (`arm_freq=1800`) lower the peak current. On 006, 2000 MHz plus the modem browned out the Pi even on a healthy supply. The old advice that overclocking saves energy by finishing sooner no longer holds: inference takes 1.6 s.
- **Wiring:** keep the Y-adapter, so the load is spread across both V50 A ports. Never charge through the V50's top USB-C port while it powers a unit.
- **Overlap:** the IP upload runs after LoRa, once segmentation is done. On 006 the wake's peaks were the same with the modem attached or in low power, so the modem isn't what drives them at stock clocks.

---

## Ideas that still apply

| Idea | Effect |
|---|---|
| Cap emergency mode (e.g. 6–8 h), then fall back to frequent scheduled wakes | Protects the battery during long events |
| Measure the off-state draw; if the mDot dominates, consider whether it must listen overnight | Up to about 1–2 Wh/day |
| Cache the camera's AWB gains across boots | About 5 s per wake |
| Battery-aware scheduling | Needs a real battery measurement first (see [Battery state of charge](#battery-state-of-charge)) |

No longer relevant:
- **Bigger panel:** the V50's 10 W input cap makes it pointless.
- **INT8 or ONNX for speed:** done.
- **Powering the modem down between cycles:** the Pi and modem are fully off between wakes, and only on for about 2.5 minutes.
- **Keeping the modem in low power during a wake:** measured, no saving (see [The LTE modem](#the-lte-modem)).
- **Trimming the ON window:** the Pi already shuts down when its cycle ends.

---

## Battery state of charge

**Today the units have no way to measure it.** Units 005 and 006 have neither
of the sensors `tools/battery_manager.py` supports (ADS1115 at 0x48, INA260 at
0x40). Until 2026-10-07 `battery_pct` was the Witty Pi's output voltage mapped
from 4.75 V (0 %) to 5.10 V (100 %). But the V50's 5 V output is regulated, so
the voltage moves with load and cabling, not charge: a wall-powered unit read
37–54 % within minutes, and the noise set off LoRa sends. Since then no
battery percentage is sent; the Witty Pi voltage is kept for diagnostics only.

### Planned: read the V50's own charge signal through the mDot

The V25, V50 and V75 put about half the cell voltage on the **USB-C D+ pin**
([Voltaic](https://blog.voltaicsystems.com/reading-charge-level-of-voltaic-usb-battery-packs/)):
roughly 1.85–2.1 V full, and about 1.73 V when the pack turns its USB output
off. The mDot can read it with no new chips:

- **ADC pin:** `PB_0` is unconnected on the main-branch HAT and unused in
  mDot-AT-firmware. In use: `PB_1` (Witty Pi switch, heartbeat, emergency out),
  `PA_5` (boot), `PC_1` (Pi state), `PA_6` (emergency in), `PC_4`/`PC_5` (ID).
  Spare ADC alternatives: `PA_1`, `PA_4`, `PA_0` (wake pin), `PA_7`.
- **Always readable:** the mDot runs from the Witty Pi's always-on 3.3 V rail,
  so it can read the pack while the Pi is off (a resting voltage, more accurate
  than one under load). Its ADC reference is about 3.0 V.
- **Wiring:** a USB-C breakout in the V50's top port with **only D+ and GND**
  connected (never VBUS; that port is the one that sagged when charging through
  it). D+ to `PB_0` through about 10 kΩ, with 100 nF from the pin to GND, and
  the V50's ground shared with the HAT.
- **Firmware:** a new AT command in mDot-AT-firmware that reads `AnalogIn(PB_0)`
  and returns the voltage (built in Mbed Studio).
- **Node software:** a new first path in `battery_manager.py` that asks the
  mDot through the LoRa daemon, with the D+ to cell-voltage scale (about 2×)
  calibrated per pack.
- **Calibration:** run the full drain test (below) with D+ logged once a
  minute, to map D+ to charge and set the empty point. Fix `CELL_V_MIN` while
  doing it: 3.0 V now, but the V50 cuts out at about 3.45 V per cell.
- **Accuracy to expect:** about ±10–15 % of charge mid-range, where a Li-ion
  cell's voltage is flat. Better near empty, which is when it matters.
- **v6 HAT:** add a connector for the D+ lead. The KiCad mDot symbol's pin
  numbers don't match MultiTech's guide (PB0/PB1 are swapped, as are PA0/PA7),
  so lay out by pin name and check the footprint.
- **To confirm first:** that the field V50s are the "Always On" model the
  feature is documented for.

Also in `battery_manager.py`: the INA260 path ignores charging and charges the
whole off period at the current measured at boot. Fix it before anyone fits an
INA260.

## Still to measure

1. **Off-state draw:** the Witty Pi plus the mDot listening in Class C, with the Pi off.
2. **Energy per wake, boot and shutdown included:** the captures were sampled at 5 Hz on 2026-10-06; boot and shutdown weren't.
3. **WiFi's draw:** idle looked about 0.3–0.6 W lower with WiFi disconnected, in one session.
4. **Solar charging through the V50's side port:** a load test while it charges from the real panel in sun.
5. **Winter yield** at the 10 W input cap.
