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

- `call_shutdown()` is skipped, without touching the emergency flags.
- The Witty Pi's shutdown time is moved to the maintenance deadline. Skipping
  `call_shutdown()` alone is not enough: the schedule's ON window would still end
  the wake after 10-15 min. Setting the shutdown time, rather than clearing it
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
- The capture pipeline pauses so the setup page can use the camera.
- The setup web page (section 8) is served on the Wi-Fi interface only.
- In hotspot mode the radio is busy as an access point, so Wi-Fi is not
  available for reporting; uplinks fall through to LoRa or cellular in the
  usual order (#120).

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
knows during each wake. Normal capture resumes and the normal shutdown logic
applies again.

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

**Status message.** The IP downlink queue marks a command delivered as soon as
the node fetches it, and there is no acknowledgement, so the dashboard cannot
tell whether a command took effect. On entering and leaving maintenance mode
the node sends a status uplink: mode, Wi-Fi mode, SSID, IP address on the Wi-Fi
interface, minutes remaining, the Pi's under-voltage flags (and battery level
once a real reading exists), and the reason it left.

**Fix first:** `14 94` means something different on each transport. The IP
path and the API encoder treat it as the flood-code frequency (an index into
10-60 min); the LoRa TLV table sets `photo_interval` in seconds. Whatever
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

- A second wake packet, reserved for maintenance, that pulses the Witty Pi
  switch exactly like `!` but does not imply emergency.
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
| 1. Check the node | Battery, radio, GPS fix, IMU and camera status | existing health checks |
| 2. Aim the camera | Low-resolution live preview with a horizon line and range rings from the current IMU pitch/roll and mount height; warns when the view does not reach the area it should cover | Picamera2 MJPEG stream, `camera_geometry.py` / `geo_core.py` |
| 3. IMU on the pole | The existing step-gated procedure, one instruction per screen, live 0-3 calibration bars, automatic advance | `tools/bno055_calibration.py` stage 2 |
| 4. Markers | Capture a still; tap each marker on the screen and name it; at least 4 required | port of Georeferencing `label_gcp.py` to touch |
| 5. Pose | **With RTK:** import the marker survey, refine the pose on the node, show per-marker residuals and pass/fail. **Without RTK:** save the photo, labels and IMU readings; the pose is marked *provisional* | Georeferencing `refine_pose_from_gcps`, vendored as `camera_calibration.py` already is |
| 6. Save | Writes the node's `unit_config`, sends it to the API, marks setup complete and leaves maintenance mode | `unit_config` loader |

A provisional pose shows as such on the dashboard until it is verified, either
by a return visit with RTK or by matching the saved photo against reference
imagery later. A node never silently runs on an unverified heading.

**Data handling.** Unit configs and marker surveys contain the node's exact
position. They go to the private API and stay on the node; they are never
committed to this repository.

**Bench work stays at the bench.** Lens calibration and IMU stage 1 are done
before shipping, so nodes reach the field already calibrated and collaborators
only do the on-pole steps.

## 9. Implementation order

1. Maintenance mode with a local trigger, the hotspot, and the time and
   battery limits (node only). Usable immediately for installation. (#117)
2. Agree on `14 94` (#116); add `17 97` and the status message (#118) and the
   dashboard control (WaterCam-Team/API#109). Works within today's wake latency.
3. Maintenance wake and downlink replay in the mDot firmware
   (WaterCam-Team/mDot-AT-firmware#3).
4. Wizard pages, starting with the live view (step 2) and the IMU wizard
   (step 3), then markers and pose (steps 4-5). (#119)

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
  cycle of its own. Still open: whether the setup session's client network (for
  example a team phone hotspot) should stay among the networks used for
  reporting afterwards.
