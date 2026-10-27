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

### 1.2.1 Give the unit its own `pi` password

The serial console stays enabled for field repairs, so `pi`'s password is a root
login for anyone holding the unit. Set one that is unique to this unit, at least 16
random characters, and store it in the team's password manager as
`ufo-01-01-NNN serial`:

```bash
passwd
```

Never reuse another unit's password or the image default. See
[NODE_SECURITY.md](NODE_SECURITY.md), which also has what to do when a unit is lost.

Loose header cables on the console pins with nothing at the other end let noise
onto the Pi's RX line, and the login prompt (`agetty`) restarts on every "input
overrun" (006 logged 11,556 in 41 hours). Tie off the free ends (RX to GND, or a
cap), and install the drop-in that rate-limits those messages so they can't flood
the journal (the prompt still comes back at once):

```bash
sudo install -D -m 644 config/serial-getty-noise.conf \
  /etc/systemd/system/serial-getty@ttyAMA0.service.d/watercam-noise.conf
sudo systemctl daemon-reload
```

### 1.3 Confirm remote access (Tailscale)

First switch DNS to systemd-resolved (run from `/home/pi/SU-WaterCam`):

```bash
sudo apt install systemd-resolved
sudo install -m 644 config/networkmanager/90-dns-systemd-resolved.conf /etc/NetworkManager/conf.d/
sudo install -D -m 644 config/systemd-resolved/90-watercam.conf /etc/systemd/resolved.conf.d/90-watercam.conf
sudo systemctl restart systemd-resolved
sudo systemctl reload NetworkManager
readlink /etc/resolv.conf      # prints: ../run/systemd/resolve/stub-resolv.conf
sudo ss -lnup | grep -cE ':(5355|5353) '   # prints: 0 (no LLMNR or mDNS responder)
```

`90-watercam.conf` turns off LLMNR and multicast DNS, which resolved enables by
default: they answer name queries from anyone on the same WiFi or cellular network.

Without it, NetworkManager and Tailscale both write `/etc/resolv.conf`. Each time a
connection comes up or goes down, including the `Quectel` connection the software
starts on demand (1.5.1), NetworkManager's rewrite drops Tailscale's DNS server. The
server's tailnet name then stops resolving for seconds to minutes. The IP uplink takes
that to mean WiFi can't reach the server and brings cellular up, on every wake. With
resolved, NetworkManager hands it each connection's DNS servers, and Tailscale adds
its own for tailnet names only.

Do this before installing Tailscale; tailscaled picks its DNS mode when it starts. On
a unit where Tailscale is already up, restart it afterwards. That drops an SSH session
over Tailscale, so do it over serial or local SSH, or detach it and reconnect after a
few seconds:

```bash
sudo systemd-run systemctl restart tailscaled
```

Run the command provided by Tailscale to install

