# Field Deployment Guide: Configuring and Installing an Assembled WaterCam Unit

**Audience:** Written for a researcher who has an assembled unit and has flashed the SD card image (https://drive.google.com/file/d/1dCcisGiLYk8vYh0eK_8w945sLaqX_JKo/view?usp=sharing) but needs to set up remote access, the WittyPi schdedule, and the auto-start functionality.

**Scope:** This guide starts where the hardware build guide (`README.md`) ends —
it assumes you have a fully assembled unit (all components installed, tested on
the bench per that guide's "Hardware Setup" section, case ready to be sealed).
It covers **software configuration and field installation** only.

If any hardware step (soldering, wiring, case prep) hasn't been done yet, stop
and follow `README.md` first.

---

## Overview: two phases

1. **Bench configuration** — do this before you leave, while the unit still has
   Ethernet/network access and you can fix mistakes easily. This is the
   longer, more important phase.
2. **Field installation** — physical siting, mounting, and on-site verification
   that everything survived the trip and is working from its final position.

---

## Phase 1: Bench Configuration Checklist

Work through this list in order. Each item includes how to verify it worked.

### 1.1 Connect to the unit

Use a USB UART serial adapter (https://www.adafruit.com/product/954) to connect to the Raspberry Pi after you've inserted the flashed SD card into the Raspberry Pi. The white wire connects to pin 8, green to pin 10, and you can connect the black wire to any free ground pin on the Raspberry Pi, like pin 14.

****Make sure the red wire is NOT connected to the Pi****

You will need software on your computer to connect to the Raspberry Pi over the serial link. On a Linux system I would recommend tio. On Windows: (https://learn.adafruit.com/windows-tools-for-the-electrical-engineer/serial-terminal)

Once you've connected the serial adapter, started your connection software with it set to the correct port for the serial adapter, and turned the Raspberry Pi on by tapping the button on the WittyPi you should see some text eventually. It will take time for the system to complete its first boot, and it should reboot automatically after it expands the filesystem to the size of the SD card. If you do not see text in your software, try hitting Enter to see if you get a response. If nothing appears double check how the serial adapter is connected.

If you get the expected login prompt you can log into the system with the standard username pi and password. You will not see the password as you type it, just type it out and hit Enter. If you are at SU campus the system should automatically connect to AirOrangeX and display its IP address (v4 and v6), at which point you can switch to using SSH to connect to the Raspberry Pi, or continue using the serial adapter.

If you want to connect to a different WiFi network you can use 'sudo nmtui' to set up the connection. (https://www.howtogeek.com/devops/how-to-manage-linux-wi-fi-networks-with-nmtui/)

If you need to edit a text file you can use `nano filename.txt`

### 1.2 Set a unique hostname

```bash
sudo hostnamectl set-hostname <site-name>
```
Use a name that either identifies the deployment location or the unit number.

After changing it, confirm `/etc/hosts` was updated — the `127.0.0.1` line must
match the new hostname, not the base SD image's original name:

```bash
cat /etc/hosts
```
If it does not match, use `sudoedit /etc/hosts` to change it. Log out and back in to check the changes applied.

### 1.3 Confirm remote access (Tailscale)

Run the command in tailscale-key.txt to install Tailscale.

If there is an issue with the script check (https://console.tailscale.com/admin/machines/new-linux)

```bash
tailscale up --ssh
```

This will allow logging into the system using SSH when the unit is in the field and connected via cellular modem.

### 1.4 Sync the clock

This should have been done automatically, but check the system time is correct.

```bash
sudo /home/pi/wittypi/syncTime.sh
```

### 1.5 Set the power schedule (WittyPi)

The WittyPi controls when the Pi is powered on/off and needs to be told what schedule to use. If you want to use one of the default schedules run `./wittypi/wittyPi.sh`

If you need to customize the schedule you can write a `.wpi` file for this deployment: (https://github.com/uugear/Witty-Pi-4/tree/main/Software/wittypi/schedules)

### 1.6 Configure `runtime_config.json`

This is the unit's operating settings. The file `/home/pi/SU-WaterCam/runtime_config.json` should be edited to have the name of the specific unit and other settings.

```bash
nano /home/pi/SU-WaterCam/runtime_config.json
```
The required change is to set a unique name. The system hostname or something derived from it would make sense.

```json
"ip_upload": {
  "device_id": "watercam-007",
  ...
}
```

The other top-level fields control monitoring behavior. From
`tools/lora_runtime_integration.py`'s parameter definitions:

| Field | Units | Range | Meaning |
|---|---|---|---|
| `area_threshold` | % | 0–100 | Flood-extent area (as % of frame) that triggers elevated/emergency reporting |
| `stage_threshold` | cm | 0–65535 | Water stage (level) threshold |
| `monitoring_frequency` | minutes | 1–10080 | How often the unit checks/reports under normal conditions |
| `emergency_frequency` | minutes | 1–1440 | How often it reports once a threshold is exceeded (should be shorter than `monitoring_frequency`) |
| `photo_interval` | minutes | 1–1440 | How often it captures a photo |
| `neighborhood_emergency_frequency` | minutes | 1–1440 | Reporting frequency once a *neighboring* unit signals emergency |

Also confirm before deploying:
- `emergency_mode: false` and `debug_mode: false` — both should be off for a
  normal deployment (debug mode is noisy and not meant for production).
- `ip_upload.enabled` — `true` if this site will use cellular/IP upload
  in addition to (or instead of) LoRa; if so, fill in `server_url` and
  `api_key` for the WaterCam API server this unit reports to. This is already set for our tailnet.

### 1.7 LoRa registration

This can be tricky. 

If the mDot has already been flashed with our custom firmware (https://github.com/WaterCam-Team/mDot-AT-firmware) then you can proceed to connecting to a gateway after the mDot ID is registered with ChirpStack. If the firmware has not been flashed, follow the instructions in the firmware GitHub. If it has programming headers installed the easy way is to use the USB development board (https://multitech.com/product/multitech-mdot-micro-developer-kit-global/). If not, the same USB serial adapter used for the Raspberry Pi can be used to flash the firmware following the instructions in the repository.

The mDot module's DevEUI must already be registered in ChirpStack before the unit can join the network. **This should be done ahead of time by the ChirpStack administrator**, confirm with
them that this specific unit's DevEUI is registered and assigned
to the correct application/device profile. The DevEUI is
unique per mDot module (see `tools/AT_COMMANDS_REFERENCE.md` for querying the module over serial if you need to look it up).

Once it's confirmed that the device is running our firmware and it is set up in ChirpStack, you can connect to the gateway following the instructions in https://docs.google.com/document/d/1Z83h89x7jlwzRIvbZtyiRxBoMupdoHzL/edit?usp=drive_link&ouid=115248649914453432688&rtpof=true&sd=true

I'd recommend using SSH at this point to connect to the Raspberry Pi instead of serial. On the Raspberry Pi run `tio /dev/ttyAMA5` to issue the commands to connect to the gateway when you know it is in range.

### 1.8 IMU Stage 1 calibration

**Has to be done before the unit is installed outside**

```bash
python tools/bno055_calibration.py --unit-config <unit_config_file>.json --mode calibrate
```

Verify `bno055_calibration.json` shows `"mag": 3` when done. Without it heading data will be unreliable and georeferencing will be difficult.

### 1.9 Confirm camera calibration exists

Calibration for our set of hardware is done and the file should transfer from unit to unit as long as hardware does not change. I forgot to copy it to the SD card image, so download from (https://drive.google.com/file/d/1Lpb0LEcR_ePSkGa5xrXXbkxigSQw4oQi/view?usp=drive_link) and copy it to `config/camera_calibration.json` in the SU-WaterCam directory using scp or an SD card reader.

```bash
ls config/camera_calibration.json 2>/dev/null || echo "MISSING — see tools/camera_calibration.py"
```

If missing, this needs to be redone before deployment (it requires our
25x18-square, 30mm calib.io checkerboard and can't easily be done remotely
afterward) — use `tools/camera_calibration.py`, the canonical calibration
tool (a copy of the calibration script from this project's separate
Georeferencing codebase, not published on GitHub; `tools/camera_calibration_legacy.py`
in this repo is deprecated).

### 1.10 Enable the production service

The production application is `ticktalk_main.py`, run via `ticktalk.service`
(not `watercam.service`, which is an older/simpler variant — make sure only
one of these is enabled to avoid both fighting over the camera and radio).

```bash
sudo cp config/ticktalk.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable watercam.service 2>/dev/null   # if it was ever enabled
sudo systemctl enable ticktalk.service
```

Also confirm the button service is installed if the unit has a physical
capture button:

```bash
sudo cp config/button.service /etc/systemd/system/
sudo systemctl enable button.service
```

### 1.11 Run the startup health check

`tools/initial_health_check.py` checks CPU temperature, WittyPi voltages, GPS
fix, and IMU availability in one shot, and sends a LoRa alert on failure. Run
it manually now, on the bench, before you rely on it happening automatically:

```bash
cd /home/pi/SU-WaterCam
python tools/initial_health_check.py; echo "exit code: $?"
```

Exit code `0` means everything passed. If it fails, read the printed failure
reasons (`gps_unavailable`, `imu_unavailable`, `wittypi_input_voltage_low_*V`,
etc.) and resolve them before moving on. GPS will very likely fail indoors —
that's expected at this stage; you'll re-check it outdoors in Phase 2.

### 1.12 Test a full capture-and-transmit cycle

```bash
sudo systemctl start ticktalk.service
journalctl -u ticktalk.service -f
```

Watch the log for a completed photo capture and a successful LoRa/IP
transmission. Then confirm the reading actually arrived — check the WaterCam
dashboard (`/dashboard`) for this device's `device_id` and confirm a fresh
reading shows up. This closes the loop end-to-end before the unit ever leaves
the bench: camera → decode → radio → server → dashboard.

Stop the service again if you still need to seal the case:

```bash
sudo systemctl stop ticktalk.service
```

### 1.13 Weatherproofing

Once everything above passes, follow `README.md`'s sealing steps (silicone
sealant on case openings, LWIR window installation for the Lepton, water
resistance check) before transporting the unit.

---

## Phase 2: Field Installation

### 2.1 Site selection

- Camera has a clear, unobstructed view of the water body/area to monitor.
- Solar panel has clear southern exposure (or appropriate for your hemisphere)
  with minimal shading across the day.
- Mount is **rigid** — the pole must not sway or rotate. This matters more
  than it sounds: mount instability shows up directly as heading drift in the
  IMU calibration doc's troubleshooting section. Avoid highly magnetic
  (steel) poles if you have a choice, since they distort the compass reading.
- Within range of a LoRa gateway or cellular coverage, as applicable to this
  unit's transport configuration.

### 2.2 Mount and record physical measurements

Once mounted, **record these values on-site** — some can only be captured
correctly right now, and are needed later for georeferencing that photo data:

- **Mount height** above the water surface or a known ground datum (meters).
- **Approximate compass heading** the camera faces (a phone compass reading is
  fine as a sanity check — the IMU calibration below gets the precise value).
- Pole material and any known magnetic interference sources nearby.
- GPS coordinates of the installation (you'll also get this automatically from
  the unit's own GPS once running, but a manual note is a good cross-check).

This raw information (mount height, heading, site notes) needs to make it into
the unit's config for georeferencing to work — check with the project lead on
the current process for entering it (unit config file, or the API's per-device
calibration/pose-config page under `/georeference`).

### 2.3 Power on and verify

```bash
ssh <hostname>   # via Tailscale, once it reconnects
python tools/initial_health_check.py; echo "exit code: $?"
```

GPS acquiring a fix outdoors from a cold start can take a few minutes (longer
without recent XTRA assistance data — see the GPS/GNSS setup section of
`README.md`). Give it time before troubleshooting.

### 2.4 IMU Stage 2 calibration (post-mount)

This requires RTK survey equipment (viDoc rover or similar) and two surveyed
reference points visible from the camera — it's often scheduled as a separate
trip if that equipment isn't with you today. Full procedure:
**`docs/IMU_CALIBRATION.md`**, Stage 2.

The unit is fully functional without this step — photos, water-level readings,
and alerts all work — but georeferenced flood-extent accuracy will be degraded
(potentially by tens of degrees of heading error) until it's done. Don't treat
it as optional long-term, just as deferrable if you're not equipped for it on
this particular visit.

### 2.5 Confirm data is flowing from the final position

Check the dashboard again for a reading with GPS coordinates matching the
actual install location, and confirm the WittyPi schedule is triggering boots
as expected (watch for 1–2 full cycles if you have time, or check back
remotely later via Tailscale).

### 2.6 Final weatherproofing

Confirm all cable entries are sealed, connections to the solar panel are
secure, and the case is fully closed before leaving the site.

---

## Before You Leave: Remote Verification

- **Dashboard**: check for regular incoming readings at the expected
  `monitoring_frequency` cadence. Gaps longer than a few cycles are worth
  investigating.
- **Remote access**: `ssh <hostname>` over Tailscale (only works while the Pi
  is powered on per its WittyPi schedule — timing your SSH attempts around the
  known ON windows helps).
- **Maintenance cadence**: see the table in `docs/IMU_CALIBRATION.md` for when
  to re-run Stage 2 (roughly every 6 months, or after any mount disturbance).

---

## Troubleshooting Quick Reference

| Symptom | Likely cause | Where to look |
|---|---|---|
| No data on dashboard at all | Service not running, or radio/network not connecting | `journalctl -u ticktalk.service`, confirm `ticktalk.service` is enabled and running |
| GPS never gets a fix | Cold start takes minutes outdoors; indoors it may never fix | Re-check outdoors, give it 5-10 min; see GPS/GNSS section of `README.md` |
| IMU heading looks wrong / jumps around | Calibration not done, or mount not rigid | `docs/IMU_CALIBRATION.md` Troubleshooting section |
| Photos are black/blank | NIR filter or Lepton wiring issue | Re-check camera connections per `README.md`'s Optical Camera / Lepton wiring sections |
| LoRa not transmitting | DevEUI not registered in ChirpStack, or antenna disconnected | Confirm registration with project lead; check antenna connections |
| Cellular has no signal | SIM/APN misconfigured, or antenna disconnected | Re-check `Cellular Modem Manual Software Setup` in `README.md` |
| Unit not reachable via Tailscale | Powered off per WittyPi schedule, or never connected | Check WittyPi ON windows; confirm `tailscale status` was healthy before deployment |
| `initial_health_check.py` reports `wittypi_input_voltage_low` | Battery/solar connection issue | Check Voltaic pack charge and cable connections |

---

## Key Files Reference

| Path | Purpose |
|---|---|
| `runtime_config.json` | Live operational config (device_id, thresholds, intervals) |
| `runtime_config.example.json` | Template/reference for the above |
| `config/wittypi/*.wpi` | Power ON/OFF schedule examples |
| `config/ticktalk.service` | systemd unit for the production application |
| `config/button.service` | systemd unit for the manual-capture button |
| `bno055_calibration.json` | Saved IMU calibration offsets (Stage 1) |
| `config/camera_calibration.json` | Camera intrinsics (lens calibration), OV5647 only |
| `tools/initial_health_check.py` | One-shot pre-flight health check |
| `docs/IMU_CALIBRATION.md` | Full IMU calibration procedure (Stage 1 + 2) |
| `docs/POWER_ANALYSIS.md`, `docs/BATTERY_ESTIMATION_PLAN.md` | Choosing a WittyPi duty cycle for a given panel/battery |
| `docs/IP_TRANSMISSION.md` | Cellular/IP upload configuration details |
