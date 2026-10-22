# Setup Mode: Guided Installation, Calibration and Field Debugging

**Status:** design, not yet implemented.
**Related:** issue #17 (Integrate Calibration Workflow), `docs/IMU_CALIBRATION.md`,
`docs/FIELD_DEPLOYMENT_GUIDE.md`, `docs/POWER_ANALYSIS.md`, `docs/IP_TRANSMISSION.md`.

## 1. Goal

Make installing and calibrating a node something a collaborator can do from
their own phone, without SSH or the command line, and give the dashboard a way
to turn Wi-Fi on for one node when someone needs to debug it in the field.

Constraints:

- Collaborators use a mix of devices (Android, iOS, laptops). Nothing may
  require installing an app.
- RTK equipment is part of the standard installation kit, but will not always
  be on hand. Setup must still work without it, and must say clearly when the
  result is weaker.
- Wi-Fi has two jobs: configuring and debugging nodes, and reporting. For
  reporting it is the second transport: LoRa first, then Wi-Fi, then cellular as
  the last resort (`docs/IP_TRANSMISSION.md`, #120).
- Power is tight (see `docs/POWER_ANALYSIS.md`, `docs/UNIT006_POWER_FAILURE.md`).
  Anything that keeps the Pi awake must end on its own.

## 2. What exists today

| Piece | Where | Relevant behaviour |
|---|---|---|
| IMU calibration | `tools/bno055_calibration.py` | Step-gated, but driven by terminal `input()` prompts over SSH |
| Camera capture | `tools/take_photo.py`, `tools/picam_fast.py` (Picamera2) | No live preview |
| Lens calibration | `tools/camera_calibration.py` | Uses `cv2.VideoCapture`, cannot drive the CSI camera; bench task |
| Pose from markers | Georeferencing repo (`gcp.py` `refine_pose_from_gcps`, `label_gcp.py`) | Desktop only, run days after installation |
| IP commands | `tools/transmit_ip.py` `poll_downlink()` / `apply_downlink_command()` | Node polls once per wake, so a command waits for the next scheduled wake. With the daylight schedule (`config/wittypi/watercam_daylight_2h.wpi`, #123) that is up to 2 h by day and about 16 h overnight |
| LoRa commands | `tools/lora_runtime_integration.py` | Parsed only while the Pi is awake |
| Wake from sleep | mDot-AT-firmware `CmdClassCPacketProcessor` | The mDot is Class C on the Witty Pi's always-on rail. Only the exact one-byte `!` (0x21) wakes the Pi, by pulsing the Witty Pi switch; that packet is not passed on to the Pi |
| Staying awake | `ticktalk_main.py` `call_shutdown()`, Witty Pi schedule | Two separate limits: `call_shutdown()` requests shutdown after `shutdown_iteration_limit` captures, and the Witty Pi schedule's ON window (10-15 min) ends the wake regardless. Emergency mode bypasses the first and clears the Witty Pi shutdown time (`tools/wittypi_control.py`); `tools/witty_pi_4.py` can clear a shutdown time but not set one |
| Battery level | none | Units have no state-of-charge sensor. The Witty Pi's 5 V reading is regulated and says nothing about charge, so `battery_pct` is no longer reported (#122). A real reading is planned (`docs/POWER_ANALYSIS.md`, *Battery state of charge*) |
| Network control | NetworkManager via `nmcli` | User `pi` has NetworkManager network-control for all interfaces. This is intentional and this design does not narrow it |

Two consequences shape the design:

1. **A sleeping node cannot be told anything except "emergency".** Using
   emergency mode to wake a node for debugging would also clear its schedule
   and switch it into emergency reporting, so it is not a substitute.
2. **The single-channel RFM95W forwarder is uplink-only.** Remote commands over
   LoRa need ChirpStack and a full gateway within range.

## 3. Design overview

```
 dashboard ──(17 97 maintenance on)──> API ──> ChirpStack ──> mDot ──wake──> Pi
                                       └─> IP downlink queue ────────────────┘
 Pi in maintenance mode:
   stay awake (bounded) ─ Wi-Fi up (hotspot or client) ─ setup web page ─ status uplink
 collaborator's phone ──Wi-Fi──> setup page: live view, IMU wizard, markers, pose check
```

Five parts, each useful on its own:

1. Maintenance mode on the node (section 4)
2. A maintenance command and status message (section 5)
3. A dashboard control (section 6)
4. A maintenance wake in the mDot firmware (section 7)
5. The setup web page and installation wizard (section 8)

## 4. Maintenance mode (node)

A new runtime state, separate from emergency mode.

**Entering it:**

- the maintenance command (section 5), over LoRa or IP;
- a local trigger in the field, so setup does not depend on the dashboard or
  on coverage. Candidate: entering maintenance mode automatically when the Pi
  was woken by the Witty Pi button rather than by its schedule. Whether the
  Witty Pi exposes the wake source needs checking; if it does not, a reed
  switch on a spare GPIO is the fallback;
- first boot of an unconfigured node (no `unit_config` with `setup_complete`).

**While active:**

- The emergency flags are not touched.
- The Witty Pi's shutdown time is moved to the maintenance deadline. Stopping
  ticktalk (and with it `call_shutdown()`) is not enough: the schedule's ON
  window would still end the wake after 10-15 min. Setting the shutdown time, rather than clearing it
  as emergency mode does, means the Witty Pi itself enforces the limit even if
  the Pi hangs. This needs a `set_shutdown_time()` in `tools/witty_pi_4.py`.
  On exit the normal schedule is restored.
- Wi-Fi comes up in one of two modes:
  - **hotspot:** the node runs an access point; the collaborator's phone joins
    it. Used for installation and the wizard. Each node has its own WPA2
    passphrase (printed on the unit label and visible to authorised dashboard
    users). It is not stored in this repository.
  - **client:** the node joins a known network, for example a team phone
    hotspot, and comes up on Tailscale so someone remote can use
    `tailscale ssh`. Network credentials are provisioned per node at the bench
    and kept out of the repository.
- **The capture and segmentation workflow does not run.** While the node is
  being configured over the hotspot, nothing captures, segments, compresses or
  transmits flood data: `ticktalk.service` (and the legacy `watercam.service`)
  are stopped, `segformer_daemon.service` is stopped to free memory and CPU,
  and `button.service` is stopped because it also drives the camera. The only
  camera use is the setup page's own preview and stills. Photos taken during
  setup are not flood observations and are never sent as captures. Client mode
  (remote debugging) starts with the workflow stopped too; whoever is
  debugging can start it by hand if that is what they are testing.
- The setup web page (section 8) is served on the Wi-Fi interface only.
- Because ticktalk is stopped, maintenance mode runs as its own service
  (`maintenance.service`, with `Conflicts=` on the workflow services). It keeps
  `lora_daemon.service` running, so it can still receive a maintenance-off
  command over LoRa and send the status uplink, and it does its own IP
  downlink polling. If the node boots straight into maintenance mode (local
  trigger or first boot), a runtime flag stops `ticktalk.service` from starting
  at all.
- With the workflow stopped, the only uplink is the maintenance status
  message. In hotspot mode the radio is busy as an access point, so it goes
  over LoRa, or cellular if LoRa is not joined (the order from #120 minus
  Wi-Fi).

**Leaving it**, whichever happens first:

- the requested duration expires (default 30 min, hard cap configurable,
  suggested 120 min);
- setup is marked complete in the wizard;
- an explicit "maintenance off" command;
- the Pi reports under-voltage (the flags added for the Unit 006
  investigation). A battery threshold is added once units have a real
  state-of-charge reading (#122); until then under-voltage and the time limit
  are the only protection.

On exit the hotspot (or the session's client network) goes down and Wi-Fi goes
back to its normal role as a reporting transport, joining the networks the node
knows during each wake. The maintenance service starts `segformer_daemon` and
`ticktalk` again, so capture resumes and the normal shutdown logic applies; if
the deadline was reached, the Witty Pi shutdown ends the wake instead and the
next scheduled wake starts normally.

## 5. Command and status message

**Command `17 97`, maintenance.** Unused in the API encoder, the IP command
table and the LoRa tables today.

| Byte | Meaning |
|---|---|
| 0 | Duration in minutes, 1-240; `0` = leave maintenance mode now |
| 1 | Wi-Fi mode: `0` hotspot, `1` client |

Needs adding in three places: `API/app/encoders.py`,
`tools/transmit_ip.py` `apply_downlink_command()`, and the TLV table in
`tools/lora_runtime_integration.py`.

**Status message.** Commands are acked ([SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md#command-frames)),
but an ack only says the command was applied, not what state the node is in. On entering and leaving maintenance mode
the node sends a status uplink: mode, Wi-Fi mode, SSID, IP address on the Wi-Fi
interface, minutes remaining, the Pi's under-voltage flags (and battery level
once a real reading exists), and the reason it left.

**Fix first:** `14 94` means something different on each transport. The IP
path and the API encoder treat it as the flood-code interval (minutes, 1-240);
the LoRa TLV table sets `photo_interval` in seconds. Whatever
`14 94` should mean, the two paths must agree before more commands are added.

## 6. Dashboard (API repo)

- Per-node **Enable Wi-Fi** control: duration picker, hotspot/client choice.
  Behind its own permission, logged with the user and time.
- Shows the status message: on/off, SSID and passphrase for hotspot mode, IP
  for client mode, countdown, last exit reason.
- If the node is asleep and cannot be woken (IP transport, or LoRa without a
  maintenance wake), say "queued, applies at the next wake" and show the
  expected wake time, rather than appearing to fail.
- Delivering queued commands on the next uplink (API issue #65) shortens that
  wait for IP nodes.

## 7. Maintenance wake (mDot firmware)

Today the only packet that wakes the Pi is `!`, and it is consumed rather than
passed on. Proposed:

- ~~A second wake packet, reserved for maintenance, that pulses the Witty Pi
  switch exactly like `!` but does not imply emergency.~~ Not needed: `!` is now
  only a remote start, and emergency mode is its own command
  ([REMOTE_START_AND_EMERGENCY.md](REMOTE_START_AND_EMERGENCY.md)).
- While the Pi is off (PA_6 low), the mDot holds the most recent non-emergency
  downlink instead of forwarding it to a serial port nobody is reading, and
  replays it once the Pi is up. The maintenance command then travels as a
  normal downlink right behind the wake packet.

With this, a LoRa node responds to the dashboard control within minutes.
Without it, the control still works, at the node's next scheduled wake.

## 8. Setup web page and wizard

Plain HTML and JavaScript served by a small web server on the node (Flask or
similar), tested on Android Chrome, iOS Safari and a desktop browser. It calls
the existing Python tools rather than reimplementing them.

| Step | What the collaborator sees | Backed by |
|---|---|---|
| 0. Connect | The page over the setup network (the team's phone hotspot, saved on the node with a higher priority than the site's network beforehand), or Tailscale over the site network; serial console as the fallback | NetworkManager profiles, `tailscale status` |
| 1. Check the node | Battery, radio, GPS fix, IMU and camera status; `get_throttled`, Witty Pi output voltage (pass ≥ 4.7 V; the input reading is not used, #228), the software release | `tools/initial_health_check.py`, `tools/node_version.py` |
| 2. New site or new angle | If the node is moving, or being re-aimed at the same site: back up, then clear what it learned at the old position (GPS home, registration orientation reference, flood baseline). The camera-to-camera registration stays | `tools/gps_home.py --reset`, `tools/registration_orientation --reset`, `tools/flood_baseline.py --reset` |
| 3. Magnetometer, before mounting | At a magnetically clean spot near the site (away from vehicles, the pole, fences): the figure-eight with live 0-3 bars, then a short log showing `cal=3/3/3/3` (accel 2 acceptable). Gyro and accelerometer stay from the bench | `tools/bno055_calibration.py --stages mag`, `--mode log` |
| 4. Mount and record | Mount height, a compass heading, pole material and nearby magnetic sources; the RTK camera position and ground control points (private: to the API, never the repo) | form, RTK import |
| 5. Power at the mount | A live trace of Witty Pi output and throttle flags while the collaborator tugs the power cable, nudges the mDot wiring and tilts the box; any restart or dip is flagged | Witty Pi registers, `vcgencmd`, the Witty Pi log |
| 6. Connectivity | Each path on its own, with a pass/fail and what it means: **LoRa** sub-band and a link check (margin, gateways; healthy needs ≥ 5 dB, `lora_health`), **site WiFi** (signal, the server reachable through Tailscale, no captive portal), **cellular** (one bring-up and reachability check, data noted). Summary: which transport the node will mostly use there | `tools/mdot_subband.py --check`, `lora_health.assess(force=True)`, NetworkManager, `transmit_ip.is_reachable` |
| 7. Aim the camera | Low-resolution live preview with a horizon line and range rings from the current IMU pitch/roll and mount height; warns when the view does not reach the area it should cover | Picamera2 MJPEG stream, `camera_geometry.py` / `geo_core.py` |
| 8. IMU on the pole | The existing step-gated procedure, one instruction per screen, live 0-3 calibration bars, automatic advance; the known bearing computed from the RTK camera position to a marker near the centre of the view (recomputed whenever the aim changes) | `tools/bno055_calibration.py --mode mount --known-bearing --save-correction`, then `--mode validate` |
| 9. Markers | Capture a still; tap each marker on the screen and name it; at least 4 required | port of Georeferencing `label_gcp.py` to touch |
| 10. Pose | **With RTK:** import the marker survey, refine the pose on the node, show per-marker residuals and pass/fail. **Without RTK:** save the photo, labels and IMU readings; the pose is marked *provisional* | Georeferencing `refine_pose_from_gcps`, vendored as `camera_calibration.py` already is |
| 11. First capture | One capture cycle with the shutdown held off (as `~/test_wake.sh` does on the bench): the mask and water %, the registration reference adopted at the new pose, an uplink seen by the server. The collaborator confirms normal conditions and sets the flood baseline from it | the capture graph, `tools/flood_baseline.py --set-from-latest` |
| 12. Save and deploy | Writes the node's `unit_config` and sends it (and the pose) to the API; installs the wake schedule and re-arms the Witty Pi; enables the capture service (and recovery boot, if the site wants it); lowers the setup network's priority so the site network is used for reporting; copies the session's logs; marks setup complete and leaves maintenance mode | `unit_config` loader, `runScript.sh`, systemd, NetworkManager |

Every step records its result. The summary page lists anything skipped or
failed, and the dashboard shows the node as installed with those noted, so a
partial install (no RTK, a failed transport test) is visible rather than
silent.

**Bench phase: before going to the site.** The same pages, opened at the bench,
also walk an installer through the bench work in `FIELD_DEPLOYMENT_GUIDE.md`
Phase 1, so a non-technical installer doesn't need the guide or a terminal.
Steps that need team credentials are done by an admin first; the page shows
them as done, or as waiting for the team, and doesn't let the installer skip
past them.

| Bench step | What the page does | Who | Guide / backed by |
|---|---|---|---|
| B1. Identity | Hostname, a unit-specific `pi` password, a unique machine ID | admin | 1.2, 1.2.1 |
| B2. Remote access | Tailscale joined with the node tag; the dashboard sees the node | admin | 1.3 |
| B3. Software | Current release, verified signature, pickle matches; packages and boot settings checked | installer (one button) | 1.3.1-1.3.3, `docs/RELEASES.md` |
| B4. Credentials | The node's IP token installed; the LoRa device registered in ChirpStack | admin | 1.5, 1.6, `tools/set_ip_token.py` |
| B5. mDot | Firmware version and sub-band checked and corrected; watchdog and Class C settings; a join | installer (one button) | `tools/mdot_subband.py`, the flash tool |
| B6. Power wiring | Photos and a checklist for the P2 feed, the mDot wiring and, on units without the PCB, the taped MOSFET and its pull-down; then a live trace while the installer tugs and tilts (as field step 5) | installer | 1.11.1, `docs/WITTYPI_P2_POWER_HARNESS.md` |
| B7. IMU gyro and accelerometer | The six faces at 90°, one per screen, with live bars and where gravity points; saved only when good enough (#229) | installer | 1.7, `tools/bno055_calibration.py --stages gyro,accel` |
| B8. Camera | Lens calibration present; a test photo in focus | installer | 1.8 |
| B9. Full cycle | Health check, then one capture cycle with the shutdown held off: captures, segmentation, an uplink the server saw | installer | 1.10, 1.11 |
| B10. Site prep | If the unit has been deployed before: back up and clear the old site's learned values (field step 2 can do it at the site instead) | installer | 1.7.1 |
| B11. Pack | Save the team's setup network (phone hotspot) with a higher priority; weatherproofing done; a packing list (serial adapter, meter, tape, RTK, towel) | installer | 1.12 |

The bench summary must be all green, or show only the admin steps as pending,
before the page offers to switch to the field steps.

**Opening the page.** When the node runs its own setup hotspot (maintenance
mode, #117), it answers DNS for every name with its own address, so a phone
joining the hotspot pops the page up as a captive portal and the installer
never types an address. Over the team's phone hotspot or Tailscale, it's a
bookmark the dashboard links to.

A provisional pose shows as such on the dashboard until it is verified, either
by a return visit with RTK or by matching the saved photo against reference
imagery later. A node never silently runs on an unverified heading.

**Data handling.** Unit configs and marker surveys contain the node's exact
position. They go to the private API and stay on the node; they are never
committed to this repository.

**Bench work stays at the bench, except the magnetometer.** Lens calibration
and the IMU's gyroscope and accelerometer (the six faces at 90°, #229) are done
before shipping. The magnetometer can't be: bench offsets are wrong outdoors, so
it is calibrated at a clean spot near the site (step 3), before mounting.

The steps follow the bench-tested manual procedure used for 006 (2026-10-21/22);
until the wizard exists, that procedure is the field checklist.

## 9. Implementation order

1. Maintenance mode with a local trigger, the hotspot, and the time and
   battery limits (node only). Usable immediately for installation. (#117)
2. Agree on `14 94` (#116); add `17 97` and the status message (#118) and the
   dashboard control (WaterCam-Team/API#109). Works within today's wake latency.
3. Maintenance wake and downlink replay in the mDot firmware
   (WaterCam-Team/mDot-AT-firmware#3).
4. Wizard pages, starting with the checks the manual field procedure already
   runs by hand (node, power and connectivity: steps 1, 5 and 6) and the
   magnetometer (step 3), then the live view (step 7) and the IMU on the pole
   (step 8), then markers and pose (steps 9-10), first capture and save (steps
   11-12). (#119)

## 10. Open questions

- Can the Pi tell whether the Witty Pi button or the schedule woke it?
- Which wake byte is free for maintenance, given what else is ever sent as a
  one-byte downlink?
- Until units have a state-of-charge reading, should maintenance mode refuse
  to start, or shorten itself, after a run of under-voltage flags?
- Is the hotspot's extra draw acceptable for a 30-120 min session at the
  battery levels seen in the field? Measure on a bench unit.
- Client Wi-Fi outside setup is settled: it is the second reporting transport,
  joined during each wake, so it follows the wake schedule and needs no duty
  cycle of its own. The setup session's network (a team phone hotspot) is saved
  with a higher priority for the visit and lowered at step 12, so the site's
  network is the one used for reporting afterwards.