If there is an issue with the script check (https://console.tailscale.com/admin/machines/new-linux)

```bash
tailscale up --ssh
```

This will allow logging into the system using SSH now and when the unit is in the field connected via cellular modem.

Check that tailnet names resolve through resolved. `tailscale0` should list
`100.100.100.100`, and the server should answer by name (prints `200`):

```bash
resolvectl dns
curl -s -o /dev/null -w '%{http_code}\n' "$(python3 -c 'import json; print(json.load(open("runtime_config.json"))["ip_upload"]["server_url"])')/health"
```

The second check needs `runtime_config.json`, so it can wait until 1.5.

### 1.3.1 Put the unit on the current release

Units run releases, not `main` ([RELEASES.md](RELEASES.md)). Check what it runs:

```bash
cd /home/pi/SU-WaterCam && python -m tools.node_version
```

Install the release signers once, outside the checkout, so the node only accepts
signed releases ([RELEASES.md](RELEASES.md#release-signing)):

```bash
sudo install -D -m 644 config/release_signers /etc/watercam/release_signers
git config --global gpg.format ssh
git config --global gpg.ssh.allowedSignersFile /etc/watercam/release_signers
```

If it isn't on the current release, or `.git` is larger than a few MB (`du -sh .git`),
follow [RELEASES.md](RELEASES.md#updating-a-node): update, or convert the full clone to
a shallow one first. Check each release with `git verify-tag` before checking it out.

After updating, install any system files the release notes list as changed. From
v2026.10.4 the doas rules are stricter (passwordless `shutdown` only as `-h now`),
so replace the whole file rather than appending lines:

```bash
sudo cp config/doas.conf /etc/doas.conf && sudo doas -C /etc/doas.conf
```

### 1.3.2 Remove packages a node doesn't need

Fewer packages means less to patch and less for an intruder to use. These have nothing
depending on them on a node, and the software doesn't use them (checked on 005 and 006,
2026-10-12):

```bash
P=""; for p in neovim wget lrzsz minicom gcc-12 g++-12 cpp-12 python3.11-minimal libpython3.11-minimal; do
  dpkg -s "$p" >/dev/null 2>&1 && P="$P $p"; done
[ -n "$P" ] && sudo apt purge --autoremove $P
RC=$(dpkg -l | awk '/^rc/{print $2}'); [ -n "$RC" ] && sudo apt purge $RC   # config left by removed packages
rm -rf /home/pi/SU-WaterCam/venv/lib/python3.11      # pre-trixie venv copy, never loaded
```

Only installed packages are passed to `apt`: on a fresh trixie unit some of these
(the bookworm leftovers) don't exist at all, and `apt` would stop on them. On units
upgraded from bookworm the venv's `lib/python3.11` is about 300 MB: Python 3.13 reads
only `lib/python3.13`.

Units set up before 2026-10-12 have apt's `python3-opencv`, which pulls in about 450 MB
the nodes never use (VTK, LLVM 17, AMD's GPU runtime, OpenMPI). Swap it for pip's headless
build, which gives the same results (`requirements.txt`), and restart the segmentation
daemon so it loads the new one:

```bash
/home/pi/SU-WaterCam/venv/bin/pip install opencv-python-headless==4.14.0.94
/home/pi/SU-WaterCam/venv/bin/python -c "import cv2; print(cv2.__version__, cv2.__file__)"   # 4.14.0, in venv/
sudo apt purge --autoremove python3-opencv
sudo systemctl restart segformer_daemon
```

If `uname -r` doesn't end in `-rt`, the real-time kernel isn't used and can go too:
`sudo apt purge linux-image-rpi-v8-rt 'linux-image-*-rpi-v8-rt'`. Check
`/boot/firmware/kernel8.img` is still there afterwards.

Turn off apt's background jobs, which download package lists over whatever link is up,
cellular included (`docs/1NCE_SIM_EXHAUSTION.md`), and remove `man-db`: a deployed node
doesn't need man pages, and its timer rebuilds their index. The `dpkg` rule keeps later
installs from adding man pages and docs (copyright notices stay):

```bash
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer man-db.timer
sudo apt purge --autoremove man-db
printf '%s\n' 'path-exclude /usr/share/man/*' 'path-exclude /usr/share/doc/*' \
  'path-include /usr/share/doc/*/copyright' | sudo tee /etc/dpkg/dpkg.cfg.d/01-nodoc
sudo rm -rf /usr/share/man/* && sudo find /usr/share/doc -type f ! -name copyright -delete
```

Updates are then done by hand (`docs/RELEASES.md`).

**Keep these**, which look removable but aren't:

| Package | Why it stays |
|---|---|
| `python3.13` | The venv runs on it (trixie's Python). |
| `curl` | The Witty Pi's `utilities.sh` sets the clock from network time with it. `libcurl` stays anyway for OpenCV and GDAL. |
| `git`, `perl` | `tools/node_version.py` and updates use git, which needs perl. |
| `gcc-14`, `build-essential`, `python3-dev` | `requirements.txt` builds `RPi.GPIO` from source; without them the venv can't be rebuilt on the unit. |
| `nano` (or `vim-tiny`) | An editor for repairs over the serial console. |
| `tio` | Typing AT commands to the mDot (`tio /dev/ttyAMA5`) or the modem by hand. It replaces `minicom`, which the README also mentions but nobody has used on 005 or 006. |

Afterwards, run the health check (1.10) and a test cycle (1.11) as usual.

### 1.3.3 Boot settings, base packages and other system files

Our images come with these; check them on a unit set up another way, and after
an OS upgrade. Reboot after changing `config.txt` or `cmdline.txt`.

**`/boot/firmware/config.txt`:** the settings in `config/config.txt` must be
present:
- `dtoverlay=uart5` and `enable_uart=1`: the mDot is on the Pi 4's alternative
  UART, `/dev/ttyAMA5` (GPIO 12/13, header pins 32/33), not the primary one, whose TX line the Witty
  Pi watches to tell that the Pi has halted;
- `gpio=5=op,dh` (**mDot units only**): drives GPIO5 (header pin 29) high from the
  firmware, so the mDot's PA_6 input reads "Pi is on" for remote start. Without
  it the line depends on the Pi's boot pull-up, which is too weak for the mDot's
  2.1 V minimum input-high level. Never set it on a handheld unit whose capture
  button is on pin 29. Check it after a reboot with `pinctrl get 5`: it must
  show `op` and `| hi` (e.g. `5: op -- pu | hi`; trixie shows the drive as `--`). A Witty Pi L3V7 (firmware ID 0x37) has a daemon that
  reconfigures GPIO5, so don't use that model on an mDot unit;
- `dtparam=i2c_arm=on`, `dtparam=i2c_arm_baudrate=400000`: Witty Pi, IMU, AHT20;
- `dtparam=spi=on`: the Lepton thermal camera;
- `camera_auto_detect=0` with `dtoverlay=ov5647`: the RGB camera;
- `dtoverlay=disable-bt` (Bluetooth off), audio, HDMI and LEDs off, to save power;
- **stock clocks:** no `arm_freq`, `over_voltage` or `initial_turbo` lines
  (an overclock or boot turbo raises the boot inrush that dips the supply;
  [POWER_ANALYSIS.md](POWER_ANALYSIS.md)). `arm_boost=1` is the Pi 4's stock
  1.8 GHz and stays.

`config/config.txt` also has a `spidev.bufsiz` line, which does nothing there:
it belongs in `cmdline.txt`.

**`/boot/firmware/cmdline.txt`:** don't copy `config/cmdline.txt` over it (its
`root=` is generic and it loads the USB-gadget modules). Make sure the one line
contains `spidev.bufsiz=131072` (Lepton frames) and
`cfg80211.ieee80211_regdom=US` (WiFi channels), keeping the image's own
`root=PARTUUID=…`.

**Packages** the software needs:

```bash
sudo apt install i2c-tools gpsd opendoas nftables uhubctl tio
```

(`i2c-tools` for the Witty Pi and the recovery/next-wake services, `gpsd` for
GNSS, `opendoas` for the shutdown/USB/clock rules in 1.9, `nftables` for the
cellular filter in 1.5.1, `uhubctl` for USB power in 1.9, `tio` for the mDot.)
`lrzsz` isn't needed: `mDot-AT-firmware/tools/mdot_serial_flash.py` has its own
YMODEM sender.

**Cellular modem GNSS** (units with the Quectel EC25):

```bash
sudo install -m 755 config/quectel-gnss-enable /usr/local/bin/
sudo install -m 644 config/77-mm-quectel-ignore-gps.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

The helper turns the GNSS engine on (`AT+QGPS=1`) for `quectel-gnss.service`
(1.9); the udev rule keeps ModemManager off the modem's GNSS ports so gpsd can
use them. `config/xtra2.bin` is optional assistance data that speeds up a cold
GNSS fix (README, "XTRA").

**Witty Pi software** comes from the UUGear installer on our images, in
`/home/pi/wittypi`. Settings:
- **Default ON:** on (register 17 = 1) with a **10 s delay** (register 47), so
  the Pi isn't switched on into a supply that is still settling when power
  returns.
- **Low-voltage cut-off and recovery voltage: disabled** (registers 19 and 22 =
  255, register 41 "ignore power mode" = 0). Our units are fed 5 V into the
  Witty Pi's USB-C input (power mode 0, register 7), and in that mode the
  firmware ignores both thresholds: it never blocks an alarm on voltage and
  never shuts down or wakes on it (`canTriggerAlarm()`, the low-voltage and
  recovery checks in the firmware only act in mode 1, the 6-30 V input, or
  with register 41 set). The V50's output is regulated 5 V until the pack is
  empty and then cuts off, so a threshold would give no warning anyway; an
  empty pack is handled by Default ON plus recovery boot (1.9) and the mDot
  watchdog. Don't set register 41: a threshold would then also act on the
  brief dips at power-on and during uploads, and could cut a cycle short.
- **Power-cut delay:** 7 s (register 21 = 70).

Check them with the read-only register dump in
[WITTYPI_SCHEDULING.md](WITTYPI_SCHEDULING.md) section 7 (add 7 and 41), and
set them with `cd ~/wittypi && sudo ./wittyPi.sh` if different.

**Expected from the image, leave enabled:** `fake-hwclock` (keeps the clock
moving forward across boots without the RTC) and `zramswap`. Mask the file
indexer like the other timers in 1.3.2: `sudo systemctl mask locate.timer`.

**Firewall:** no host firewall beyond Tailscale's own rules and the cellular
filter in 1.5.1, so no `ufw` and no stock `nftables.service`.

**Tailscale:** the tailnet ACL lives in the Tailscale admin console, not on the
node and not in this public repository (it names who can reach the units). It
limits the relays (DERP) to nyc, ord and iad, which cuts cellular data. Ask the
tailnet admin for changes.

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
   1 s, and writes it to the system clock and the RTC. That needs internet at
   boot: WiFi, or cellular if the modem autoconnects. A unit whose modem is kept
   off until needed has no internet at boot without WiFi.
3. **Without internet,** `syncTime.sh` takes the time from GPS instead (gpsd,
   through `gps_time.py`), waiting up to 90 s for a fix. The GPS is the
   modem's, and works with its data connection down. With neither, the clock
   and RTC are left alone.
4. **The RTC holds local time.** After a daylight-saving change, the first boot
   runs an hour off until step 2 or 3 corrects it. A unit with neither internet
   nor a GPS fix isn't corrected for the change or for drift, so check its
   clock at each visit.

Install this repo's time sync into the Witty Pi folder (the stock `syncTime.sh`
has no GPS fallback):

```bash
cp /home/pi/SU-WaterCam/config/wittypi/syncTime.sh /home/pi/SU-WaterCam/config/wittypi/gps_time.py /home/pi/wittypi/
```

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
| `area_threshold`                   | points  | 0–100   | Percentage points of water above the unit's baseline that count as a flood, and the rise between captures that counts as growing (default 10) |
| `camera_flood_detection_enabled`   | bool    |         | Node-side flood flags (07 17 detected, 07 27 growing); default `false`. Needs the baseline (section 2.4.1). The water % (07 37) is sent either way |
| `bitmap_refresh_hours`             | hours   | 0–168   | LoRa sends the mask bitmap (08 18) only when the water % moved by `max(1, area_threshold)` points since the last bitmap sent, a flood flag is set or changed, emergency mode is on, or the last one is this old (default 24); 0 sends it every capture. IP uploads send it every capture (`tools/bitmap_policy.py`) |
| `bitmap_method2_enabled`           | bool    |         | LoRa bitmap method 2 (lossless context model, up to 640 px, about twice the pixels in the same bytes); default `true`. `false` goes back to methods 0/1 up to 160 px. Needs an API that decodes it (docs/BITMAP_DECODING_SPEC.md) |
| `stage_threshold`                  | cm      | 0–65535 | Reserved: kept and reported, but unused on the node (the server applies its own stream gauge thresholds) |
| `monitoring_frequency`             | minutes | 1–10080 | How often the unit checks/reports under normal conditions                                         |
| `emergency_frequency`              | minutes | 1–240   | Time between captures (and uploads) in emergency mode and while a remote access session holds the unit awake after its wake's captures (default 10); applied to the running program from the next capture. In emergency mode the unit is off between captures from 3 minutes up (`emergency_sleep`) |
| `photo_interval`                   | seconds | 30–1440 | Time between captures during a normal wake (default 60; three captures, then shutdown); applied to the running program |
| `neighborhood_emergency_frequency` | minutes | 1–1440  | Reporting frequency once a *neighboring* unit signals emergency                                   |
| `ip_fallback_after_hours`          | hours   | 0–168   | One IP upload, even with `ip_upload.enabled` false, when no bitmap was delivered for this long and LoRa holds bitmaps back (default 12; 0 = off). About 90 KB per cellular wake (`tools/ip_fallback.py`) |
| `ip_fallback_min_interval_hours`   | hours   | 0–168   | At most one such fallback upload this often, whether or not it got through (default 6) |

Also confirm before deploying:

- `emergency_mode: false` and `debug_mode: false` — both should be off for a normal deployment
- `ip_upload.enabled` — `true` if this site will use cellular/IP upload in addition to (or instead of) LoRa; if so, fill in `server_url` for the WaterCam API server this unit reports to (already set for our tailnet), and give it this unit's own token: on the dashboard, Management → select the unit → IP access token → Generate (the token is shown once), then run `venv/bin/python tools/set_ip_token.py` on the unit and paste it at the hidden prompt (docs/IP_TRANSMISSION.md, "Installing the IP token"). Once the server enforces tokens (`IP_AUTH=enforce`), a unit without one can't send over IP.
- **LoRa sensor packet:** each capture sends the capture time plus only the fields
  that changed by 5% or more since they were last sent (`data/lora_last_sent.json`).
  Orientation (the IMU's heading, roll, pitch) uses an absolute threshold instead,
  `lora_imu_threshold_deg` (default 2°), allowing for the heading wrapping at 360°.
  Every `lora_full_send_hours` (default 24) all fields go out, since LoRa uplinks are
  unconfirmed and a lost packet would otherwise leave an old value on the server.
  `always_transmit_sensors: true` sends every field every capture.
- **Registration orientation check:** co-registration reuses the cached optical-to-thermal
  transform (`images/registration_transform.json`) on every capture and never re-solves it on
  its own. After each co-registration the node compares the IMU pose with the one recorded for
  that transform and writes `registration_check.json` into the capture folder. `status: shifted`
  means the unit tilted by `coreg_tilt_threshold_deg` (default 5°, from the gravity vector) or
  turned by `coreg_rotation_threshold_deg` (default 15°, heading included). Turns only count when
  the magnetometer is calibrated, since an uncalibrated BNO055 reads heading 0. A flag is a
  prompt to check the alignment, not an error: the cached transform is still used. If the
  alignment is off, re-seed (`tools/seed_registration_cache.py`); if it is fine, run
  `python -m tools.registration_orientation --reset` from `~/SU-WaterCam`. A cache with no
  recorded pose (new, re-seeded, or from before this check) takes the next reading as its
  reference, so install and seed with the unit in its final position.
  The status also goes to the server as `registration_status` (channel `01 08`, LoRa and IP)
  whenever it changes, and the dashboard shows **Check alignment** on the unit's card while it
  is `shifted`.
- **Pose reference for georeferencing:** separately, `tools/pose_monitor.py` watches for the unit
  being knocked, removed or tilted enough to spoil georeferencing (1° of tilt; see
  `docs/SYSTEM_REFERENCE.md`, "Pose levels"). Once the unit is mounted in its final position and
  georeferenced, run `venv/bin/python tools/pose_monitor.py --reset`: the next three stable captures
  become the reference. After re-georeferencing a unit that was knocked, reset it again (or send
  "Accept current orientation" from the dashboard).
- **Transport order** (on by default): LoRa first, then WiFi, then cellular only if
  neither works. The modem is only kept off until needed once it is set up as in
  1.5.1 below.
- **IP fallback** (on by default, `ip_fallback_after_hours` 12): when no bitmap was
  delivered for 12 h because LoRa holds them back (DR0's 11 B fits none), one capture
  goes over IP even with `ip_upload.enabled` false, at most every 6 h; the server can
  also ask for uploads with `17 97 NN` (dashboard **Request IP upload**). It needs
  `ip_upload.server_url`, `device_id` and `api_key` set. About 90 KB per cellular
  wake ([IP_TRANSMISSION.md](IP_TRANSMISSION.md#ip-fallback-when-lora-cant-carry-bitmaps)).

### 1.5.1 Keep the cellular modem off unless LoRa and WiFi fail

The SIM's data is limited (500 MB for its lifetime), and a wake that uses cellular
costs 126–474 KB, almost all of it Tailscale overhead
([CELLULAR_DATA.md](CELLULAR_DATA.md)). So the modem's data connection should stay
down and only come up when the reading can't be sent any other way. The software
already does this ([IP_TRANSMISSION.md](IP_TRANSMISSION.md#transport-order-lora-wifi-cellular)),
but **only for a connection whose autoconnect is off**. Out of the box, `Quectel`
autoconnects and cellular is up on every wake.

Enable it on the unit (run from `/home/pi/SU-WaterCam`):

```bash
sudo nmcli connection modify Quectel connection.autoconnect no
sudo install -m 644 config/polkit/50-watercam-networkmanager.rules /etc/polkit-1/rules.d/
nmcli connection down Quectel      # takes effect now rather than at the next boot
```

The polkit rule lets `pi` (which runs ticktalk with no login session) bring the
connection up and down, and switch the WiFi radio off while LoRa works (1.9.1).
Changing the connection's settings still needs `sudo`. Check it with
`nmcli general permissions`: `network-control` and `enable-disable-wifi` both `yes`.

Keep the modem's interface named `wwan0`. Since Trixie, udev renames it to a name
like `wwp1s0u1u4i4`, and the cellular filter, the data counter and the checks below
all expect `wwan0`. Without this file they never see the modem, and cellular traffic
goes out unfiltered:

```bash
sudo install -D -m 644 config/systemd-network/10-wwan.link /etc/systemd/network/10-wwan.link
sudo udevadm control --reload      # applies the next time USB power brings the modem up
```

After the next cellular session, `journalctl -k -b | grep "renamed from wwan0"` prints
nothing. A line there means the file didn't match and the interface was renamed.

Keep Tailscale's probes to private addresses off the modem. Tailscale tries every
address the API server advertises, including LAN and Docker ones that can never
answer over cellular, and each try costs data ([CELLULAR_DATA.md](CELLULAR_DATA.md)):

```bash
sudo install -D -m 644 config/nftables/watercam-cellular.nft /etc/nftables.d/watercam-cellular.nft
sudo install -m 644 config/watercam-cellular-filter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watercam-cellular-filter.service   # starts whenever wwan0 exists
sudo nft list table netdev watercam_cellular                     # shows the two drop rules
```

Don't enable the stock `nftables.service`: its `/etc/nftables.conf` starts with
`flush ruleset`, which would also remove Tailscale's own firewall rules.

Count the cellular data the unit uses, so the server can track it against the SIM's
allowance (`tools/cellular_usage.py`). The total is reported on the first wake of
each day:

```bash
sudo install -m 644 config/watercam-cellular-usage.service /etc/systemd/system/
sudo install -m 755 config/networkmanager/dispatcher.d/90-watercam-cellular-usage \
    /etc/NetworkManager/dispatcher.d/
sudo systemctl daemon-reload
sudo systemctl enable watercam-cellular-usage.service   # runs whenever wwan0 exists
venv/bin/python -m tools.cellular_usage show            # after a cellular session
```

Check it, as `pi` and without `sudo`:

```bash
nmcli -g connection.autoconnect connection show Quectel    # prints: no
nmcli connection up Quectel && nmcli connection down Quectel   # both succeed
ip route | grep default                                     # no wwan0 route once it's down
```

If `nmcli connection up` says "Insufficient privileges", the polkit rule is missing.
The modem stays registered on the network while its data connection is down. That
costs no measurable power and keeps GPS working.

**What else changes with the modem off.** Anything that needed internet at boot
or at the start of a wake now only has it over WiFi, or later in a wake once
cellular is brought up for an upload:

- **Clock:** the boot-time network time sync (1.4) fails without WiFi. Install
  this repo's `syncTime.sh` and `gps_time.py`, so the clock is set from GPS
  instead (#130).
- **Commands sent over IP** (the dashboard's IP downlink) are normally collected
  at the start of a wake, which skips cellular. On a unit with only cellular,
  they arrive after that wake's upload. On a unit whose LoRa is joined, IP isn't
  used at all, so commands must go by LoRa.
- **The IP upload backlog** is only sent when WiFi or cellular is used.
- **Over-the-air model updates** (planned) also need an IP link: WiFi, or a wake
  that used cellular.
- **GPS is unaffected:** the modem stays registered and its GNSS keeps working.

**Remote access:** with this on, the unit is only reachable over cellular
(Tailscale) during a wake in which LoRa and WiFi both failed. Set it up while you
can still reach the unit another way. Don't turn it on remotely for a unit whose only link is
cellular unless you accept that. To undo it:
`sudo nmcli connection modify Quectel connection.autoconnect yes`.

### 1.6 LoRa registration

This can be tricky. 

If the mDot has already been flashed with our custom firmware (https://github.com/WaterCam-Team/mDot-AT-firmware) then you can proceed to connecting to a gateway after the mDot ID is registered with ChirpStack. If the firmware has not been flashed, follow the instructions in the firmware GitHub. If it has programming headers installed the easy way is to use the USB development board (https://multitech.com/product/multitech-mdot-micro-developer-kit-global/). If not, the same USB serial adapter used for the Raspberry Pi can be used to flash the firmware following the instructions in the repository.

The mDot module's DevEUI must already be registered in ChirpStack before the unit can join the network. **This should be done ahead of time by the ChirpStack administrator**, confirm with them that this specific unit's DevEUI is registered and assigned to the correct application/device profile. The DevEUI is unique per mDot module (see `tools/AT_COMMANDS_REFERENCE.md` for querying the module over serial if you need to look it up).

Once it's confirmed that the device is running our firmware and it is set up in ChirpStack, you can connect to the gateway following the instructions in https://docs.google.com/document/d/1Z83h89x7jlwzRIvbZtyiRxBoMupdoHzL/edit?usp=drive_link&ouid=115248649914453432688&rtpof=true&sd=true

**Set the gateway's sub-band before the first join.** A new mDot uses US915
sub-band 0 (all 64 channels) and our gateway listens on sub-band 1, so only about
one join request in eight is heard and a join can take 15 minutes. On the Pi:

```bash
python3 tools/mdot_subband.py            # sets and saves sub-band 1 and no sub-band search (nothing if already set)
python3 tools/mdot_subband.py --check    # report only
```

It also sets join retries to 0 (`AT+JR=0`). Otherwise an mDot that keeps
failing to join, out of range or at the edge of coverage, moves on to the next
sub-band after a few tries, round all eight. Then it can't join our gateway even
once it's back in range. 006 drifted from 1 to 8 in a day on the bench out of
range, and to 3 at its site.

It stops and restarts `lora_daemon` around it. A gateway on another sub-band:
`--sub-band N`, or `--from-mask` on a unit that has joined and sent an uplink.

**mDot firmware.** Units run `4.1.5-wc2610-sw3` or later (`ATI`). It restarts
the mDot by itself if its firmware stops, or if the Pi is on but the LoRa daemon's
keep-alive (`AT+ALIVE`, every 5 minutes) stops for an hour. Put the unit on
release v2026.10.4 or later (1.3.1) **before** flashing it, or the silence
restart never arms. Flash with `tools/mdot_serial_flash.py` from the firmware
repository (its `docs/BUILD_AND_FLASH.md`): it reads the sub-band, Pi watchdog,
silence limit and join nonce before the flash and puts back any the flash
changed. After any flash, with `lora_daemon` stopped, check by hand:

- `AT+FSB?` is the gateway's sub-band (1). A flash on 005 once left it at 8, and
  the mDot never joined until it was set back (`AT+FSB=1`, `AT&W`).
- `AT+WDOG?` starts with 20 and `AT+SILENCE?` with 60.
- Start `lora_daemon` and leave it at least 5 minutes, then stop it again
  briefly: `AT+SILENCE?` now shows `60,1,…` (armed by the keep-alive). Start
  the daemon again afterwards.

**If every join fails** after a flash or several restarts, the network server is
probably rejecting join nonces it has already seen. Ask the ChirpStack
administrator to flush the device's OTAA nonces, then on the mDot `AT+JN=0,0`
and `AT+JOIN`.

I'd recommend using SSH at this point to connect to the Raspberry Pi instead of serial. On the Raspberry Pi run `tio /dev/ttyAMA5` to issue the commands to connect to the gateway when you know it is in range.

### 1.7 IMU Stage 1 calibration

**Has to be done before the unit is installed outside.** It doesn't have to be
at the site: any magnetically clean outdoor spot works, such as a lawn or open
area at least 5-10 m from cars, buildings, metal fences and power lines. Not a
workbench: computers, power supplies, steel furniture and building rebar bend
the field and end up in the offsets. Why the site itself isn't needed: the
offsets correct the unit's own field (the V75's steel-cased cells, screws,
connectors, boards), which moves with the unit. Whatever the site adds (its
pole, nearby metal) is fixed, and because a mounted camera never turns, it
shows up as one constant heading offset, which Stage 2 measures at the site.

Calibrate the unit fully assembled and powered as deployed: battery in place,
antennas on, lid closed, BNO055 fixed in its final position. Remounting the
sensor means recalibrating.

Each unit needs a unit config JSON for the tool (`unit_config_UFO<NNN>.json` in
`~/SU-WaterCam`, not in git):

```json
{
  "unit_id": "UFO-006",
  "imu_calibration_file": "/home/pi/SU-WaterCam/bno055_calibration.json",
  "imu_mount_offset_deg": 0.0,
  "imu_magnetic_declination_deg": -11.5,
  "imu_heading_correction_deg": 0.0
}
```

`imu_magnetic_declination_deg` is negative for west: about -11.9 in the Syracuse
area. Compute the site's value from its surveyed position with NOAA's World
Magnetic Model, offline, so the coordinates go nowhere (e.g. the `pygeomag`
package with its `WMM_2025.COF`), or use NOAA's calculator.

The tools this guide runs are executable and use the node venv's Python, so
`./tools/bno055_calibration.py ...` (with tab completion) works as well as
`venv/bin/python tools/bno055_calibration.py ...`. Plain `python3` (the system
one) lacks their dependencies.

The calibration has three parts, and they can be done in two goes:

- **Gyroscope and accelerometer: anywhere, indoors included.** They don't
  depend on the magnetic field.
  ```bash
  cd ~/SU-WaterCam && venv/bin/python tools/bno055_calibration.py -u unit_config_UFO006.json --mode calibrate --stages gyro,accel
  ```
  Gyroscope: still on a flat surface. Accelerometer: six positions, base down,
  lid down and four 45-degree tilts, antennas clear. The magnetometer is saved
  as "not calibrated" (zeros, `"mag": null` under `components`), so whatever it
  learned on the bench is never saved or loaded.
- **Magnetometer: outdoors at a magnetically clean spot** (see above), at the
  site if need be. It keeps the saved gyroscope and accelerometer offsets.
  ```bash
  cd ~/SU-WaterCam && venv/bin/python tools/bno055_calibration.py -u unit_config_UFO006.json --mode calibrate --stages mag
  ```
  Slow figure-eights for at least 30 s until `mag` reaches 3. It needs the
  unit in hand, so at the site do it before mounting.

Running without `--stages` does all three at once. Check that
`bno055_calibration.json` lists all three parts under `components` when done.
Until the magnetometer is calibrated, headings are unreliable (the node logs a
warning at boot) and georeferencing will be difficult.

After a reboot, check the offsets load and the status with the unit still
(Ctrl-C to stop; see "Checking calibration status at any time" in
`docs/IMU_CALIBRATION.md`):
`venv/bin/python tools/bno055_calibration.py -u unit_config_UFO006.json --mode log --interval 2`.
Watch `cal=sys/gyro/accel/mag`: `mag` may start below 2 for a few seconds after
boot, then should reach 2-3 and stay there with the unit not moving. Stage 2
needs `mag` at 2 or more.

Stage 2 (`--mode mount` with a surveyed bearing, about a minute with the unit
still) is done at the site after mounting: see `docs/IMU_CALIBRATION.md`.

### 1.7.1 Clear the learned mount position

A unit learns its mount position from its first three consistent GPS fixes, and
from then on alerts (`01 06`, with its position, over LoRa and IP) when two
fixes put it more than `gps_move_alert_m` (default 50 m) away: it may have been
taken or come off its mount. Clear the old position before a unit goes to a new
site, so it learns the new one there:

```bash
python tools/gps_home.py --reset
```

`python tools/gps_home.py` shows whether a position has been learned and
whether the unit counts as moved.

Any wake with a good fix learns a position, including the bench tests in 1.10
and 1.11 next to a window. So clear it again as the last bench step (1.14), and
at the site check that it was learned there (2.4). A home learned at the bench
makes every `04 03` distance on the dashboard the distance from the bench to
the site and, if that is more than `gps_move_alert_m`, the unit alerts as
moved once it is mounted.

Clear any flood baseline from a previous site the same way
(`python tools/flood_baseline.py --reset`); the new one is set at the site
(section 2.4.1).

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
        config/wittypi-next-wake.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable watercam.service 2>/dev/null   # if it was ever enabled
sudo systemctl enable --now lora_daemon.service segformer_daemon.service
sudo systemctl enable ticktalk.service wittypi-recovery.service wittypi-boot-mark.service
sudo systemctl enable --now wittypi-next-wake.service
```

`ticktalk` and the WittyPi services start on the next boot.

**Unit files change with releases.** The installed units are copies, so a
`git checkout` of a new release does not update them. They also carry systemd sandbox settings
(`ticktalk`, `lora_daemon`, `segformer_daemon`, `powerlog`): after copying,
`systemd-analyze security lora_daemon` should read about 2.5 OK and `ticktalk` about 7.1 MEDIUM
(it runs doas, so it can't drop privileges). `ticktalk.service`,
`lora_daemon.service` and `segformer_daemon.service` set
`Environment=PYTHONUNBUFFERED=1`, so each `print()` reaches the journal when it
happens (with buffered stdout, journal timestamps bunch together: two captures
60 s apart are logged within the same second). On a unit installed before that,
copy the three files again as above and run `sudo systemctl daemon-reload`;
check with `systemctl show ticktalk.service -p Environment`. The new unit takes
effect when the service next starts (next wake, or restart lora_daemon and
segformer_daemon now).

**ticktalk watchdog.** `ticktalk.service` sets `WatchdogSec=15min`: each capture
iteration sends systemd a heartbeat (`tools/systemd_watchdog.py`, from
`call_shutdown`), and if none arrives for 15 minutes, because the graph has
stalled (a dead worker, a wedged read), systemd kills ticktalk and
`Restart=on-failure` starts it again 10 s later. Iterations are about a minute
apart, so a normal wake never comes near it. In emergency mode the interval is
`emergency_frequency` (10 minutes by default, up to 240), so the capture-loop
watcher (`tools/photo_interval_watcher.py`) sends the heartbeat between
iterations, for as long as the last iteration is no older than the current
period plus 15 minutes. `systemctl show ticktalk -p
WatchdogUSec -p WatchdogTimestamp` shows the setting and the last heartbeat; a
restart shows as `Watchdog timeout` in `journalctl -u ticktalk`. Copy the unit
again on units installed before it (as above).

**Hard maximum awake time.** A wake is normally 10-15 minutes and `ticktalk`
powers the Pi off at the end of it. `max-awake.timer` is the backstop for when
that doesn't happen (a hung capture, a restart loop): every 5 minutes it checks
the uptime and, past `max_awake_minutes` (`runtime_config.json`, default 60,
`0` turns it off), runs the same `doas /usr/sbin/shutdown -h now` that
`call_shutdown()` uses, so the Witty Pi sees a normal halt and
`wittypi-next-wake.service` arms the next slot. It never powers off while a
remote debug session is active, while `/etc/watercam/hold-awake` exists
(`sudo touch` it to keep a unit up on the bench, remove it afterwards), or in
emergency mode until that has run `emergency_max_hours` (default 24, `0` = no
expiry) plus 15 minutes from `emergency_since`. The capture graph normally ends
emergency mode at `emergency_max_hours`; this covers a hung graph, which would
otherwise keep the unit up until the battery is flat. (The Witty Pi's shutdown
alarm, moved to an hour after that end, is the last resort if the Pi itself hangs.)
Past that deadline it turns `emergency_mode` off and powers off as above. A
missing or unusable `emergency_since` counts from boot. `tools/max_awake.py
check --dry-run` says what it would do.

```bash
sudo cp config/max-awake.service config/max-awake.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now max-awake.timer
```

On a unit that is meant to stay on (a development unit, or one held on for bench
work with `ticktalk` stopped), create the hold flag **before** enabling the
timer, or it powers the Pi off within 5 minutes of passing the limit:
`sudo mkdir -p /etc/watercam && sudo touch /etc/watercam/hold-awake`.

`lora_daemon.service` owns the mDot serial port, and every LoRa send or
receive in `ticktalk` goes through it. Without it the unit has no LoRa.
`segformer_daemon.service` keeps the flood-segmentation model loaded. Without
it `ticktalk` falls back to loading the model on every cycle, about 40 s
instead of about 2 s. Both start before `ticktalk.service`; `--now` also starts
them straight away for the test cycle in 1.11.

`wittypi-next-wake.service` checks the WittyPi's startup alarm every time
the unit shuts down. The WittyPi's own script arms its alarms only at boot,
and after a boot between slots (the button, say) it skips the next slot. At
shutdown this service arms the next `schedule.wpi` slot instead whenever the
armed wake is missing, past or later than that slot. It never moves a wake
later, and leaves a recovery boot's deliberate wake alone.

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

Keep USB off unless something needs it. The cellular modem draws 0.6-0.9 W
whenever it has power, even idle, and the microphone about 0.1 W
(docs/POWER_ANALYSIS.md). `usb-power-off.service` turns USB off about 5 s into
boot and nothing turns it back on by default: `tools/usb_power.py` turns it on
for the daily GPS fix (`tools/gps_window.py`: once the last live fix is
`gps_fix_interval_h` old, default 24 h, after a knock or removal, or when the
server asks), for a cellular upload or remote session, and for recording, and
off again when the last of these is done. Wakes in between use the last saved
fix (`gps_last_fix.json`, kept for `gps_cache_max_age_h`, default 30 days),
marked as cached. A Pi 4's USB ports share one power switch, so the modem and
microphone are always on or off together. The four `uhubctl` lines from
`config/doas.conf` let `pi` turn USB on and off.

```bash
sudo apt install uhubctl
sudo cp config/usb-power-off.service config/quectel-gnss.service /etc/systemd/system/
sudo cp config/wittypi/syncTime.sh /home/pi/wittypi/
# Units set up before 2026-10-22: the old timer turned USB on 8 s after every boot
sudo systemctl disable --now usb-power-on.timer 2>/dev/null; sudo rm -f /etc/systemd/system/usb-power-on.{timer,service}
sudo systemctl daemon-reload
sudo systemctl disable quectel-gnss.service   # drops the old gpsd.service link
sudo systemctl enable quectel-gnss.service usb-power-off.service
grep uhubctl config/doas.conf | sudo tee -a /etc/doas.conf && sudo doas -C /etc/doas.conf
```

Set the clock from the Witty Pi RTC at the start of each boot. Otherwise the
system clock starts at the last shutdown's time (on a 30-minute schedule, about
28 minutes behind) until the Witty Pi daemon corrects it, and `tailscaled`
starts with that wrong time and may not connect for part of the wake.
`rtc-early-clock.service` runs before `tailscaled`, the LoRa daemon and
`ticktalk`; it only moves the clock forward, ignores an RTC that lost power or
reads an implausible time, and gives up after 5 s without a Witty Pi. The clock
still counts as unverified (`clock_unverified`) until network, GPS or server
time confirms it.

```bash
sudo cp config/rtc-early-clock.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rtc-early-clock.service
```

Install the clock helper. A unit with no WiFi at boot gets no network time, and
without a GPS fix no GPS time either; after each IP upload it sets its clock and
the Witty Pi RTC from the server's reply when they're more than 5 s off:

```bash
sudo install -o root -g root -m 755 config/watercam-set-clock /usr/local/sbin/
grep watercam-set-clock config/doas.conf | sudo tee -a /etc/doas.conf && sudo doas -C /etc/doas.conf
```

`quectel-gnss.service` now starts whenever the modem's AT port appears, and
hands the GPS port to gpsd, which was already running. To keep USB on from the
start on a bench unit, run `sudo mkdir -p /etc/watercam && sudo touch /etc/watercam/usb-on-at-boot`.
To turn it on by hand, run `doas uhubctl -l 1-1 -a on`.

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

Rotate the logs that otherwise only grow: the power log, the bench
`~/throttle_log.csv`, ticktalk's `output.log` and `data/temp_humidity.csv`
(each kept to four 5 MB compressed copies). `~/wittypi/wittyPi.log` is left
alone: this boot's offset into it is how the unit tells this boot's lines from
earlier ones.

```bash
sudo cp config/logrotate-watercam.conf /etc/logrotate.d/watercam
sudo logrotate -d /etc/logrotate.d/watercam    # check it parses
```

### 1.9.1 LoRa health, WiFi only when needed, photo retention

**LoRa health.** Each boot the unit asks the network whether it hears it: a
link check (`AT+NLC`) that the network server must answer with the signal
margin and the number of gateways (`tools/lora_health.py`). The mDot's
"joined" flag isn't enough: it's a saved session, still set with no gateway in
range. LoRa counts as healthy with an answer from a gateway at least
`lora_min_margin_db` (default 5 dB) above the noise floor, and at most one
failed check in the last three wakes. History: `data/lora_health.json`.

**WiFi off while LoRa works.** Healthy LoRa turns the WiFi radio off for the
wake, as cellular already is; otherwise WiFi comes on so the IP uplink can use
it before cellular (`tools/wifi_policy.py`). NetworkManager remembers the
radio state, so the next boot starts with WiFi off too. WiFi stays on during a
remote access session, while the unit counts as moved from its mount, and with
`"wifi_when_lora_healthy": true` in `runtime_config.json`. **Set that on bench
units before updating them**, or a unit with good LoRa drops off SSH. To reach
a deployed unit with WiFi off, start a remote access session from the
dashboard: WiFi only (`18 99`), WiFi then cellular (`18 9A`), or cellular
directly for units without WiFi (`18 98`).

**Photo retention.** By default every file is kept (`data_retention_days` 0)
while the system is being developed. Set a number of days to remove JPEG and
TIFF files (never masks, bitmaps or metadata) older than that, and audio too.
Whatever the setting, when the card's free space is at or below
`storage_low_free_pct` (20 %) each wake removes the oldest JPEGs and TIFFs until
`storage_target_free_pct` (30 %) is free (`tools/data_retention.py`), so a full
card can't stop the unit. The photos aren't uploaded, so this is the only copy.

### 1.9.2 Optional, legacy and bench-only services

- **Remote debug session** (dashboard "Remote access"): install
  `config/remote-debug.service` and `.timer` and enable the timer, as in
  [REMOTE_DEBUG_SESSION.md](REMOTE_DEBUG_SESSION.md). Install it on every unit
  that should accept remote-access requests (`sudo systemctl enable --now
  remote-debug.timer`); the startup health check (1.10) warns when it isn't enabled.
- **filebrowser:** off on deployed units (`sudo systemctl disable --now
  filebrowser`), it costs cellular data ([1NCE_SIM_EXHAUSTION.md](1NCE_SIM_EXHAUSTION.md)).
- **Legacy, never install:** `watercam.service` (replaced by ticktalk; the unit file is gone from `config/`),
  `gps.service` (replaced by `quectel-gnss.service`), `button.service`,
  `temperature.service` (removed from `config/`); `config/dhcpcd.conf` (NetworkManager is used) and
  `config/avrdude_gpio.conf` (Witty Pi firmware flashing only).
- **Bench-only:** the `throttle-log` service (logs `get_throttled` at boot and
  shutdown to `~/throttle_log.csv`), `~/test_wake.sh`, and any ticktalk drop-ins
  under `/etc/systemd/system/ticktalk.service.d/`. Remove them before deploying.

### 1.10 Run the startup health check

`tools/initial_health_check.py` checks CPU temperature, WittyPi voltages, GPS fix, and IMU availability in one shot, and sends a LoRa alert on failure.

```bash
cd /home/pi/SU-WaterCam
venv/bin/python tools/initial_health_check.py --no-alert; echo "exit code: $?"
```

It prints `Health check: OK` or `FAIL` with each reason, then every reading.
Without `--no-alert`, a failure also queues a LoRa alert.

Exit code `0` means everything passed. If it fails, read the printed failure reasons (`gps_unavailable`, `imu_unavailable`, `internal_voltage_low_*V`,
etc.) and resolve them before moving on. GPS will very likely fail indoors, re-check it outdoors in Phase 2.

After the readings it prints `WARN` lines for setup mistakes (they don't change
the exit code): on an mDot unit, GPIO5 not driven high (`pinctrl get 5` should say
`op ... | hi`; see 1.3.3) or a Witty Pi L3V7 (firmware ID 0x37); and
`remote-debug.timer` not enabled.

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

### 1.11.1 Power wiring

- **V50 to Witty Pi through the Y-adapter:** both V50 USB-A ports into the
  dual USB-A to single USB-C cable, into the Witty Pi's USB-C input. Each A port
  is rated 5 V / 2 A (3 A for both, Voltaic's booklet); a cellular upload
  during segmentation has peaked at 2.06-2.21 A, past one port's rating
  ([POWER_ANALYSIS.md](POWER_ANALYSIS.md#peaks-matter-more-than-averages),
  [UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md)). Use a short, thick USB-C
  cable and seat both ends firmly: a marginal cable dropped 006's power at wakes.
- **Prefer the P2 power feed to the USB-C socket.** 006's Witty Pi socket cut
  power whenever the plug was pushed or wiggled, and the box tilted, with two
  different adapters and cables (logged as "power supply is newly connected",
  with no voltage sag first). Wire the Y-cable to the Witty Pi's P2 header
  instead ([WITTYPI_P2_POWER_HARNESS.md](WITTYPI_P2_POWER_HARNESS.md)). On a unit
  still on USB-C, push and wiggle the plug while watching for a new boot
  (`grep -a "System starts up because" ~/wittypi/wittyPi.log | tail -3`).
- **Secure the mDot's wiring.** The mDot runs from the Witty Pi's own 3.3 V rail
  (P3 header), so touching its jumper wires reset the whole unit on 005, and a
  lifted mDot ground left the mDot hung. Solder or latch its VDD and GND, connect
  PA_6 to GPIO5 (pin 29), and tug-test each wire during the wiggle test.
- **Units without the PCB: tape the switch MOSFET down.** Without the HAT, the
  MOSFET that presses the Witty Pi switch (gate on the mDot's PB_1, a 10 kΩ
  pull-down to GND) hangs loose on its leads and jumpers, and moving it or the
  wires near it cut 006's power three times. Tape it (or hot-glue it) down to the
  board or case, put heat-shrink over its leads and the resistor's joints, and
  connect or disconnect any of these wires only with the unit off
  ([WITTYPI_P2_POWER_HARNESS.md](WITTYPI_P2_POWER_HARNESS.md#secure-the-mdot-wiring)).
- **The A ports are always on:** anything in a USB-A port presses the V50's
  Always On switch, so the pack never auto-shuts off under a light load.
- **Never use the V50's top USB-C port** while it powers a unit: anything in it
  turns Always On off on the A ports and sags the output. Charge on the bench
  through the **side** port, which keeps the pack full while it powers the unit.
- **Solar:** the panel goes into the V50's solar input (at most about 2 A).
- Check after wiring: `get_throttled` is `0x0` after a capture cycle (1.11) and
  the Witty Pi reports Vout around 4.9 V (`~/powerlog/powerlog.csv`).
- **Pass rule: Witty Pi output voltage at least 4.7 V under load, and
  `get_throttled` `0x0`**, during a wake: the `vout` column of
  `~/powerlog/powerlog.csv` while the unit segments or uploads, or the startup
  health check, which fails below 4.7 V (`internal_voltage_low_*V`). The Pi's
  under-voltage threshold is about 4.63 V. A weak supply or cable shows up as a
  low `vout` and as `get_throttled` `0x50000` (under-voltage has occurred, and
  throttling has occurred) at shutdown: swap in a known-good 5.1 V supply and a
  short, thick USB-C cable, then retest. Don't judge by `vin`: the Witty Pi's
  input reading is 0.4-0.5 V low on several boards (about 4.5 V from a 5.0 V
  supply, below its own output reading), so it is only logged.

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

Whatever schedule you install, keep a copy as `schedule.wpi.deploy` (`cp /home/pi/wittypi/schedule.wpi /home/pi/wittypi/schedule.wpi.deploy`). If `schedule.wpi` is ever missing when emergency mode ends, the software puts that copy back (else the newest `schedule.wpi.deploy-*` or `schedule.wpi.bak-*`, else the daylight preset above) rather than generating a generic schedule (`tools/witty_pi_4.py` `fallback_schedule_sources`).

Then install this repo's `beforeScript.sh`, which lets the WittyPi arm the next wake about 15 s after boot instead of about 55 s:

```bash
cp /home/pi/SU-WaterCam/config/wittypi/beforeScript.sh /home/pi/wittypi/beforeScript.sh
```

### 1.14 Post-install smoke checklist

Run through this on the bench, after the last reboot and before the unit leaves
for the site:

- [ ] `pinctrl get 5` shows `op` and `| hi` (mDot units only; not on a handheld with the
      capture button on pin 29).
- [ ] Units without the PCB: the switch MOSFET is taped down, its leads and the
      pull-down resistor are insulated, and nudging them doesn't restart the unit
      (no new "System starts up" line in `~/wittypi/wittyPi.log`) (1.11.1).
- [ ] `systemctl is-enabled remote-debug.timer max-awake.timer` prints `enabled`
      twice (`/etc/watercam/hold-awake` must not exist).
- [ ] `systemctl is-enabled rtc-early-clock.service` prints `enabled`, and after a
      reboot `journalctl -b -u rtc-early-clock` shows `system clock ... -> RTC ...`
      or a `skip:` reason (an RTC that lost power must be set before deploying).
- [ ] `venv/bin/python tools/initial_health_check.py --no-alert` prints no
      `WARN` lines (GPS may fail indoors).
- [ ] **Power feed:** on the P2 harness, or the USB-C plug passed a push and
      wiggle test with no new boot; then tilt the closed box in every direction,
      including the mounting angle, again with no new boot (1.11.1).
- [ ] **mDot:** `AT+FSB?` is 1 and `AT+JR?` is 0 (`tools/mdot_subband.py --check`),
      `AT+WDOG?` starts with 20. On `4.1.5-wc2610-sw3` or later, `AT+SILENCE?` shows
      `60,1,…` after the LoRa daemon has run 5 minutes (stop it to query, then start
      it again; 1.6). Those builds restart every 20 s when they can't join (seen with
      sw6 and sw7, 2026-10-22; mDot-AT-firmware #22). A unit that may be out of LoRa
      range when it starts stays on `4.1.5-wc2610` until that is fixed, as 006 does.
- [ ] `sudo doas -C /etc/doas.conf` passes and `/etc/doas.conf` matches
      `config/doas.conf` (`sudo diff config/doas.conf /etc/doas.conf`).
- [ ] Witty Pi output voltage is at least 4.7 V under load: `vout` in
      `~/powerlog/powerlog.csv` during a capture cycle, `get_throttled` `0x0`,
      and no `internal_voltage_low_*V` from the health check (1.11.1).
- [ ] **IP token:** if `ip_upload.enabled` is true, the operator installs the
      unit's token from their own terminal with `tools/set_ip_token.py`
      ([IP_TRANSMISSION.md](IP_TRANSMISSION.md#installing-the-ip-token)); don't
      paste it into a chat or command line. A unit without a token is
      unauthenticated on the dashboard.
- [ ] One full cycle (1.11) reached the dashboard, and `ticktalk.service` is
      enabled.
- [ ] The dashboard shows the unit's transport as LoRa before it leaves the
      bench. A routine IP upload switches it to `ip`; set it back in the device
      panel's Transport section.
- [ ] `systemctl show ticktalk.service -p Environment` includes
      `PYTHONUNBUFFERED=1` (the unit file from this release, 1.9).
- [ ] **Tailscale:** the unit appears in the admin console with the
      `tag:watercam` tag and no personal owner (the ACL itself is kept by the
      tailnet admin, outside this repository).
- [ ] **Last:** clear the mount position learned during the bench tests,
      `venv/bin/python tools/gps_home.py --reset`, then shut down without
      another test cycle (1.7.1).

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

Check the mount position: `venv/bin/python tools/gps_home.py` should show a
home set after the unit was mounted, with no moved state, and the dashboard's
move distance should stay well under `gps_move_alert_m`. If the home was set before the
install (a wake on the way, or a bench home that was never cleared), run
`venv/bin/python tools/gps_home.py --reset` so it learns the mount position on
its next wakes.

### 2.4.1 Set the flood baseline

The camera sees some water in normal conditions, so a flood is judged against
the water percentage at installation. Once a capture from the final position
has been segmented (the dashboard shows its mask and water %), on the unit:

```bash
cd ~/SU-WaterCam
venv/bin/python tools/flood_baseline.py --set-from-latest   # or --set N, N = 0-100
venv/bin/python tools/flood_baseline.py --show
```

Pick a capture in normal conditions: after rain or with a high river, use
`--set N` with the usual level instead. Flood detection stays off until
`camera_flood_detection_enabled` is set to `true` in `runtime_config.json`; then
the unit sends flood detected (07 17) when the water percentage is at least
baseline + `area_threshold`, and flood growing (07 27) when it rose by
`area_threshold` or more since the previous capture. After moving or re-aiming
the camera, run `--reset` and set the baseline again.

### 2.5 Final weatherproofing

Confirm all cable entries are sealed, connections to the solar panel are secure, and the case is fully closed before leaving the site.

---

## Before You Leave: Remote Verification

- **Dashboard**: check for regular incoming readings at the expected
  `monitoring_frequency` cadence.
- **Remote access**: `ssh <hostname>` over Tailscale (only works while the Pi is powered on per its WittyPi schedule
- **Remote Start**: if the system is in range of a LoRa gateway and connected to it you can send a remote start signal from the Chirpstack server or through the dashboard (the dashboard is still under development so it might have issues)
