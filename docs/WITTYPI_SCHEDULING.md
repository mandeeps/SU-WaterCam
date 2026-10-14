# How Witty Pi 4 scheduling works (for diagnosing wake problems)

Reference notes from the Witty Pi 4 firmware and software source and the user
manual, plus what our node software adds. Use them when a unit skips a wake,
wakes at the wrong time, or stays off.

Sources (upstream UUGear repo `Witty-Pi-4`, firmware revision 0x07, the one on
our units):
- firmware: `Firmware/WittyPi4/WittyPi4.ino` (ATtiny841);
- Pi software: `Software/wittypi/daemon.sh`, `runScript.sh`, `utilities.sh`;
- manual: UUGear's Witty Pi 4 user manual (`WittyPi4_UserManual.pdf`, in the
  upstream repo; not kept here). Where the
  manual and the firmware disagree the firmware wins (the manual lists fewer
  start reasons and a 5 s power-cut delay; the firmware's default is 7 s).

Related: [UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md) (the stranding
failure), [FIELD_DEPLOYMENT_GUIDE.md](FIELD_DEPLOYMENT_GUIDE.md) 1.9 (recovery
boot and next-wake services) and 1.13 (installing a schedule),
[POWER_ANALYSIS.md](POWER_ANALYSIS.md) (energy per wake).

## 1. Alarms are one-shot, absolute and have a 2-second window

- Two alarms in the ATtiny, I2C address 0x08, BCD: **startup** (alarm 1) in
  registers 27-30 (second, minute, hour, day of month) and **shutdown**
  (alarm 2) in 32-35. 31 and 36 hold a weekday that is never compared. All
  zero means "not set".
- There's no month: an alarm is "this day of the month, at this time". It isn't
  a daily repeat; something must set the next one every cycle.
- The firmware doesn't use RTC interrupts. A watchdog tick, once a second even
  while asleep, computes `now - alarm` from the RTC and fires only when that is
  **0 or 1 second** (`processAlarmIfNeeded`). An alarm already 2 s or more in
  the past is ignored forever: **a missed alarm never fires late.**
- After firing, registers 9 (alarm 1) and 10 (alarm 2) are set to "triggered",
  which blocks a repeat until the alarm is written again. The alarm values stay.
- Firing is an emulated button press. A startup alarm while the Pi is off powers
  it on (reason 1). A shutdown alarm while it is on starts a shutdown (reason 2);
  while it is off it does nothing.
- **A startup alarm while the Pi is still on or halting is cached**, unless bit
  0 of register 48 (MISC) is set: about 3 s after the power is cut, the firmware
  powers the Pi on with reason 8, "alarm1 delayed". The cache is a RAM variable,
  so a reset of the ATtiny (a brownout) loses it.
- `canTriggerAlarm()` blocks alarms while Vin is at or below the low-voltage
  threshold (or the recovery voltage after a low-voltage shutdown), and a
  blocked alarm is lost like any other missed one.

## 2. Power on and off

Power-on comes from:
- a button press (real, or emulated by an alarm);
- Default ON (register 17 = 1) when the ATtiny starts: it waits register 47
  seconds, then powers the Pi with reason 10;
- voltage recovery (register 22) after a low-voltage shutdown, reason 5;
- guaranteed wake (register 49), reason 12.

Shutdown: the Pi halts (button, shutdown alarm, low Vin, temperature, or our
own `shutdown -h`), and the firmware watches the Pi's TXD line (GPIO14). When it
goes low after the Pi is up, the firmware waits the power-cut delay (register
21 x 0.1 s, default 7 s), then cuts power, unless TXD came back high (a reboot,
reason 11). A TXD line stuck high keeps the Pi powered after a halt.

"System up" is the Pi pulsing GPIO17 at the end of `daemon.sh`'s start-up. Low
voltage only shuts down after about 3 minutes on and once the system is up.

## 3. Start reasons and what they really mean

`daemon.sh` reads register 11 at boot and logs the reason in
`~/wittypi/wittyPi.log`:

| Code | Log text | Means |
|---|---|---|
| 1 | scheduled startup is due | the startup alarm fired while off |
| 3 | the button is clicked | a real button press |
| 5 | voltage is restored | recovery after a low-voltage shutdown |
| 8 | (alarm1 delayed) | a startup alarm that came while the Pi was still on |
| 10 | power supply is newly connected | **the ATtiny itself (re)started** with Default ON |
| 11 | it previously reboot | the Pi rebooted (TXD high again within the cut delay) |
| 12 | guaranteed wake | the guaranteed-wake timer |

**Reason 10 doesn't only mean someone plugged power in.** It's written by
`setup()`, which runs on every ATtiny reset, and there is no reset-cause
register. A dip in Vin when the Pi powers on (the boot inrush) can brown out the
ATtiny. It then cuts the Pi's power, restarts, waits register 47 seconds and
powers the Pi again with reason 10. Alarms and the RTC survive (the registers
reload from EEPROM; the RTC is a separate chip). So "power supply newly
connected" about 20-30 s after an alarm time is a power-on dip, not an outage:
e.g. 006 on 2026-10-14 woke at 15:15:26 for a 15:15:00 alarm. The throttle log
then shows two boots for that wake.

## 4. How the next wake gets set

**Upstream (`daemon.sh` at boot):**
1. checks the ATtiny;
2. copies the RTC to the system clock (or the reverse if the RTC year is 0);
3. logs the reason;
4. waits for GPIO4 to be steady for 5 s (an unsteady line, e.g. a sagging supply,
   counts as a shutdown request);
5. runs `beforeScript.sh` (ours runs `syncTime.sh`);
6. runs `runScript.sh 0 revise` in the background;
7. pulses "system up", then waits on GPIO4 (the button) to shut down.

**`runScript.sh`** reads `~/wittypi/schedule.wpi`:
- syntax: `BEGIN`, `END`, then `ON`/`OFF` lines with `D H M S` durations; `WAIT`
  leaves an edge unscheduled;
- it walks the ON/OFF cycle from BEGIN to the first ON state that ends after
  now, then sets **shutdown = the end of that ON state** and **startup = the end
  of the following OFF state**, as absolute day-of-month times;
- booted inside an OFF state, it treats the next ON as current;
- it logs `Schedule next shutdown at: …` and `Schedule next startup at: …`
  (**one space** after the colon);
- it doesn't enforce a minimum gap.

**Ours** (see the guide, 1.9):
- `tools/recovery_boot.py` (`wittypi-recovery.service`, before ticktalk): on
  reasons 5, 9 or 10 it checks whether the boot falls within
  [armed startup - 2 min, + `scheduled_wake_window_minutes` (20)]. If it does,
  this was a scheduled wake that dipped, and the cycle runs. Otherwise it
  treats it as power returning after an outage: it logs `Recovery boot: …`,
  arms the first ON slot at least `min_delay_minutes` (120) away and powers
  off, so the battery charges first.
- `tools/ensure_next_wake.py` (`wittypi-next-wake.service`, runs as the system
  stops, whatever stopped it): if no startup is armed, the armed one is less
  than 120 s away, or it is later than the next ON slot, it arms the first ON
  slot at least 120 s from now. It never moves a wake later than the next slot,
  and it writes alarm 1 directly (registers 27-30) and clears alarm 2.
- Both log `Schedule next startup at:  …` with **two spaces**, so the log shows
  which program armed a wake. `ensure_next_wake` writes a `Shutdown check: …`
  line just before it.
- Example: a cycle started by the button at 14:56 got shutdown 14:59 and startup
  15:00 from `runScript.sh`. The Pi halted at 14:58, under 2 minutes before
  15:00, so `ensure_next_wake` moved the startup to the 15:15 slot (two
  spaces, 14:58:06). The unit woke at 15:15.

## 5. Clock

- The RTC wins at boot (`daemon.sh`). Ours then corrects it once a boot:
  `syncTime.sh` uses network time, then GPS (`/run/watercam-time-source`), and
  failing both, `transmit_ip.py` asks the server (`watercam-set-clock`).
- The RTC keeps local time and schedules count seconds from BEGIN, so wakes
  move an hour across a daylight-saving change.
- A wrong (non-zero) RTC shifts every wake silently; `utilities.sh` only warns
  when the RTC and system clock differ by more than 10 s. All the
  "too soon"/"window" arithmetic above uses the RTC.
- The stock `rtc_to_system` runs `timedatectl set-ntp 0` on every boot.

## 6. Ways a unit misses wakes or stays off

1. **No alarm armed while the Witty Pi has power** ("stranded"). A boot dies
   (brownout, cable) after the old alarm fired and before `runScript.sh` or
   `ensure_next_wake` armed the next one. Default ON acts only when the ATtiny
   restarts, so nothing starts the Pi until the button, a power cycle, or the
   mDot watchdog (mDot-AT-firmware `AT+WDOG`, 20 h) presses the switch.
2. An alarm set in the past, or one that passes while the ATtiny is resetting,
   is never fired.
3. A startup alarm during a low-Vin condition is blocked and lost.
4. MISC bit 0 set: a startup alarm while the Pi is halting is dropped instead
   of cached.
5. TXD stuck high after a halt: power never cut, so the next alarm (needs the
   Pi off) never fires.
6. A recovery-boot deferral after a dip that missed the window (no armed line
   in the log, or the dip came more than 20 min late): the unit sleeps at least
   2 hours.
7. A wrong RTC: wakes at the wrong real time.

## 7. What to collect from a unit

- `~/wittypi/wittyPi.log`: start reasons, upstream (one space) and our (two
  spaces) "next startup" lines, `Recovery boot:` and `Shutdown check:` lines.
- `~/powerlog/powerlog.csv`: one line a minute (Vin, Vout, Iout, get_throttled,
  RTC time), with a `#` line per boot giving its reason.
- The throttle log (boot/shutdown `get_throttled` lines, on the bench units).
- The journal is in RAM (`Storage=volatile`) and gone after a power cut: use
  the two files above for history.
- The alarm and configuration registers, read-only:

```bash
for r in 9 10 11 17 19 21 22 27 28 29 30 32 33 34 35 47 48 49; do
  printf '%s=0x%02x ' $r $(i2cget -y 1 0x08 $r)
done; echo
```

(9/10 triggered flags, 11 last reason, 17 Default ON, 19 low-voltage and 22
recovery thresholds x0.1 V (255 off), 21 power-cut delay x0.1 s, 27-30 startup
and 32-35 shutdown alarms in BCD (s, m, h, day), 47 Default ON delay, 48 MISC,
49 guaranteed wake.)

Reference values, unit 006 on 2026-10-14 (a 15-minute bench schedule, woken at
15:30 by a power-on dip):

```
9=0x00 10=0x00 11=0x0a 17=0x01 19=0x23 21=0x46 22=0x25 27=0x00 28=0x45 29=0x15 30=0x14
32=0x00 33=0x38 34=0x15 35=0x14 47=0x0a 48=0x00 49=0x00
```

Default ON on with a 10 s delay; low voltage 3.5 V, recovery 3.7 V; power-cut
delay 7.0 s; next startup 15:45:00 and shutdown 15:38:00 on the 14th; MISC 0
(startup alarms during a halt are cached); guaranteed wake off; last reason 10.

`i2cget`/`i2cset` are in `/usr/sbin`, which isn't on the PATH of a
non-interactive `ssh` session: use the full path there.
