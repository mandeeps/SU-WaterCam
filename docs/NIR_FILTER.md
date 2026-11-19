# IR-cut (NIR) filter: how it is driven, testing it, expected lifetime

The optical camera is a Dorhea OV5647 IR-cut camera. A small motorised filter
slides in front of the sensor to block near-infrared (NIR), or out of the way
to let it through. Each capture takes one frame with the filter in (NIR-OFF)
and one with it out (NIR-ON); their difference is the NIR band.

## How the filter is driven

The camera's light sensor (photoresistor) is removed and a wire runs from its
pad (third point from the bottom on the back of the board) to Pi pin 40,
GPIO 21 (README, "Optical Camera"). The Pi does not drive the filter coil: it
sets the level the light sensor used to set, and the camera board's own
circuit moves the filter.

`tt_take_photos.take_two_photos()` (the capture the app runs; also
`tools/take_nir_photos.py` and `button_hold_camera.py`):

1. GPIO 21 low: filter **in**. Wait 2.5 s (the filter needs about 0.6 s, the
   rest is for auto white balance and exposure), lock both, take the NIR-OFF
   frame.
2. GPIO 21 high: filter **out**. Wait 0.5 s (the filter needs about 0.33 s),
   take the NIR-ON frame at the same locked settings.
3. Release the pin (input, no pull). The camera board's pull-up holds it high,
   so the filter stays out for the rest of the wake.

So every capture is one filter cycle (in, then out), except that the first
capture after boot usually starts with the filter already in (next section).

### The resting position depends on whether a capture has run since boot

| When | GPIO 21 (`pinctrl get 21`) | Filter |
|---|---|---|
| From boot until the first capture | `ip pd \| lo`: input, the Pi's pull-down, reads low | in |
| During NIR-OFF / NIR-ON | `op \| lo` / `op \| hi` | in / out |
| After any capture, for the rest of the boot | `ip pn \| hi`: input, no pull, reads high | out |

The Pi's reset default for GPIO 21 is a pull-down, and it is stronger than the
camera board's pull-up, so after boot the line reads low. Releasing the pin
from Python (gpiozero) clears the pull, and the board's pull-up then wins. No
`config.txt` or kernel command-line setting is involved. Seen on 005 after three
reboots (2026-11-16 to 2026-11-18). A photo check on 2026-11-19 confirmed the
filter position: with the pin untouched for 13 h after boot, the frame matched
one with the pin driven low (mean red 40.0 in both, at locked exposure), and
driving the pin high raised it to 76.9.

This does not change the frames, because the capture drives the pin before
each one. It matters when checking a unit by hand:

- GPIO 21 reading **low while released** right after boot is normal.
- GPIO 21 reading **low while driven high** (`op | hi` set, `lo` read back) is a
  fault: something on the camera side holds the line to ground, as on 007 in
  November 2026.

Measured move times (005, 2026-11-18, 27 moves each way, to within one 33 ms
frame): in 0.56-0.60 s, out 0.30-0.33 s. Code that moves the filter in and
then takes a frame should wait at least 0.7 s.

`tools/nir_filter_check.py` compares the two frames' mean red, green and blue
and reports `nir_filter_status` (0 moved, 1 not) when the NIR-OFF frame is lit.

## Indoor bench tests can report a working filter as stuck

The check only knows a frame is *lit*, not that the light contains NIR. That
holds outdoors: daylight carries plenty of NIR, and a working filter changes
the frame's means by roughly 13-145 (of 255). Indoors it often does not hold:

- LED and fluorescent room lights emit almost nothing above about 700 nm, the
  band the filter blocks. With the filter in or out the frames come out nearly
  the same, so the check reports `1` (not moved) for a working filter.
- Low-E window glass reflects much of the NIR in daylight, so a unit lit
  through a window can do the same.

From the frames alone a stuck filter and a scene without NIR look the same.
A `nir_filter_status = 1` from a unit on the bench, or any capture lit by room
light, is not evidence of a fault.

To test a filter on the bench:

1. Light the scene with NIR: take the unit outside in daylight, or use an
   incandescent/halogen lamp or an 850 nm IR illuminator. Room LEDs will not do.
2. Run a capture and compare the pair (`python -m tools.nir_filter_check <capture dir>`
   from `~/SU-WaterCam`), or toggle the pin by hand and watch the preview:
   `pinctrl set 21 op dl`, then `pinctrl set 21 op dh`. With the camera
   covered you can also listen for the click.
3. A working filter differs by well over the 5.0 threshold; one that really is
   stuck stays under it in daylight.
4. Only then look at the hardware: the GPIO 21 wire and its solder joint,
   the voltage across the filter's two wires while toggling the pin (it should
   reverse polarity), and the coil's resistance with its connector unplugged
   (an open circuit means a burnt coil).

## Expected lifetime

Dorhea publishes no lifetime or switch-count figure for this filter, and no
schematic for the drive circuit. The nearest published figures are for
comparable M12 IR-cut switchers (Commonlands CLA214/CLA219): at least
**50,000 cycles** guaranteed (often over 100,000), driven by a 3.3 V pulse of
100-500 ms with the polarity reversed to move back. Treat 50,000 as an
estimate for our part, not a rating.

At one cycle per capture:

| Capture rate | Cycles per year | Years to 50,000 cycles |
|---|---|---|
| Monitoring every 60 min (default), 24/day | 8,760 | about 5.7 |
| What units actually manage (wakes skipped on low power), about 11/day | about 4,000 | about 12 |
| Emergency every 10 min, 144/day, if it ran all year | 52,560 | about 1 |

Emergency mode runs only during an event, so in practice switching wear should
take several years, longer than other parts of a unit are expected to last. A
filter that stops within months (006's stopped after about three months of
hourly captures, at most about 2,000 cycles) has not worn out from switching. Look
instead at the wiring, moisture or corrosion, dirt in the slider, or heat.

Unknown: whether the camera board pulses the coil or keeps it energised for as
long as GPIO 21 holds a level. If it keeps it energised, the coil carries
current for the whole wake (the line rests high after the first capture, low
before it), and heat in a sealed summer
enclosure, not cycle count, would limit its life. Step 4 above (watch whether
the voltage across the coil stays on while the pin is held) answers this; if it
does, a pulsed driver or switching the filter's supply would be the fix.
