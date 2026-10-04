# Unit 006 Power Failure (September 2026)

| | |
|---|---|
| **Unit** | ufo-01-01-006 (Pi 4B rev 1.5, Witty Pi 4, Voltaic V50, 20 W panel) |
| **Failed** | 2026-09-14, 21:03 |
| **Bench work** | 2026-09-29 to 2026-10-02 |
| **Fixes** | SU-WaterCam [#97](https://github.com/mandeeps/SU-WaterCam/pull/97) and [#98](https://github.com/mandeeps/SU-WaterCam/pull/98) |

006 went silent in the field on 14 September and never woke again on its own,
even after its solar panel would have recharged the battery. This document
covers what the bench tests and the unit's own logs showed, the most likely
chain of events, the software fixes, and what is still open.

Confidence labels: **Confirmed** means measured or seen directly. **Likely**
means it fits all the evidence but isn't proven. **Open** means not yet tested.

## Summary

- **What happened:** during the 21:00 wake on 14 September, 006 lost power
  abruptly at 21:03, mid-capture. It made one brief start on 20 September at
  20:10, died within a minute, and stayed off until it was brought in on
  29 September.
- **Why it stayed dead:** once the Witty Pi has been without power past its
  alarm time, it never starts the Pi later and never sets a new alarm. The
  only way back is power returning with "Default ON", which boots the Pi
  straight onto a nearly empty battery. 006's boot also left its next wake
  unset for the first ~55 s, during the heaviest load of the cycle.
- **Underlying cause:** marginal power. There was an energy shortfall as the
  days shortened, plus brownouts at wake time: from August, most wakes started
  with the Witty Pi itself losing power. The V50 also sags badly while charging
  through its top USB-C port. The bench work was initially misled by a faulty
  "known-good" charger.
- **Fixes:** [#97](https://github.com/mandeeps/SU-WaterCam/pull/97) sets the
  next wake 15–17 s after boot instead of 46–55 s, and holds the main program
  until it is set. [#98](https://github.com/mandeeps/SU-WaterCam/pull/98) puts
  a unit back to sleep for at least 2 h when a boot follows a power cut, so the
  battery can recharge. Both were tested on 006.
- **Still open:**
  - whether the V50 sags while charging from a real panel through its side
    solar port
  - whether a full drain on panel power recovers by itself
  - whether other units show the same pattern
  - a Pi-side voltage drop under full load

## Field timeline from 006's logs

The journal had not persisted since April, so this comes from
`~/wittypi/wittyPi.log` and the timestamps of files on disk.

| When | Event | Evidence |
|---|---|---|
| Jun → Sep | Wakes that start with the Witty Pi itself losing power ("power supply newly connected") climb from rare to most wakes, at every hour, including midday. | June 33 vs 210 scheduled, July 65 vs 212, August 110 vs 75, September 76 vs 17 |
| 09-14 21:00:17 | Wake starts with "power supply newly connected". | Witty Pi log |
| 21:01–21:03 | Captures run normally; segmentation finishes at 21:02:24. | Files in `images/20260914-210100`, `-210200` |
| **09-14 21:03** | **Abrupt power loss mid-capture.** The last file written is `images/20260914-210300/IMG_0000.pgm`. | 668 zero bytes at the end of the Witty Pi log (lines never written to disk); last ext4 shutdown unclean |
| 09-14 23:00 → 09-20 | No wakes for six days, including sunny days. | Nothing in the log; no files |
| 09-20 20:10:18 | Power returns ("newly connected"). The Pi boots and dies within about a minute; the last file is written at 20:09:49. | Witty Pi log; `runtime_config.json` and Tailscale files |
| 09-29 | Retrieved and brought to the lab. The V50 shows 3 of 4 bars. | Bench notes |

## Findings

### Bench power path, 005 vs 006

- **006's bench charger was faulty, not "known-good".** *Confirmed.*
  - **On it:** 006 throttled to 600 MHz in most samples and logged repeated
    under-voltage. Segmentation ran about 2.4× slower than on 005 (2.9 s vs
    1.2 s).
  - **On a new charger:** no under-voltage, no throttling, and 1.31 s
    inference at 1800 MHz, which is 005's time scaled by the clock difference.
  - **Consequence:** the 2026-09-29 test that seemed to clear the V50 had used
    this faulty charger, so its conclusion did not hold.
- **The Witty Pis are identical.** *Confirmed.* Both have board ID 0x26,
  firmware 0x07 and scripts v4.21, with the same calibration offsets, and a
  similar input-to-output drop (130–155 mV/A).
- **Software changes alone couldn't fix a weak supply.** *Confirmed.* Stock
  clocks and disabling Bluetooth helped only a little. On the faulty charger,
  only 1 inference thread avoided brownouts, and it was no faster than the
  throttled 4-thread runs. On a good supply, 4 threads is fastest and safe on
  both units.

How fast the Witty Pi's input voltage falls as current rises (fitted slope, in
mV per amp; lower means a stiffer supply). Compare these with each other rather
than as absolute volts, because they come from the Witty Pi's own sensor.

| Supply | mV/A |
|---|---|
| 005, its usual charger | 2 |
| 006, V50 not charging (one A port, stock cable) | 29 |
| 006, V50 + Y-adapter, charger in side port (probably not charging) | 59 |
| 006, new charger | 71 |
| 006, old "known-good" charger | 260 |
| 006, V50 charging via top USB-C port, one A port | 338 |
| 006, V50 charging via top USB-C port, through the Y-adapter | 411 |

The Pi flags under-voltage near 4.63 V. On the worst supplies, the Witty Pi's
output fell to about 4.3 V at the 1.2 A of a capture cycle.

### Voltaic V50 battery

- **Charging through the top USB-C port sags the output and cuts it on
  plug/unplug.** *Confirmed.*
  - **The sag:** while charging through that port, the no-load voltage drops
    about 0.2 V and sags about 0.3 V per amp, enough for under-voltage even at
    idle.
  - **The cut-outs:** plugging in or unplugging a charger at that port reset
    006 both times.
  - **Field relevance:** field units don't charge through this port, so it is a
    bench hazard rather than the field cause.
- **The side solar port doesn't reset the output, but its sag under real solar
  charging is untested.** *Open.*
  - **Plugging in and out** caused no resets.
  - **Why the load tests don't count:** the solar input holds its input at
    5.2 V (matching the panel's 5.21 V maximum-power point), and a 5.0–5.1 V
    wall charger probably can't reach that. That fits what happened: the V50
    drained completely overnight with that charger in the side port while
    running 006 at idle. So the side-port load tests probably weren't charging
    at all.
- **The solar input is capped at about 10 W.** *Confirmed from spec.* The 20 W
  panel peaks at 23 W (5.21 V, 4.46 A), but each V50 solar input accepts at
  most 2 A.
- **The Y-adapter isn't the problem, and may help.** *Confirmed.*
  - **Its cost:** only about 30–45 mV per amp under field conditions.
  - **Why it helps:** each A port is rated 2 A (3 A combined), and with the
    modem active, peak draw reached 2.06–2.21 A (averaged readings). Keep it in
    the field for now.
- **The V50 restores its output after recharging.** *Confirmed.* The product
  is designed to do this, and unit 007 recovered this way through a winter. A
  V50 that stayed off is therefore unlikely to explain 006's six days.

### Witty Pi behaviour

- **The next wake was set about 55 s into each boot.** *Confirmed.*
  - **Why:** the daemon only sets alarms after `beforeScript.sh` finishes, and
    ours ran `syncTime.sh`, which sleeps 30 s and then waits for network time.
  - **Measured:** 46 s on the bench, and about 55 s in the field, while the
    cameras, modem and inference were already running.
- **A missed alarm is gone for good.** *Confirmed by earlier team testing.*
  - **The rule:** if the Witty Pi has no power when the alarm time comes, it
    never starts the Pi later and never sets a new alarm.
  - **What does survive:** its clock keeps time through outages (it had the
    correct time after the six-day gap). An alarm that hasn't been missed also
    survives a 60-second power cut (tested 2026-10-02).
- **The Witty Pi only cuts power once the Pi has sent "system is up".**
  *Confirmed during testing.*
  - **What happened:** a Pi that powered off before the daemon sent SYS_UP on
    GPIO-17 stayed halted with the power on (steady red LED). The Witty Pi
    still counted it as running, so it missed its alarm, and only a long button
    press recovered it.
  - **The contrast:** a shutdown about one second after SYS_UP was cut cleanly.
- **The "default on" delay goes up to only 10 s.** *Confirmed.* That's too
  short to let a battery recharge. 006 is set to 10 s, which only lets the
  V50's output settle before the boot surge.

### Raspberry Pi side

- **Under full load the Pi browns out even on a healthy supply.** *Confirmed.*
  - **What was seen:** with camera, modem pings, inference and 4-core stress
    running, the Pi flagged under-voltage in 82 of 89 samples while the Witty
    Pi still read about 4.70 V.
  - **Why:** fast spikes from the camera and modem transmit bursts, which the
    Witty Pi's averaged readings can't see, plus the drop across the GPIO
    extension header.
- **Repeated on the field supply.** *Confirmed.* On the V50 with the
  Y-adapter (the field wiring), the full-load test on 2026-10-02 again flagged
  under-voltage early in the load (`0x50005`). The Witty Pi output read 4.64 V
  at 1.82 A, and the CPU reached 58–62 °C at 2000 MHz.
- **Both units are overclocked.** *Confirmed.*
  - **Clock:** both have `arm_freq=2000` with `arm_boost=1` (stock is 1800).
    006 was set back to 2000 on 2026-10-01 at 21:26.
  - **Kernel:** 006 runs an `rpi-update` kernel (6.18.54-v8+); 005 runs the
    packaged 6.18.29.

### Energy budget

*Likely:* positive on average, but thin across overcast spells.

- **Use:** about 9 wakes a day and about 2.1–2.4 h of scheduled on-time
  (June–August) comes to roughly 12–15 Wh/day from the pack.
- **Solar input:** about 20–30 Wh on a clear day at the 10 W input cap, but
  only about 3–6 Wh overcast.
- **Storage:** the pack's 48 Wh is about 3 days with no sun.

Only 20–37 wakes a month ended with a logged clean shutdown, so actual on-time
may have been higher.

### Timekeeping quirks that matter

- **The system clock is stale early in every boot.** *Confirmed.*
  - **Why:** until the Witty Pi daemon copies its clock in, about 10 s into
    boot, the system clock holds the last saved time, often the previous
    shutdown.
  - **Impact:** two first versions of the fixes made wrong decisions because of
    this. Anything that runs early must use the Witty Pi clock or a per-boot
    log mark.
- **The field schedule runs an hour late in summer.** *Confirmed.*
  `runScript.sh` steps through the schedule in raw seconds from `BEGIN`, and
  the field schedule's `BEGIN` is in January. So during daylight saving time
  the wakes land at 07:00 … 23:00 rather than 06:00 … 22:00, as 006's log
  shows.

## Most likely failure chain

1. **Power margins shrank through late summer.** *Likely.* Shorter days and
   overcast spells against about 12–15 Wh/day of use, with an input capped at
   about 10 W.
2. **Wakes started browning out.** *Confirmed.* From August, most wakes began
   with the Witty Pi itself losing power at the boot surge, then recovering.
3. **On 09-14 at 21:03, power was lost mid-capture.** *Confirmed.* This was
   after sunset, with the pack at its lowest point of the day. The alarm lines
   from that boot were never written to disk, so whether the next wake had been
   set is unknown.
4. **The Witty Pi was without power past its alarm, so it never woke the Pi.**
   *Likely.* A missed alarm never fires later, so recovery depended entirely on
   power returning.
5. **When power returned, "Default ON" booted the Pi onto a nearly empty
   pack.** *Likely.*
   - **The evidence:** the 09-20 boot died within a minute.
   - **Unconfirmable:** earlier attempts that died within ~30 s would leave no
     trace on disk. A repeating restart loop that kept draining the pack would
     fit the gap, but can't be confirmed.

## Fixes

| Change | What it does | Status |
|---|---|---|
| [#97](https://github.com/mandeeps/SU-WaterCam/pull/97) (`early-wittypi-alarm`) | `config/wittypi/beforeScript.sh` syncs network time in the background unless the daemon reported a bad clock, so the next wake is set 15–17 s after boot (was 46–55 s). `ticktalk.service` waits up to 120 s (`tools/wait_for_wittypi_schedule.py`) for "Schedule next startup", then starts regardless. | Tested on 006; open |
| [#98](https://github.com/mandeeps/SU-WaterCam/pull/98) (`recovery-boot`, stacked on #97) | After a power cut, `wittypi-recovery.service` (`tools/recovery_boot.py`) sets the next schedule slot at least 2 h ahead and powers off before the main program starts. A boot within 20 min after the previous wake counts as a scheduled wake that browned out, and runs normally. It powers off only after the daemon's "system is up" signal, takes times from the Witty Pi clock, and uses `wittypi-boot-mark.service` to tell this boot's log lines apart. Any failure runs the normal cycle. Settings are under `recovery_boot` in `runtime_config.json`. | Tested on 006; open |
| Witty Pi on 006 | "Default ON" kept; default-on delay set to 10 s. | Applied |
| Power logger on 006 | `~/powerlog/powerlog.py` run by `powerlog.service`. Once a minute it records Witty Pi voltages and current, throttle flags and the Witty Pi clock, forced to disk so the last reading survives a power cut, plus a marker line per boot. Not in this repo. | Running |
| Bench charger | The faulty charger on 006 was replaced. | Done |

### Tests run on 006

A stub stood in for the main program in these tests, because the real one
uploads to the production server.

| Test | Result |
|---|---|
| Reboot with a schedule, three times | Next wake set at 15, 17 and 16 s; main program started only after it was set |
| Two real scheduled power cycles | Witty Pi cut power and restored it on its alarms; next wake set at 17 s each time |
| Clock deliberately invalidated | Waited for network time, set the alarms at 46 s, restored the Witty Pi clock |
| Alarm survives a 60 s power cut | Booted on time: "scheduled startup is due" |
| Power restored with no wake due | Waited for "system is up", set a wake, powered off (red LED off), woke on the alarm, normal cycle |
| Power restored just after a scheduled wake | Counted as a wake that browned out; normal cycle, stayed on |
| Button press and reboot | Normal cycle |
| Power cut during full load at a scheduled wake, on the V50 with the Y-adapter (load: segmentation loop, camera, 4-core stress, cellular pings; 1.6–1.8 A) | Next wake (22:56) set before the load started. After replug: power restore before that wake, so recovery waited for "system is up", re-set 22:56 and powered off 7 s after boot. Woke at 22:56 on the alarm and ran the full load again |

## Recommendations for field units

- **Keep "Default ON"** on every Witty Pi, with the 10 s delay. "Default off"
  would leave a unit off after any outage that ran past its next wake.
- **Merge #97, then #98, and install both by hand on each unit.** A
  `git pull` alone doesn't install them: the units and `beforeScript.sh` must
  be copied, and `recovery_boot.enabled` set to `true`. See
  `FIELD_DEPLOYMENT_GUIDE.md` for the commands.
- **At deployment, press the Witty Pi button** after connecting power.
  Connecting power alone now just sets an alarm and goes back to sleep.
- **Lower the peak current:** stock clocks rather than `arm_freq=2000`, and
  keep modem uploads from overlapping capture and inference.
- **Match the schedule to the season,** and consider moving `BEGIN` into
  daylight saving time, or dropping the 23:00 wake, to remove the summer hour
  shift.
- **Keep the Y-adapter,** and never charge through the V50's top USB-C port
  while it powers a unit.
- **Check the GPIO extension header's voltage drop** with a multimeter during a
  capture.

## Open questions and next tests

- **Solar charging through the side port:** a load test on 006 while the V50
  charges from the real panel through the side port, in sun. This is the main
  open question for the field.
- **Drain and recovery on panel power:** a full drain with only the panel
  connected, the power logger and the recovery rule turned on, to see the
  cut-out and recovery on the logger's timeline.
- **The other field units:** a compact summary of the Witty Pi logs from
  008–011 (wakes after a power loss; boots that never set a next wake). This
  costs a few KB of cellular data per unit, so it's waiting for approval.
- **Unit 007:** whether it was overclocked or had a modem and Y-adapter during
  the winter it survived.
- **006's configuration:** who set it back to `arm_freq=2000`, and whether to
  return it to the packaged kernel. Also remove the redundant
  `dtoverlay=miniuart-bt` line.

## Current state of the bench units

**006:**
- **Code and services:** SU-WaterCam checkout on the `recovery-boot` branch,
  with the boot-mark and recovery units installed and enabled.
  `recovery_boot.enabled` is `false`, so bench plugging doesn't put it to
  sleep. `ticktalk` is disabled.
- **Witty Pi:** new `beforeScript.sh` (the old one is in
  `beforeScript.sh.orig`); the previous unit is saved as
  `ticktalk.service.pre-pr97`. "Default ON" with the 10 s delay, no alarms set,
  no `schedule.wpi`.
- **Power logger:** running.
- **Test scripts:** `~/loadtest.sh` and `~/modemtest.sh` are in the home
  directory.

**005:** unchanged. Its usual charger, `arm_freq=2000`, packaged kernel
6.18.29; no resets or under-voltage during testing.

### Reusable test tools

- **`~/loadtest.sh`** runs 5 segmentations and 4-core stress, sampling the
  Witty Pi every 0.4 s, and prints per-phase voltage, current and throttling.
  Fitting voltage against current from its CSV gives the mV/A figures above.
- **`~/modemtest.sh`** adds the camera and tiny cellular pings over `wwan0`,
  using about 11 KB of data per run.

```bash
tailscale ssh pi@ufo-01-01-006.taild7822.ts.net 'bash ~/loadtest.sh'
tail -f ~/powerlog/powerlog.csv     # on the node
```

Related earlier work: segformer_5band PR #1 (merged), SU-WaterCam PR #96
(remote debug status, open).
