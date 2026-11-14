# Bench hardware tests

`tests/hardware/` checks a bench unit's real devices: that each one answers and
gives a plausible reading. The rest of `tests/` checks the software and must give
the same result on any machine, so it never touches a node's hardware, files or
server (`tests/conftest.py`, `_no_real_node`). The two are kept apart on purpose:
a unit test that reaches real hardware tests nothing about it and can leave it
changed (on 2026-11-12 the suite, run on 005, armed a Witty Pi shutdown alarm for
the next day).

## Running them

On a bench unit, from `~/SU-WaterCam`, with ticktalk stopped:

```bash
sudo touch /etc/watercam/bench-unit     # once per bench unit; never on a deployed unit
venv/bin/python -m pytest --hardware -rs tests/hardware
```

`--hardware` runs only `tests/hardware`, with the real drivers in place of the
test stubs. The run stops at once on anything but a Raspberry Pi, on a unit without
`/etc/watercam/bench-unit`, or while ticktalk is running (it owns the camera, the
Witty Pi and the IR-cut pin). Without `--hardware`, these tests are skipped.

`-rs` lists the skipped tests: a device the unit doesn't have (no modem, no AHT20)
is skipped and named, so the report shows what wasn't tested. A device that is
fitted but wrong fails.

The run takes under a minute. Run it after a hardware change, when a unit comes
back from the field, and before a release (RELEASES.md, bench checks).

## What they check

| Test | Checks | Side effects |
|---|---|---|
| Witty Pi rails, firmware, I2C | answers at 0x08; output 4.7-5.4 V, input above 3 V, temperature plausible | none |
| RTC | readable, oscillator running, within 2 min of system time | none |
| Pi throttling | no under-voltage, capping or throttling now, and no under-voltage since boot | none |
| Battery | a real source answers (ADS1115, INA260 or Witty Pi output) | the INA260 state file goes to tmp |
| IMU | BNO055 at 0x28 answers with chip ID 0xA0 | none (no calibration write) |
| AHT20 | temperature and humidity in range | none |
| Optical camera | libcamera sees it | none |
| IR-cut filter | a production NIR pair differs by at least `SAME_MAX_DIFF` (tools/nir_filter_check.py) | drives GPIO 21, two photos into tmp |
| Lepton | the capture binary returns a non-blank 160×120 frame | frame into tmp |
| SegFormer daemon | segments a small synthetic five-band image | image and mask in tmp |
| LoRa daemon, mDot | the daemon answers; `AT+NJS?` says joined | none: a local AT query, nothing transmitted |
| gpsd, GNSS | gpsd answers; with the modem powered, it sees the receiver | none |

Nothing transmits over LoRa or IP, writes a Witty Pi alarm, or changes the unit's
configuration.

The IR-cut test needs a lit scene: below a frame mean of 20 it skips rather than
judge the filter. "Not joined" means the mDot has no saved session: its keys,
sub-band or a join that never succeeded, not just a gateway out of range.

## Adding a test

Put it in `tests/hardware/`, give it a plausible-range assertion rather than a
smoke check, and skip with `need(...)` when the device may not be fitted. A test
that must change hardware state (an alarm, USB power) saves the current value,
restores it in a `finally`, and checks the restore with a fresh read. Never
transmit.
