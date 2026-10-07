# Field Deployment Guide: Configuring and Installing an Assembled WaterCam Unit

**Audience:** Written assuming you have an assembled unit and flashed the SD card image (https://drive.google.com/file/d/1dCcisGiLYk8vYh0eK_8w945sLaqX_JKo/view?usp=sharing) but need to set up remote access, the WittyPi schdedule, and the auto-start functionality. 

**Scope:** This guide starts where the hardware build guide (`README.md`) ends, it assumes you have a fully assembled unit (all components installed, case ready to be sealed).

If any hardware step (soldering, wiring, case prep) hasn't been done yet, stop and follow `README.md` first.

---

## Overview:

1. **Bench configuration** — do this before you leave, while the unit still has network access and you can fix issues.
2. **Field installation** — physical attachment, and on-site verification that everything survived the trip and is working from its final position.

---

## Phase 1: Bench Configuration Checklist

### 1.1 Connect to the unit

Use a USB UART serial adapter (https://www.adafruit.com/product/954) to connect to the Raspberry Pi after you've inserted the flashed SD card into the Raspberry Pi. The white wire connects to pin 8, green to pin 10, and you can connect the black wire to any free ground pin on the Raspberry Pi, like pin 14.

****Make sure the red wire is NOT connected to the Pi****

You will need software on your computer to connect to the Raspberry Pi over the serial link. On a Linux system I would recommend tio. On Windows: (https://learn.adafruit.com/windows-tools-for-the-electrical-engineer/serial-terminal)

Once you've connected the serial adapter, started your connection software with it set to the correct port for the serial adapter, and turned the Raspberry Pi on by tapping the button on the WittyPi you should see some text eventually. It will take time for the system to complete its first boot, and it should reboot automatically after it expands the filesystem to the size of the SD card. If you do not see text in your software, try hitting Enter to see if you get a response. If nothing appears double check how the serial adapter is connected.

If you get the expected login prompt you can log into the system with the standard username pi and password. You will not see the password as you type it, just type it out and hit Enter. If you are at SU campus the system should automatically connect to AirOrangeX and display its IP address (v4 and v6), at which point you can switch to using SSH to connect to the Raspberry Pi, or continue using the serial adapter.

If you want to connect to a different WiFi network you can use 'sudo nmtui' to set up the connection. (https://www.howtogeek.com/devops/how-to-manage-linux-wi-fi-networks-with-nmtui/)

If you need to edit a text file you can use `nano filename.txt`

You can check that the camera is in focus by capturing a photo with `rpicam-still --output test.jpg` and viewing the photo by copying it to your system once SSH is setup or by shutting down the Raspberry Pi and copying the file from the SD card. 

### 1.2 Set a unique hostname

```bash
sudo hostnamectl set-hostname <site-name>
```

Use a name that either identifies the deployment location or the unit number.

After changing it, confirm `/etc/hosts` was updated — the `127.0.0.1` line must match the new hostname, not the base SD image's original name:

```bash
cat /etc/hosts
```

If it does not match, use `sudoedit /etc/hosts` to change it. Log out and back in to check the changes applied.

Every unit flashed from the same SD image starts with the same machine ID and
network keys. Give this unit its own, then reboot:

```bash
sudo rm /etc/machine-id && sudo systemd-machine-id-setup
sudo rm -f /var/lib/NetworkManager/secret_key /var/lib/dhcpcd/duid
sudo reboot
```

systemd, journald and NetworkManager all treat the machine ID as unique, and
the NetworkManager key sets the unit's IPv6 address, so two clones on the same
network would otherwise share it. Check with `cat /etc/machine-id` that it no
longer matches another unit's.

When making a new SD image, empty the machine ID and delete those two files
before capturing it, so each unit generates its own on first boot:

```bash
sudo truncate -s 0 /etc/machine-id
sudo rm -f /var/lib/NetworkManager/secret_key /var/lib/dhcpcd/duid
```

### 1.3 Confirm remote access (Tailscale)

Run the command provided by Tailscale to install

If there is an issue with the script check (https://console.tailscale.com/admin/machines/new-linux)

```bash
tailscale up --ssh
```

This will allow logging into the system using SSH now and when the unit is in the field connected via cellular modem.

### 1.4 Sync the clock

This should have been done automatically, but check the system time is correct.

```bash
sudo /home/pi/wittypi/syncTime.sh
```

How the unit keeps time (NTP is off; `timedatectl` reports "not synchronized", which is expected):

1. **At boot,** the Witty Pi daemon copies its RTC into the system clock, or
   the other way round if the RTC's time looks bad.
2. **About 30 s later,** `beforeScript.sh` runs `syncTime.sh`. It reads the
   `Date:` header of an HTTP request to `http://google.com`, accurate to about
   1 s, and writes it to the system clock and the RTC. This only works when the
   unit has internet at boot (WiFi or cellular).
3. **The RTC holds local time.** After a daylight-saving change, the first boot
   runs an hour off until step 2 corrects it. A unit that never has internet
   isn't corrected for either the change or drift, so check its clock at each
   visit.

### 1.5 Configure `runtime_config.json`

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

| Field                              | Units   | Range   | Meaning                                                                                           |
| ---------------------------------- | ------- | ------- | ------------------------------------------------------------------------------------------------- |
| `area_threshold`                   | %       | 0–100   | Flood-extent area (as % of frame) that triggers elevated/emergency reporting                      |
| `stage_threshold`                  | cm      | 0–65535 | Water stage (level) threshold                                                                     |
| `monitoring_frequency`             | minutes | 1–10080 | How often the unit checks/reports under normal conditions                                         |
| `emergency_frequency`              | minutes | 1–1440  | How often it reports once a threshold is exceeded (should be shorter than `monitoring_frequency`) |
| `photo_interval`                   | seconds | 30–1440 | Time between captures while the unit is awake (default 60); applied to the running program        |
| `neighborhood_emergency_frequency` | minutes | 1–1440  | Reporting frequency once a *neighboring* unit signals emergency                                   |

Also confirm before deploying:

- `emergency_mode: false` and `debug_mode: false` — both should be off for a normal deployment
- `ip_upload.enabled` — `true` if this site will use cellular/IP upload in addition to (or instead of) LoRa; if so, fill in `server_url` and  `api_key` for the WaterCam API server this unit reports to. This is already set for our tailnet.
- **LoRa sensor packet:** each capture sends the capture time plus only the fields
  that changed by 5% or more since they were last sent (`data/lora_last_sent.json`).
  Orientation (the IMU's heading, roll, pitch) uses an absolute threshold instead,
  `lora_imu_threshold_deg` (default 2°), allowing for the heading wrapping at 360°.
  Every `lora_full_send_hours` (default 24) all fields go out, since LoRa uplinks are
  unconfirmed and a lost packet would otherwise leave an old value on the server.
  `always_transmit_sensors: true` sends every field every capture.
- **Transport order** (on by default): the unit uses LoRa, WiFi if LoRa isn't
  joined, and cellular only if neither works. For the modem to be used only on
  demand, run `sudo nmcli connection modify Quectel connection.autoconnect no` and install
  `config/polkit/50-watercam-networkmanager.rules` to `/etc/polkit-1/rules.d/`. Until then
  cellular stays up all the time, as before. See
  [IP_TRANSMISSION.md](IP_TRANSMISSION.md#transport-order-lora-wifi-cellular).

### 1.6 LoRa registration

This can be tricky. 

If the mDot has already been flashed with our custom firmware (https://github.com/WaterCam-Team/mDot-AT-firmware) then you can proceed to connecting to a gateway after the mDot ID is registered with ChirpStack. If the firmware has not been flashed, follow the instructions in the firmware GitHub. If it has programming headers installed the easy way is to use the USB development board (https://multitech.com/product/multitech-mdot-micro-developer-kit-global/). If not, the same USB serial adapter used for the Raspberry Pi can be used to flash the firmware following the instructions in the repository.

The mDot module's DevEUI must already be registered in ChirpStack before the unit can join the network. **This should be done ahead of time by the ChirpStack administrator**, confirm with them that this specific unit's DevEUI is registered and assigned to the correct application/device profile. The DevEUI is unique per mDot module (see `tools/AT_COMMANDS_REFERENCE.md` for querying the module over serial if you need to look it up).

Once it's confirmed that the device is running our firmware and it is set up in ChirpStack, you can connect to the gateway following the instructions in https://docs.google.com/document/d/1Z83h89x7jlwzRIvbZtyiRxBoMupdoHzL/edit?usp=drive_link&ouid=115248649914453432688&rtpof=true&sd=true

I'd recommend using SSH at this point to connect to the Raspberry Pi instead of serial. On the Raspberry Pi run `tio /dev/ttyAMA5` to issue the commands to connect to the gateway when you know it is in range.

### 1.7 IMU Stage 1 calibration

**Has to be done before the unit is installed outside**

```bash
python tools/bno055_calibration.py --unit-config <unit_config_file>.json --mode calibrate
```

Verify `bno055_calibration.json` shows `"mag": 3` when done. Without it heading data will be unreliable and georeferencing will be difficult.

### 1.8 Confirm camera calibration exists

Calibration for our set of hardware is done and the file should transfer from unit to unit as long as hardware does not change. I forgot to copy it to the SD card image, so download from (https://drive.google.com/file/d/1Lpb0LEcR_ePSkGa5xrXXbkxigSQw4oQi/view?usp=drive_link) and copy it to `config/camera_calibration.json` in the SU-WaterCam directory using scp or an SD card reader.

```bash
ls config/camera_calibration.json
```

If missing, this file should be copied over or it needs to be redone before deployment (using the 25x18-square, 30mm calib.io checkerboard) use `tools/camera_calibration.py`

### 1.9 Enable the production service

The application is run via `ticktalk.service`

```bash
sudo cp config/lora_daemon.service config/segformer_daemon.service \
        config/ticktalk.service config/wittypi-recovery.service config/wittypi-boot-mark.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable watercam.service 2>/dev/null   # if it was ever enabled
sudo systemctl enable --now lora_daemon.service segformer_daemon.service
sudo systemctl enable ticktalk.service wittypi-recovery.service wittypi-boot-mark.service
```

`ticktalk` and the WittyPi services start on the next boot.

`lora_daemon.service` owns the mDot serial port, and every LoRa send or
receive in `ticktalk` goes through it. Without it the unit has no LoRa.
`segformer_daemon.service` keeps the flood-segmentation model loaded. Without
it `ticktalk` falls back to loading the model on every cycle, about 40 s
instead of about 2 s. Both start before `ticktalk.service`; `--now` also starts
them straight away for the test cycle in 1.11.

`wittypi-recovery.service` handles recovery after a power outage. Leave the
WittyPi on "Default ON", so it boots the Pi whenever power returns. When a
boot comes from power returning rather than from the schedule, the service
arms the next `schedule.wpi` slot at least 2 hours away and shuts down before
the cameras and modem start, giving the battery time to recharge. Settings are
under `recovery_boot` in `runtime_config.json`.

This also applies when you first connect power, so a freshly connected unit
goes straight back to sleep. **Press the WittyPi button** to start a normal
cycle on the bench or at deployment. On the bench you can also set
`"recovery_boot": {"enabled": false}`.

Optionally, install the power logger. It writes one line a minute to
`~/powerlog/powerlog.csv`, synced to disk so the last reading before a
brownout survives, with a marker line for every boot. When a unit fails in
the field, this log shows when the power dropped and how each boot started.
`tools/power_test_report.py ~/powerlog/powerlog.csv` summarises it.

```bash
sudo cp config/powerlog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now powerlog.service
```

Keep the systemd journal in RAM to spare the microSD card. Logs then last
until the next shutdown or power cut; the power log above is the record that
survives one.

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo cp config/journald-volatile.conf /etc/systemd/journald.conf.d/
sudo systemctl restart systemd-journald
```

### 1.10 Run the startup health check

`tools/initial_health_check.py` checks CPU temperature, WittyPi voltages, GPS fix, and IMU availability in one shot, and sends a LoRa alert on failure.

```bash
cd /home/pi/SU-WaterCam
python tools/initial_health_check.py; echo "exit code: $?"
```

Exit code `0` means everything passed. If it fails, read the printed failure reasons (`gps_unavailable`, `imu_unavailable`, `wittypi_input_voltage_low_*V`,
etc.) and resolve them before moving on. GPS will very likely fail indoors, re-check it outdoors in Phase 2.

### 1.11 Test a full capture-and-transmit cycle

```bash
sudo systemctl start ticktalk.service
journalctl -u ticktalk.service -f
```

Watch the log for a completed photo capture and a successful LoRa/IP
transmission. Then confirm the reading actually arrived — check the WaterCam dashboard for this device's `device_id` and confirm a fresh
reading shows up. Stop the service again if you still need to seal the case as the service will shutdown the Raspberry Pi once it has finished capturing data:

```bash
sudo systemctl stop ticktalk.service
```

### 1.12 Weatherproofing

Once everything above passes, follow `README.md`'s sealing steps (silicone sealant on case openings, LWIR window installation for the Lepton, water resistance check) before transporting the unit.

### 1.13 Set the power schedule (WittyPi)

The WittyPi controls when the Pi is powered on/off and needs to be told what schedule to use. For a normal deployment use this repo's daylight schedule. It wakes for up to 15 min every 2 h while the camera can see: 08:00–16:00 EST, which is 09:00–17:00 during daylight saving, since the Witty Pi counts seconds from `BEGIN`.

```bash
cp /home/pi/SU-WaterCam/config/wittypi/watercam_daylight_2h.wpi /home/pi/wittypi/schedule.wpi
```

It was checked against sunrise and sunset for Syracuse (43° N) all year. At a site far from that latitude, check the first and last wake times. Don't run `runScript.sh` on the bench, because it arms the schedule and the unit then shuts down at the end of the window. The daemon arms it at the next boot, which is when you press the button at deployment.

If you want to use one of the default schedules run `./wittypi/wittyPi.sh`

If you need to customize the schedule you can write a `.wpi` file for this deployment: (https://github.com/uugear/Witty-Pi-4/tree/main/Software/wittypi/schedules)

Then install this repo's `beforeScript.sh`, which lets the WittyPi arm the next wake about 15 s after boot instead of about 55 s:

```bash
cp /home/pi/SU-WaterCam/config/wittypi/beforeScript.sh /home/pi/wittypi/beforeScript.sh
```

## Phase 2: Field Installation

### 2.1 Site selection

- Camera has a clear, unobstructed view of the area to monitor.
- Solar panel has clear southern exposure (or whatever maximizes light at the install location) with minimal shading across the day.
- Mount is **rigid** — the pole must not sway or rotate
- Within range of a LoRa gateway or cellular coverage, as applicable to this unit's transport configuration.

### 2.2 Mount and record physical measurements

Once mounted, **record these values on-site** 

- **Mount height** above the surface.
- **Approximate compass heading** the camera faces (a phone compass reading is fine as a sanity check — the IMU calibration below gets the precise value).
- Pole material and any known magnetic interference sources nearby.
- GPS coordinates of the installation taken with RTK hardware
- RTK ground control points in the camera's field of view

This is needed for the georeferencing process

### 2.3 Power on and verify

```bash
ssh <hostname>   # via Tailscale, once it reconnects
```

Check that the monitoring software is running with `journalctl -u ticktalk.service -f`

GPS acquiring a fix outdoors from a cold start can take a few minutes (longer without recent XTRA assistance data — see the GPS/GNSS setup section of `README.md`). Give it time before troubleshooting.

### 2.4 Confirm data is flowing from the final position

Check the dashboard again for a reading with GPS coordinates matching the actual install location, and confirm the WittyPi schedule is triggering boots as expected.

### 2.5 Final weatherproofing

Confirm all cable entries are sealed, connections to the solar panel are secure, and the case is fully closed before leaving the site.

---

## Before You Leave: Remote Verification

- **Dashboard**: check for regular incoming readings at the expected
  `monitoring_frequency` cadence.
- **Remote access**: `ssh <hostname>` over Tailscale (only works while the Pi is powered on per its WittyPi schedule
- **Remote Start**: if the system is in range of a LoRa gateway and connected to it you can send a remote start signal from the Chirpstack server or through the dashboard (the dashboard is still under development so it might have issues)
