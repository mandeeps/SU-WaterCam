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

**Tailscale:** the tailnet ACL (`config/tailscale-acl.txt`, applied in the
Tailscale admin console, not on the node) limits the relays (DERP) to nyc, ord
and iad, which cuts cellular data. The live ACL has diverged from the repo
copy; check the admin console when changing it.

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
| `area_threshold`                   | %       | 0–100   | Flood-extent area (as % of frame) that triggers elevated/emergency reporting                      |
| `stage_threshold`                  | cm      | 0–65535 | Water stage (level) threshold                                                                     |
| `monitoring_frequency`             | minutes | 1–10080 | How often the unit checks/reports under normal conditions                                         |
| `emergency_frequency`              | minutes | 1–1440  | How often it reports once a threshold is exceeded (should be shorter than `monitoring_frequency`) |
| `photo_interval`                   | seconds | 30–1440 | Time between captures while the unit is awake (default 60); applied to the running program        |
| `neighborhood_emergency_frequency` | minutes | 1–1440  | Reporting frequency once a *neighboring* unit signals emergency                                   |

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
- **Transport order** (on by default): LoRa first, then WiFi, then cellular only if
  neither works. The modem is only kept off until needed once it is set up as in
  1.5.1 below.

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
python3 tools/mdot_subband.py            # sets and saves sub-band 1 (nothing if already set)
python3 tools/mdot_subband.py --check    # report only
```

It stops and restarts `lora_daemon` around it. A gateway on another sub-band:
`--sub-band N`, or `--from-mask` on a unit that has joined and sent an uplink.

I'd recommend using SSH at this point to connect to the Raspberry Pi instead of serial. On the Raspberry Pi run `tio /dev/ttyAMA5` to issue the commands to connect to the gateway when you know it is in range.

### 1.7 IMU Stage 1 calibration

**Has to be done before the unit is installed outside**

```bash
python tools/bno055_calibration.py --unit-config <unit_config_file>.json --mode calibrate
```

Verify `bno055_calibration.json` shows `"mag": 3` when done. Without it heading data will be unreliable and georeferencing will be difficult.

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

Keep USB off through early boot. The cellular modem draws its start-up
current as soon as USB has power. `usb-power-off.service` turns USB off about
5 s into boot and `usb-power-on.timer` turns it back on 8 s after boot, past
the kernel and early userspace. The wake cycle, `cellular_up()` and
`syncTime.sh` also turn it on, in case the timer didn't run. The modem's GPS
needs 40–50 s for a fix after that, so the wake's first LoRa step uses the last
saved fix (`gps_last_fix.json`, kept for `gps_cache_max_age_h`, default two
days). A Pi 4's USB ports share one power switch, so the modem and microphone
are always on or off together.
The two `uhubctl` lines from `config/doas.conf` let `ticktalk` turn USB on.

```bash
sudo apt install uhubctl
sudo cp config/usb-power-off.service config/usb-power-on.service config/usb-power-on.timer \
        /etc/systemd/system/
sudo cp config/quectel-gnss.service /etc/systemd/system/
sudo cp config/wittypi/syncTime.sh /home/pi/wittypi/
sudo systemctl daemon-reload
sudo systemctl disable quectel-gnss.service   # drops the old gpsd.service link
sudo systemctl enable quectel-gnss.service usb-power-off.service usb-power-on.timer
grep uhubctl config/doas.conf | sudo tee -a /etc/doas.conf && sudo doas -C /etc/doas.conf
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
  that should accept remote-access requests.
- **filebrowser:** off on deployed units (`sudo systemctl disable --now
  filebrowser`), it costs cellular data ([1NCE_SIM_EXHAUSTION.md](1NCE_SIM_EXHAUSTION.md)).
- **Legacy, never install:** `watercam.service` (replaced by ticktalk; the unit file is gone from `config/`),
  `gps.service` (replaced by `quectel-gnss.service`), `button.service`,
  `temperature.service`; `config/dhcpcd.conf` (NetworkManager is used) and
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

### 1.11.1 Power wiring

- **V50 to Witty Pi through the Y-adapter:** both V50 USB-A ports into the
  dual USB-A to single USB-C cable, into the Witty Pi's USB-C input. Each A port
  is rated 5 V / 2 A (3 A for both, Voltaic's booklet); a cellular upload
  during segmentation has peaked at 2.06-2.21 A, past one port's rating
  ([POWER_ANALYSIS.md](POWER_ANALYSIS.md#peaks-matter-more-than-averages),
  [UNIT006_POWER_FAILURE.md](UNIT006_POWER_FAILURE.md)). Use a short, thick USB-C
  cable and seat both ends firmly: a marginal cable dropped 006's power at wakes.
- **The A ports are always on:** anything in a USB-A port presses the V50's
  Always On switch, so the pack never auto-shuts off under a light load.
- **Never use the V50's top USB-C port** while it powers a unit: anything in it
  turns Always On off on the A ports and sags the output. Charge on the bench
  through the **side** port, which keeps the pack full while it powers the unit.
- **Solar:** the panel goes into the V50's solar input (at most about 2 A).
- Check after wiring: `get_throttled` is `0x0` after a capture cycle (1.11) and
  the Witty Pi reports Vout around 4.9 V (`~/powerlog/powerlog.csv`).

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
