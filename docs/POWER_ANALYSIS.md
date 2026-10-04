# WaterCam Power Budget

**Updated:** 2026-10-04, from bench measurements on units 005 and 006 running
current `main`, and the field investigation in
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
| Idle with the LTE modem attached | **2.6 W** (0.53 A at 4.89 V) | measured: 006, median of 2,792 samples |
| During a capture cycle | **3.0–5.2 W** | measured: seven one-minute samples taken during test cycles on 005 and 006 |
| Brief peaks (camera, inference, modem transmit) | **8–10 W** (1.8–2.2 A) | measured: 006 load tests |
| Off: Witty Pi standby plus the mDot listening | **about 0.05–0.1 W** | estimate: not yet measured |

The one-minute logger is too coarse to integrate a two-minute wake accurately,
so the per-wake energy below is an estimate built from these figures.

---

## One wake, with current software

| Step | Time | Source |
|---|---|---|
| Boot (kernel and userspace) | 11 s | measured: `systemd-analyze` on 005 and 006 |
| Next-wake alarm armed, `ticktalk` starts | about 15 s after boot | measured on 006 when testing #97 |
| Wait for the first capture (captures fall on `photo_interval` boundaries, 60 s by default) | 0–60 s, 30 s on average | from the code |
| Two captures 60 s apart (`shutdown_iteration_limit` 2). Each runs photo, co-registration, segmentation (1.6 s via the daemon), compression and transmit, and finishes within about 15 s. | about 75 s | measured: 005 and 006 |
| Shutdown request after the last capture | `ticktalk` start to shutdown request: **1 min 48 s** | measured: 005 and 006, 2026-10-04 |
| Shutdown | about 10 s | estimate |

**A wake lasts about 2.5 minutes (2–3 min).** At an average of about 4 W, that
is **about 0.17 Wh per wake** (0.12–0.25 Wh). *Estimate.*

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
| `watercam_on15_off1h45_offnight.wpi` (every 2 h, 06:00–22:00) | 9 | 1.5 Wh | 1.2–2.4 Wh | **about 3–4 Wh/day** |
| `watercam_10minutes_per_hour.wpi` | 24 | 4.1 Wh | 1.2–2.4 Wh | **about 5–6.5 Wh/day** |

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
- **Overlap:** keep modem uploads from overlapping capture and inference.

---

## Ideas that still apply

| Idea | Effect |
|---|---|
| Cap emergency mode (e.g. 6–8 h), then fall back to frequent scheduled wakes | Protects the battery during long events |
| Measure the off-state draw; if the mDot dominates, consider whether it must listen overnight | Up to about 1–2 Wh/day |
| Cache the camera's AWB gains across boots | About 5 s per wake |
| Battery-aware scheduling | Needs a real battery measurement first: `battery_pct` comes from the Witty Pi's Vout, which mostly shows supply sag |

No longer relevant:
- **Bigger panel:** the V50's 10 W input cap makes it pointless.
- **INT8 or ONNX for speed:** done.
- **Powering the modem down between cycles:** the Pi and modem are fully off between wakes, and only on for about 2.5 minutes.
- **Trimming the ON window:** the Pi already shuts down when its cycle ends.

---

## Still to measure

1. **Off-state draw:** the Witty Pi plus the mDot listening in Class C, with the Pi off.
2. **Energy per wake:** with a logger sampling at least once a second, to replace the 0.17 Wh estimate.
3. **Solar charging through the V50's side port:** a load test while it charges from the real panel in sun.
4. **Winter yield** at the 10 W input cap.
