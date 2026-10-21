# Witty Pi P2 Power Harness

**Written:** 2026-10-19, after unit 006's power cuts were traced to its Witty Pi
USB-C socket. This note replaces the USB-C plug at the Witty Pi with wires
soldered to the Witty Pi's P2 power header.

## Why

A unit is powered by a Y-cable that joins both USB-A ports of the Voltaic
battery into the Witty Pi's USB-C socket. On 006 the socket itself was faulty:
the unit lost power when the plug was pushed in or moved side to side, with two
different wall adapters and cables, and once with nobody touching it. Tilting
the box (forward or backward) did the same. Each time the Witty Pi logged
"System starts up because power supply is newly connected" (action reason
0xA), with no voltage sag beforehand.

A friction-fit USB-C plug is a weak point on a pole-mounted unit, even on a
good socket. Feeding the Witty Pi through P2 with soldered wires removes the
socket from the power path.

## The P2 header

P2 is an unpopulated 3-pin header on the right edge of the Witty Pi 4. Its pins
are, in order, **5V**, **GND** and **VIN**, with **5V** closest to the Raspberry
Pi's GPIO header. The Witty Pi 4 manual (section 11.1) says 5 V may be fed
between **5V** and **GND**.

> **Never connect the battery to VIN.** VIN goes to the on-board DC/DC
> converter, which needs 6–30 V.

## Parts

- A Y-cable with two USB-A plugs: the unit's own (USB-C end) or an older one
  with a micro-USB end. The far end gets cut off, so its type doesn't matter.
- Heat-shrink, solder and flux.
- A cable tie and an adhesive tie mount, or a tie to a standoff, for strain relief.
- Kapton or electrical tape to cover the USB-C socket.
- Multimeter.
- Optional: a 2-pin latching connector at 2.54 mm pitch (Molex KK 254 or
  JST XH) instead of soldering the wires straight to the pads. It makes the
  Witty Pi easier to swap, but needs a crimp tool.

If the Y-cable's power wires are thinner than 22 AWG (common on micro-USB cables), keep its USB-A ends and
join them to a short run of 20 AWG wire instead.

## Build

1. **Power off and disconnect.** Shut the unit down, then unplug both USB-A
   plugs from the battery. Remove the HAT and lift the Witty Pi off the Pi, so
   heat and flux stay away from the Pi.
2. **Cut the far-end plug (USB-C or micro-USB) off the Y-cable,** leaving enough cable to reach P2
   with some slack.
3. **Identify the wires with the meter,** not by colour. Plug the USB-A ends
   into the battery and measure: 5 V wire to ground wire should read about
   +5.1 V. Then plug in **one USB-A leg at a time** and measure again: both
   legs must carry 5 V. Some older Y-cables carry power on only one leg (the
   other is data only), which would leave the unit on a single 2 A port.
   Unplug the battery again before going on.
4. **Insulate every other wire** (data lines, shield) individually with
   heat-shrink. Don't join them to each other or to anything else.
5. **Check P2 is connected to the input.** With the meter on continuity, check
   that P2's **5V** pad connects to the USB-C socket's VBUS pin, and P2's
   **GND** pad to the socket shell or a GND pin. If the socket damage has
   broken a trace, this shows it.
6. **Solder** the 5 V wire to the **5V** pad and the ground wire to the
   **GND** pad, from the top of the board. Leave VIN empty. Slide heat-shrink
   over each joint if it fits, or cover the joints with a dab of hot glue.
7. **Strain relief:** tie the cable down within a few centimetres of P2, so a
   pull on the cable can't reach the solder joints.
8. **Cover the USB-C socket** with tape and write "do not use" on it.
   Plugging a charger in there would feed it against the battery.
9. **Check before stacking:** plug the USB-A ends back into the battery and
   measure at the P2 pads: about +5.1 V, positive on **5V**. Unplug again.
10. **Reassemble:** Witty Pi back on the Pi (press it fully onto the header),
    HAT on top, then plug the battery in. The Witty Pi's Default ON setting
    starts the Pi.

## Secure the mDot wiring

On units where the mDot is wired with jumper wires, those wires need the same
care as the power harness. The mDot is powered from the Witty Pi's P3 header
(3V3 and GND). If its GND jumper comes loose, the mDot resets (seen on 005),
and while the ground is open its current flows through the Pi's GPIO pins.

- **Check the grounds match:** with the meter on continuity, P2 **GND** and
  P3 **GND** should be connected (both are the Witty Pi's board ground). Then
  the harness doesn't change the mDot's ground.
- **Replace the 3V3 and GND jumpers** with soldered wires, or crimp them into a
  latching housing. If that has to wait, at least group the jumpers into a
  multi-pin housing and fix it with a dab of hot glue.
- **Secure the switch MOSFET in place.** On jumper-wired units the MOSFET that
  presses the Witty Pi switch (gate on the mDot's PB_1, a 10 kΩ pull-down from
  gate to GND, source to GND) hangs on its leads and jumpers. Moving it, or the
  wires around it, cut 006's power three times on 2026-10-21: once as the PB_1
  wire was connected, twice when the wires at the Pi's GPIO header were touched.
  Each time the Witty Pi logged no shutdown, only "power supply is newly
  connected" at the next start, so the whole board lost power, most likely from a
  contact making and breaking at a jumper. Tape or hot-glue the MOSFET to the
  board or case, put heat-shrink over its leads and the resistor's joints, and
  keep its wires from moving. Taped in place, 006 then stayed up through half an
  hour of handling in every orientation (the IMU calibration's six faces).
- **Connect and disconnect the mDot's wires only with the unit off,** and
  check for a short before powering it: with the meter on resistance, the Pi's
  5V pins (2, 4) to GND (pin 6) should never read near 0 Ω, even while you
  wiggle the wires. If a wire must go on live, connect GND first and 3V3 last.
- **Expect an mDot reset** whenever power is disconnected for this work. It
  rejoins LoRa afterwards.

## Test

1. **Wiggle:** with the unit running, pull and bend the harness near P2 and at
   the battery end.
   Give each mDot jumper a light tug too, and nudge the MOSFET; an mDot reset
   shows up as a LoRa rejoin in `journalctl -u lora_daemon`, not in the Witty Pi
   log, while a lost supply shows up there as "power supply is newly connected".
2. **Tilt:** with the lid closed, tip the box forward, backward and to each
   side, and hold it at the mounting angle for a minute.
3. **Check for cuts:** after the test, look for a new boot:

   ```
   grep -a "System starts up because" ~/wittypi/wittyPi.log | tail -5
   grep -a "logger start" ~/powerlog/powerlog.csv | tail -3
   ```

   Any "power supply is newly connected" entry during the test means the cut
   is still there.
4. **Check the reading under load:** `~/wittypi/wittyPi.sh` shows the input
   and output voltage. Compare them with the readings before the change, not
   as absolute volts.

## Later

- Check the USB-C socket on every other unit the same way (push and
  side-to-side wiggle while watching for a new boot), and consider the P2 feed
  for all of them.
- A 1000 µF polymer capacitor across 5V and GND at the P2 end of the harness
  is the input bulk capacitor proposed in the HAT design notes
  (pcb-designs `WaterCam_PCB_Design_Changes_Required.md`, section 19).
