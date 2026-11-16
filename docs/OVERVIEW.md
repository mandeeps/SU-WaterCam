# The node from wake to shutdown

A map of what one WaterCam unit does, in order, and where each part lives in
this repository. It points to the detailed docs and does not repeat them. Each
statement was checked against the code on 2026-11-14.

A unit sleeps most of the time. The Witty Pi powers the Pi on at scheduled
times. The Pi takes photos, makes a flood mask, sends the result, and shuts
itself down.

## 1. Hardware at a glance

| Part | Role | Code |
|---|---|---|
| Raspberry Pi 4B | runs everything below | |
| Witty Pi 4 | RTC, wake schedule, cuts power to the Pi while it sleeps | `tools/witty_pi_4.py`, `tools/wittypi_control.py` |
| Camera with a motorised IR-cut (NIR) filter (a generic third-party CSI camera, not an official Raspberry Pi camera) | two frames per capture, filter in and out | `tt_take_photos.py`, [NIR_FILTER.md](NIR_FILTER.md) |
| FLIR Lepton 3.5 thermal camera | thermal band | `tt_take_photos.flir`, `lepton/` |
| BNO055 IMU, AHT20 temperature and humidity (no unit uses a BNO085; it is being evaluated: same sensor, different firmware that reports some values more directly, and a driver exists) | pose, temperature, humidity | `tools/bno055_imu.py`, `tools/aht20_temperature.py` |
| Quectel modem with GNSS | cellular uplink and GPS, normally powered off | `tools/usb_power.py`, `tools/get_gps.py`, `tools/gps_window.py` |
| MultiTech mDot (LoRaWAN, firmware in the separate mDot-AT-firmware repository, private) | LoRa uplink, remote start, emergency command; stays powered while the Pi is off (see [Battery wiring](#battery-wiring)) | `tools/lora_daemon.py` |
| USB microphone (not in the standard parts list; being evaluated for acoustic environmental monitoring) | audio segments | `tools/audio_recorder.py` |
| Voltaic V75 battery pack with solar panel | power | [POWER_ANALYSIS.md](POWER_ANALYSIS.md), [Battery wiring](#battery-wiring) |

Two cameras in total: the visible camera (with the switchable IR filter) and the Lepton.

### Battery wiring

- **Witty Pi feed:** a Y-cable joins the battery's two USB-A ports (3 A combined) into the Witty Pi's input. It powers only the Witty Pi (and through it the Pi). Plugging a USB-A cable into the V75 engages its Always On mode; unplugging returns it to Auto Off.
- **Modem:** runs from the Pi's USB, so it draws nothing while the Pi is off.
- **Measuring the off-state draw:** a meter at the battery's USB-A output sees the Witty Pi and the mDot together (not the V75's own ~7 mA); measure in series with the mDot's 5 V lead to separate it ([POWER_ANALYSIS.md](POWER_ANALYSIS.md)).
- **mDot:** fed with 5 V into its pin 1, so it stays powered while the Pi is off. Hand-wired units take it from the Witty Pi's P2 5 V input, with no diode (the planned board adds a Schottky). This is the standard for every unit (005 and 006 so far); the older wiring took the Witty Pi's always-on 3.3 V rail, which measures about 3.0 V, below the mDot's minimum (pcb-designs `Schematic_Remaining_Work.md` #20).
- **Solar:** into the V75's 3.5 × 1.1 mm barrel jack.
- **USB-C ports:** empty when deployed, because the end (PD) port disables Always On. The side USB-C port is fine for charging on the bench. The battery's charge signal is exposed only on the PD port, so reading it without turning Always On off is still being investigated.
- **006:** power reaches the Witty Pi through wires soldered to its P2 header ([WITTYPI_P2_POWER_HARNESS.md](WITTYPI_P2_POWER_HARNESS.md)).


No deployed unit has a custom circuit board: every unit is hand-wired. The HAT
in the `pcb-designs` repository is the planned board, not the one in the field.
Build steps are in [README.md](../README.md); the install of the software is
[FIELD_DEPLOYMENT_GUIDE.md](FIELD_DEPLOYMENT_GUIDE.md).

## 2. Wake and boot

1. The Witty Pi powers the Pi on at the next slot of `~/wittypi/schedule.wpi`.
   The schedule follows the sun by default and can be set from the dashboard per
   node or per neighbourhood (command `15 95`, `tools/schedule_control.py`,
   `tools/sun.py`); if the node has no position it uses a fixed fallback window.
   See [WITTYPI_SCHEDULING.md](WITTYPI_SCHEDULING.md) and
   [SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md#wake-schedule-15-95).
2. Early in boot, `rtc-early-clock.service` copies the Witty Pi RTC into the
   system clock, and `usb-power-off.service` turns USB (modem, microphone) off.
3. `wittypi-boot-mark.service`, then the Witty Pi's own daemon, then
   `wittypi-recovery.service`. The recovery service looks at why the Pi booted.
   If power simply returned after an outage, it arms a wake at least 2 hours
   away and shuts down before the cameras and modem start, so the battery can
   recharge (`recovery_boot` in `runtime_config.json`). A button press or a
   scheduled wake goes on to the normal cycle.
4. `ticktalk.service` starts. Before the app it runs
   `tools/wait_for_wittypi_schedule.py`, which holds the start until the Witty
   Pi has armed the next wake (so a power dip during boot cannot leave the unit
   with no wake armed), and `tools/schedule_control.py --refresh`, which moves
   a sun-following schedule if the sun has moved enough.
5. The Witty Pi's script arms its alarms only at boot, so
   `wittypi-next-wake.service` re-checks the startup alarm at every shutdown.
   More in [UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md) and
   FIELD_DEPLOYMENT_GUIDE 1.9.

## 3. Services

All unit files are in `config/`. FIELD_DEPLOYMENT_GUIDE 1.9 copies and enables
them; the installed copies do not update on `git checkout`, so a release's notes
say when to copy them again.

| Service | What it does |
|---|---|
| `ticktalk.service` | runs the capture application (section 4); `WatchdogSec=15min`, `Restart=on-failure` |
| `lora_daemon.service` | the only owner of the mDot serial port; every LoRa send or receive goes through its socket. Starts before ticktalk |
| `segformer_daemon.service` | keeps the segmentation model loaded; ticktalk asks it over a socket. Starts before ticktalk |
| `wittypi-boot-mark`, `wittypi-recovery`, `wittypi-next-wake` | Witty Pi boot and wake handling (section 2) |
| `rtc-early-clock`, `usb-power-off` | early clock, USB off |
| `max-awake.timer` | hard maximum awake time (section 8) |
| `powerlog.service` | one power log line a minute |
| `quectel-gnss.service` | enables the modem's GNSS when its AT port appears |
| `watercam-firewall`, `watercam-cellular-filter`, `watercam-cellular-usage` | input firewall, cellular traffic filter, cellular byte counter |
| `remote-debug.timer`, `.path`, `.service` | a remote Tailscale session over cellular, requested over LoRa ([REMOTE_DEBUG_SESSION.md](REMOTE_DEBUG_SESSION.md)) |

`gps.service` (replaced by `quectel-gnss.service`), `button.service` and
`filebrowser.service` are not part of a fixed node's install (see the guide).

## 4. The TickTalk graph and the compiled pickle

The application is a TickTalk graph. `ticktalk_main.py` defines the steps
(`@SQify`), one periodic step (`get_time`, `@STREAMify`, period 60 s) and the
graph `ttmain` (`@GRAPHify`). `ticktalk.service` runs
`runrtm.py ... output/ticktalk_main.pickle`: **nodes run the compiled pickle,
not the source.** After editing `ticktalk_main.py`, rebuild with
`python compile.py ticktalk_main.py --out output` and commit both files in
`output/` (the pickle and its `.sha256`). The service sets
`TT_PICKLE_POLICY=enforce`, so the runtime refuses network messages that carry
types outside an allowlist (`ticktalkpython/SafeUnpickle.py`).

TickTalk runs each step in its own process with only that function's body, so
steps import what they need inside the function and shared logic lives in
`tools/`. The runtime is the copy in `ticktalkpython/`.

## 5. Capture, segment, send

One pass of the graph, in order (`ttmain` in `ticktalk_main.py`):

1. **Setup.** `initialize_lora_integration` sets up the runtime-parameter integration (`tools/lora_runtime_integration.py`).
   `ip_downlink_poll_and_apply` fetches queued server commands over IP, so a
   changed threshold or frequency applies to this pass. `validate_configuration`
   and `adaptive_monitoring` follow.
2. **Capture.** `get_time` makes `images/<timestamp>/`. `record_audio` starts
   an audio segment if enabled. `take_two_photos` (`tt_take_photos.py`) takes
   the NIR-filter-in and filter-out frames with the IMU and GPS saved into the
   file metadata; `flir` takes the thermal frame. Notes:
   [CAPTURE_PIPELINE_NOTES.md](CAPTURE_PIPELINE_NOTES.md).
3. **Co-register.** `coregistration` (`tools/coreg_multiple.py`) aligns the
   frames into a five-band TIFF (`color_preserved_5_band.tiff`) and checks the
   registration against the unit's current orientation
   (`tools/registration_orientation.py`).
4. **Segment.** `segformer` asks `segformer_daemon` for a mask
   (`..._segmentation.png`). Section 6.
5. **Compress.** `compress_bitmap` (`tools/compress_segmented.py`,
   `tools/bitmap_cm.py`, `tools/bitmap_fit.py`) turns the mask into the flood
   bitmap. Format: [BITMAP_DECODING_SPEC.md](BITMAP_DECODING_SPEC.md),
   [BITMAP_SF_ADAPTIVE_COMPRESSION.md](BITMAP_SF_ADAPTIVE_COMPRESSION.md).
6. **Send over LoRa.** `lora_token_with_tracker`. Section 7.
7. **Send over IP.** `ip_uplink_transmit`. Section 7.
8. **Shutdown check.** `call_shutdown`. Section 8.

A capture that lacks a band (no thermal frame, or the IR-cut filter did not
move) is marked in `capture_status.json` (`tools/capture_status.py`,
`tools/nir_filter_check.py`) and still goes through the steps where it can.

### Capture details

- **Frames and resolution.** `take_two_photos` shoots both visible frames with
  Picamera2 at the OV5647's full 2592×1944. It first lets exposure and white
  balance settle with the IR-cut filter in (GPIO 21 low), locks them, takes
  `NIR-OFF.jpg`, then moves the filter out (GPIO 21 high, 0.5 s to settle) and
  takes `NIR-ON.jpg` with the same settings, so the two frames differ only by the
  filter. The setup photos are taken at the same size: the reference photo in
  `tools/field_setup_steps.py` and the deployment guide's manual step both use
  2592×1944, and the camera calibration (`tools/calibrate_node.py`) is made at it
  too. A photo at another size (`rpicam-still` with no size, as in
  `tools/fast_photo.sh`) would not match the calibration.
- **Working sizes.** Co-registration undistorts the visible frames with the
  unit's calibration and halves anything over 1000 px, so it works at 1296×972.
  `color_preserved_5_band.tiff` (red, green, blue, thermal, NIR) stays at
  1296×972 and is what the model reads; `final_5_band.tiff` is a 512×512 legacy
  copy, not for models.
- **Registration transform.** The thermal frame (moving) is registered to the
  filter-in visible frame (fixed) with an affine transform, Mattes mutual
  information and multi-resolution gradient descent (SimpleITK). One transform
  serves every capture: it is cached next to the capture folders as
  `images/registration_transform.json` plus `.tfm`, and reused while it is
  compatible with the image sizes and transform type. A new solve happens only
  when there is no usable cache or `--position-changed` is given. Because solves
  of one scene spread over about 100 px, a new solve is cached only if it lies
  within 25 px of the cached one (no metric threshold yet); otherwise the capture
  uses the cached transform. To set the transform deliberately, run
  `tools/seed_registration_cache.py` on a few scenes with good thermal contrast:
  it solves each several times and caches the medoid. `--no-drift-check` accepts
  a new solve after a camera is remounted on purpose. With no thermal frame, the
  capture skips registration and the thermal band is zero.
- **Orientation check.** `tools/registration_orientation.py` stores the IMU pose
  the cached transform belongs to and compares each capture with it: tilt (angle
  between gravity vectors, more than 5°) and rotation (quaternion angle, more than
  15°, counted only when the magnetometer is calibrated). The result is
  `registration_status` (field code `01 08`: 0 ok, 1 shifted, 2 adopted, 3 no
  cache, 4 no IMU), and the dashboard shows "Check alignment" while it is 1. It
  never re-solves: a turned node barely changes the transform (both cameras are
  bolted together), and an automatic re-solve could swap a good transform for an
  outlier. After checking the alignment by eye, clear the flag with
  `python -m tools.registration_orientation --reset`, or re-seed.
- **NIR band.** `extract_nir_band` takes the red channel of the undistorted
  `NIR-ON` frame minus the red channel of the `NIR-OFF` frame (8-bit, negative
  values clipped to 0) and resizes it to the working size.

## 6. Which model runs

`segformer_daemon.service` loads one ONNX model at start. As shipped in
`config/segformer_daemon.service` that is the five-band SegFormer checkpoint,
`/home/pi/segformer_5band/segformer_5band_int8.onnx`. A unit can run another
model by pointing the daemon's `--model` at it with a systemd drop-in (the
daemon reports the loaded file's hash, so a model change shows up even with no
git change).

The daemon also knows an RGB-only model (`--rgb-model`, default
`/home/pi/segformer_type_pool/type_pool_b0_dyn_meta.onnx`). The choice per capture:

1. If the capture has all its bands, the loaded model runs.
2. If the thermal or NIR band is empty, `segformer` asks for `rgb_only`. The
   loaded model runs only if it reads just red, green and blue. Otherwise the
   daemon loads the RGB model and uses it (reported as `rgb_fallback`).
3. With no RGB model available, or no daemon, segmentation is skipped, the
   capture has no mask, and the reason is written to `capture_status.json`.

Each uplink says which model made the mask (`model_id`, `model_bands`;
[RELEASES.md](RELEASES.md#on-the-wire)). Which unit runs which model is not
recorded in this repository. Model training and export are in the
`segformer_5band` repository; the speed measurements are in
[SEGFORMER_OPTIMIZATION.md](SEGFORMER_OPTIMIZATION.md).

## 7. LoRa and IP sending

Both paths carry the same readings and the same bitmap
([IP_TRANSMISSION.md](IP_TRANSMISSION.md)). Each value in a payload is tagged
with a two-byte field code. (Moving the message type into the LoRaWAN FPort is
a proposal, not implemented.)

**LoRa** (`lora_token_with_tracker`, `tools/lora_handler_concurrent.py`,
`tools/lora_send.py`): one sensor packet with the capture time and the fields
that changed past their thresholds (`tools/sensor_changes.py`), then the bitmap,
if `tools/bitmap_policy.py` says one is due. Packets are sized to the current
data rate. If the mDot is not joined, or a send stalls, they go to the
store-and-forward queue (`tools/lora_store_forward.py`, `data/lora_pending/`) and
go out later. At the lowest data rate no bitmap fits, so bitmaps wait in the queue.

**IP** (`ip_uplink_transmit`, `tools/transmit_ip.py`): a POST to the server's
`/ip/uplink` with a per-device token. Failed uploads are kept in
`data/ip_uplink_pending/` and retried at the next wake.

**How the transport is chosen.** The order is LoRa, then WiFi, then cellular.

- IP is off unless `ip_upload.enabled` is true in `runtime_config.json`.
- With `ip_upload.only_if_lora_unavailable` (default true), IP is skipped while
  the mDot is joined.
- When the server cannot be reached otherwise, the node brings up the cellular
  connection named in `ip_upload.cellular_connection`, and takes it down again
  after the upload and the command poll. It does not use cellular when WiFi is
  connected and only the server is down.
- IP fallback (`tools/ip_fallback.py`): one upload goes over IP even with IP off
  or LoRa healthy, when the server asks for it (command `17 97 NN`), or when no
  bitmap has been delivered for `ip_fallback_after_hours` (default 12) while LoRa
  is holding bitmaps back.
- A reading delivered over IP is removed from the LoRa queue.

Commands come back the same two ways: in `/ip/uplink` and `/ip/downlink`
replies, and as LoRa downlinks handled by `lora_daemon`. The frame format, acks
and dedup are in [SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md). Cellular data use:
[CELLULAR_DATA.md](CELLULAR_DATA.md).

## 8. Shutdown and the awake limit

**How long a wake lasts.** 006, 25 Oct to 13 Nov 2026 (200 wakes, 2 passes; from its
once-a-minute power log, so each figure can be up to a minute short): typically 1–2
minutes from power-on to halt, 90% under about 3 minutes, the longest about 4–5
minutes (GPS windows, commands). None ran into the Witty Pi's ON window. With the
default 3 passes, expect about a minute more. The Witty Pi's ON window and
`max_awake_minutes` are backstops, not the usual length.

`call_shutdown` runs once per pass.

1. It sends the systemd watchdog heartbeat and adds one to the pass count for
   this boot.
2. Normal mode: when the count reaches `shutdown_iteration_limit` (default 3)
   and auto shutdown is on, it goes on. Otherwise the graph runs another pass,
   60 s later. 006 runs 2 passes (`shutdown_iteration_limit` 2 in its
   `runtime_config.json`).
3. Before powering off, `tools/last_downlink_check.py` picks up any command sent
   during the wake. A command can say to stay up (emergency mode on, a remote
   session).
4. It waits for an open GPS window (`tools/gps_window.py`), finalizes any audio
   segment, then runs `doas /usr/sbin/shutdown -h now`. The Witty Pi sees a
   normal halt and cuts power.
5. **Emergency mode** changes this: the node captures every
   `emergency_frequency` minutes, sleeping between captures
   (`tools/emergency_sleep.py`), and ends after `emergency_max_hours` (default
   24). Its Witty Pi shutdown alarm is moved to a backstop an hour after that
   end. See [REMOTE_START_AND_EMERGENCY.md](REMOTE_START_AND_EMERGENCY.md).

Backstops, in order: the systemd watchdog restarts a stalled `ticktalk`;
`max-awake.timer` (every 5 minutes) shuts the Pi down once it has been up more
than `max_awake_minutes` (default 60; `hold_awake` and active remote sessions
override it); the Witty Pi's own off time in the schedule cuts power if the Pi
itself hangs. Details: FIELD_DEPLOYMENT_GUIDE 1.9.

## 9. The clock

Units run no NTP, by design: the Witty Pi documentation says NTP interferes with
the Witty Pi's own timekeeping (its software turns NTP off itself). The clock is the Witty Pi RTC, copied in at boot,
then corrected from LoRa network time (`tools/lora_time.py`), network time or
GPS (`config/wittypi/syncTime.sh`), the server's `Date` header after an IP
upload, or the daily GPS fix. A boot with no source marks its captures "clock
unverified". All the rules are in [TIME_SYNC.md](TIME_SYNC.md).

## 10. Updating a node

Nodes run releases: signed CalVer git tags (`vYYYY.MM.N`). A node keeps a
shallow checkout, fetches one tag, runs `git verify-tag` against the signers in
`/etc/watercam/release_signers`, checks out the tag, compares the pickle's
SHA-256 with `output/ticktalk_main.pickle.sha256`, and restarts the two daemons.
The dashboard shows each node's release. Steps, release checks and rollback:
[RELEASES.md](RELEASES.md). Anything outside the checkout (unit files,
`doas.conf`, Witty Pi scripts) is called out in the release notes.

## 11. Where security is described

[NODE_SECURITY.md](NODE_SECURITY.md): what a node exposes, the firewall, trusted
servers, release signing. The IP token and its installation:
[IP_TRANSMISSION.md](IP_TRANSMISSION.md) ("Installing the IP token"). Remote
access: [REMOTE_DEBUG_SESSION.md](REMOTE_DEBUG_SESSION.md).

## 12. Where the reasons are recorded

| Choice | Read |
|---|---|
| Clock sources, no NTP | [TIME_SYNC.md](TIME_SYNC.md) |
| Energy per wake, why the mDot is fed 5 V, battery signal plans | [POWER_ANALYSIS.md](POWER_ANALYSIS.md), [BATTERY_ESTIMATION_PLAN.md](BATTERY_ESTIMATION_PLAN.md) |
| Why the Witty Pi gate, recovery boot and next-wake services exist | [UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md), [WITTYPI_SCHEDULING.md](WITTYPI_SCHEDULING.md), [WITTYPI_P2_POWER_HARNESS.md](WITTYPI_P2_POWER_HARNESS.md) |
| Capture details, co-registration, band handling | [CAPTURE_PIPELINE_NOTES.md](CAPTURE_PIPELINE_NOTES.md), [NIR_FILTER.md](NIR_FILTER.md), [IMU_CALIBRATION.md](IMU_CALIBRATION.md) |
| Why a resident segmentation daemon | [SEGFORMER_OPTIMIZATION.md](SEGFORMER_OPTIMIZATION.md) |
| Why one LoRa daemon owns the serial port | [LORA_HANDLER_MULTIPROCESS_ISSUE.md](LORA_HANDLER_MULTIPROCESS_ISSUE.md) |
| IP transport and its fallbacks | [IP_TRANSMISSION.md](IP_TRANSMISSION.md), [1NCE_SIM_EXHAUSTION.md](1NCE_SIM_EXHAUSTION.md), [CELLULAR_DATA.md](CELLULAR_DATA.md) |
| Bitmap format and compression | [BITMAP_DECODING_SPEC.md](BITMAP_DECODING_SPEC.md), [BITMAP_SF_ADAPTIVE_COMPRESSION.md](BITMAP_SF_ADAPTIVE_COMPRESSION.md), `BITMAP_COMPRESSION_ANALYSIS.md` (repository root) |
| Remote start, emergency mode, remote debug | [REMOTE_START_AND_EMERGENCY.md](REMOTE_START_AND_EMERGENCY.md), [REMOTE_DEBUG_SESSION.md](REMOTE_DEBUG_SESSION.md) |
| Field setup mode (design) | [SETUP_MODE_DESIGN.md](SETUP_MODE_DESIGN.md) |
| Releases and signing | [RELEASES.md](RELEASES.md) |
| Security choices | [NODE_SECURITY.md](NODE_SECURITY.md) |
| Every setting | [CONFIGURATION.md](CONFIGURATION.md) |

Old backlogs are in [archive/](archive/README.md).
