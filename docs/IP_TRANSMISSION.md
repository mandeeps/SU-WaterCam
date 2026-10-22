# IP Transmission — WaterCam over WiFi/Cellular

**Status:** Complete — standalone client and TickTalkPython integration both implemented.  
**Branch:** `CellTransmit`  
**Last updated:** 2026-04-14

---

## Overview

The WaterCam device normally sends sensor data over LoRa via an mDot modem.
On deployments with WiFi or cellular access, the same data can be sent directly
to the FastAPI server over HTTP.  Both transports produce identical readings in
InfluxDB and the dashboard — the only difference is the delivery path.

```
Sensors ──┬── LoRa (mDot → ChirpStack → POST /chirpstack/uplink)
           └── IP  (requests → POST /ip/uplink)
                            └── GET /ip/downlink/{device_id}  (polling)
```

The server stores every command once and offers it over IP (in `/ip/uplink` and
`/ip/downlink` replies) until the node acks it; a device with a LoRa EUI also gets
it over LoRa. Whichever ack arrives first completes it
([SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md#command-frames)).

---

## Files

| File | Purpose |
|---|---|
| `tools/transmit_ip.py` | `IPTransmitter` class — uplink POST and downlink poll |
| `runtime_config.json` → `ip_upload` | All user-tunable settings |
| `tests/test_ip_upload.py` | Integration tests against a live server |
| `docs/IP_TRANSMISSION.md` | This document |

---

## Configuration (`runtime_config.json`)

Add or edit the `ip_upload` block:

```json
"ip_upload": {
  "enabled": false,
  "server_url": "http://localhost:8000",
  "api_key": "",
  "device_id": "watercam-001",
  "timeout_s": 15,
  "retry_attempts": 3,
  "retry_backoff_s": 2,
  "fallback_to_lora": true,
  "only_if_lora_unavailable": true,
  "cellular_connection": "Quectel",
  "cellular_connect_timeout_s": 90,
  "wifi_grace_s": 45
}
```

| Key | Type | Description |
|---|---|---|
| `enabled` | bool | Master switch. Set `true` to activate IP transport. |
| `server_url` | str | Base URL of the FastAPI server, no trailing slash. |
| `api_key` | str | This unit's own IP API token, sent as `Authorization: Bearer <token>` on uplinks and downlink polls. Issued once per device on the dashboard (Management → IP access token); install it with `tools/set_ip_token.py` (below) rather than editing the file by hand. Required when the server runs `IP_AUTH=enforce`; until then a missing token is accepted and logged (API `docs/IP_TRANSPORT.md`). |
| `device_id` | str | Logical device identifier used for all uplinks and downlink polling. Must be unique per deployment. |
| `timeout_s` | int | Per-request timeout in seconds. |
| `retry_attempts` | int | Max POST attempts before giving up (backoff between attempts). |
| `retry_backoff_s` | float | Base sleep between retries. Before attempt N, waits `retry_backoff_s * 2^(N-1)` seconds (exponential backoff). |
| `fallback_to_lora` | bool | Intent flag for the TickTalkPython integration layer: if IP fails, fall back to LoRa. Not enforced by `transmit_ip.py` itself. |
| `only_if_lora_unavailable` | bool | Default `true`: skip the IP uplink while the mDot is joined (LoRa first). `false` sends over both. |
| `cellular_connection` | str | Default `"Quectel"`: the LTE modem's NetworkManager connection, brought up only when the server can't be reached over WiFi. Managed only if its autoconnect is off. `""` leaves the modem alone. See [Transport order](#transport-order-lora-wifi-cellular). |
| `cellular_connect_timeout_s` | int | How long `nmcli` may take to bring the connection up (default 90; re-attaching took 3–51 s on 006). |
| `wifi_grace_s` | int | Default 45. When the server isn't reachable but a saved WiFi network is in range (in NetworkManager's last scan) or WiFi is connected, wait up to this long for the server over WiFi before bringing up cellular. A weak signal can take a minute to authenticate at boot. Skipped when no saved network is in range, so a cellular-only unit loses no time. `0` disables it. |

---

## `IPTransmitter` Class

```python
from tools.transmit_ip import IPTransmitter

tx = IPTransmitter()  # reads runtime_config.json automatically
```

### Constructor options

```python
IPTransmitter(
    config_path="runtime_config.json",  # path to config
    override_url="http://...",          # skip config server_url
    override_device_id="dev-001",       # skip config device_id
)
```

### `send_uplink(channels, device_ts=None) → dict`

POST sensor readings to `/ip/uplink`.

**`channels`** — list of channel dicts. Each channel encodes one sensor value:

```python
channels = [
    {"code": "00 01", "payload_hex": "000000006710AB12"},  # device timestamp
    {"code": "02 01", "payload_hex": "00000050"},          # battery 80%
    {"code": "05 01", "payload_hex": "0898"},              # temperature 22.00°C
    {"code": "06 01", "payload_hex": "3D"},                # humidity 61%
    {"code": "04 01", "payload_hex": "028F5C2AFAD47800"},  # GPS lat/lon
    {"code": "07 17", "payload_hex": "00000001"},          # flood detected
    {"code": "08 18", "payload_hex": "FF00FF00..."},       # flood bitmap (variable)
]
result = tx.send_uplink(channels, device_ts=int(time.time()))
```

**Returns:**
```python
{
    "success": True,
    "status_code": 201,
    "response": { "device_id": "...", "decoded": {...}, "ts": "..." },
    "error": None,
    "attempts": 1
}
```

On failure `success` is `False` and `error` contains a human-readable message.
4xx errors are **not retried** (client mistake). 5xx and connection errors are
retried up to `retry_attempts` times.

### `poll_downlink() → dict`

GET `/ip/downlink/{device_id}` to retrieve the commands waiting for this node.

```python
result = tx.poll_downlink()
if result["success"]:
    for cmd in result["commands"]:
        print(cmd["id"])        # server id, used to ack it
        print(cmd["frame"])     # hex command frame: [seq][code][value]...
```

The server offers a command until the node acks it, so polling twice is
harmless. The node applies each frame once (it remembers `seq` and a hash) and
acks every offer with `post_acks()` (`POST /ip/ack`); acks that can't be posted
are kept and sent in the next uplink. Frame format, result codes and dedup:
[SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md#command-frames).

A wake polls three times: at the start (often skipped, the network isn't up
yet), after an IP uplink is delivered, and **just before shutdown**
(`tools/last_downlink_check.py`, from `call_shutdown`). The last one polls up
to twice (one poll returns everything pending; the second catches a command sent
meanwhile) and never brings a connection up. If a command turns
emergency mode on or starts a remote access session, the node doesn't power
off. LoRa needs no poll: Class C downlinks reach the mDot as soon as they're sent.

**Returns:**
```python
{
    "success": True,
    "status_code": 200,
    "commands": [                 # empty if nothing is pending (also for a 404)
        {"id": 412, "frame": "9c219118"}
    ],
    "error": None
}
```

A server that is not trusted (not on the tailnet, not HTTPS) is not polled:
`commands` is empty and `untrusted` is true.

### `post_acks(acks) → bool`

POST `/ip/ack` with `{"device_id": ..., "acks": [{"id": 412, "result": 0}]}`
(`result`: 0 applied, 1 partly, 2 rejected). True on a 2xx reply. Otherwise the
acks are stored (`data/pending_acks.json`) and `send_uplink()` adds them to the
next `/ip/uplink` body as `"acks"`, clearing them once the server answers.

### `is_reachable() → bool`

Quickly checks if `/health` responds with HTTP 200. Use this before deciding
whether to attempt IP or fall back to LoRa.

---

## Channel Encoding Reference

The server's `CHANNEL_REGISTRY` maps 2-byte codes to sensor fields.  The
`payload_hex` for each channel must be exactly the right length and use
**big-endian byte order**.

| Code | Field | Bytes | Encoding |
|---|---|---|---|
| `00 01` | `device_ts` | 8 | uint64 — UNIX seconds |
| `01 04` | `emergency_status` | 4 | uint32 bool (0 or 1) |
| `02 01` | `battery_pct` | 4 | uint32 (0–100) |
| `03 01` | `imu_block` | 12 | 3× int32 accel (mg), or custom IMU packing |
| `01 06` | `move_threshold` | 4 | uint32 bool: 1 while the unit is away from its mount position (`tools/gps_home.py`) |
| `04 01` | `gps_block` | 8 | int32 lat\_microdeg, int32 lon\_microdeg |
| `04 02` | `gps_fix_age_s` | 4 | uint32 seconds: 0 for a live fix, more for the saved one (`tools/get_gps.py`) |
| `04 03` | `gps_move_distance_m` | 4 | uint32 metres from the mount position, once it's known |
| `05 01` | `temperature_c` | 2 | int16 (value × 100) |
| `06 01` | `humidity_pct` | 1 | uint8 (0–100) |
| `07 17` | `camera_flood_detect` | 4 | uint32 bool: water % ≥ baseline + `area_threshold`; only when `camera_flood_detection_enabled` and a baseline is set (`tools/flood_baseline.py`) |
| `07 27` | `camera_new_local_max` | 4 | uint32 bool: water % rose by `area_threshold` or more since the previous capture; same condition |
| `07 37` | `camera_water_pct` | 1 | uint8 (0–100): water share of the capture's mask, whenever it has one (same 1 byte over LoRa) |
| `08 18` | `camera_flood_bitmap` | variable | the mask at full resolution, lossless (methods 0/1, at most 32000 B); the LoRa-sized bitmap if it is larger or there is no mask (docs/BITMAP_DECODING_SPEC.md) |
| `09 19` | `sr_area_threshold_pct` | 4 | uint32 |
| `09 29` | `sr_stage_threshold_cm` | 4 | uint32 (cm); reserved, unused on the node |
| `09 39` | `sr_monitoring_frequency` | 4 | uint32 |
| `09 49` | `sr_emergency_frequency` | 4 | uint32 |
| `09 59` | `sr_neighborhood_emerg_freq` | 4 | uint32 |

**Encoding examples (Python `struct`):**

```python
import struct, time

# device_ts — 8 bytes
struct.pack(">Q", int(time.time())).hex()

# battery_pct — 4 bytes
struct.pack(">I", 75).hex()          # → "0000004b"

# temperature_c — 2 bytes (22.50°C → 2250 → 0x08CA)
struct.pack(">h", int(22.50 * 100)).hex()

# humidity_pct — 1 byte
struct.pack(">B", 61).hex()          # → "3d"

# gps_block — 8 bytes (40.7128°N, -74.0060°W — placeholder)
struct.pack(">ii", int(40.7128 * 1e6), int(-74.0060 * 1e6)).hex()

# flood bitmap — variable
bitmap_bytes = b'\xff\x00\xff\x00'   # 8 pixels, alternating
bitmap_bytes.hex()
```

### LoRa sensor packet: compact channels

The LoRa sensor packet (`_encode_compressed_packet` in
`tools/lora_handler_concurrent.py`) is a different encoding from the IP channels
above: `[channel][type][value]` with values sized to fit (u8 flags, a u32
`00 01` capture time). Its orientation and temperature now go in 2-byte
fixed-point form under new codes, and the Witty Pi temperature and output
voltage are no longer sent over LoRa (the server never stored them; the IP
uplink is unchanged). The old codes keep their sizes and are still decoded, for
units on older software:

| Old code (float32) | New code | Field | Encoding | Decoded value |
|---|---|---|---|---|
| `03 01` (12 B) | `03 02` (6 B) | `tilt_roll_yaw` (heading, roll, pitch) | u16 heading, i16 roll, i16 pitch, in 0.01° | value / 100; heading kept in 0–360 (mod 360), roll and pitch clamped to ±327.67 |
| `05 01` (4 B) | `05 02` (2 B) | `temperature_celsius` (AHT20) | i16 in 0.01 °C | value / 100 |
| `0A 01` (4 B) | not sent | `wittypi_temperature` | | |
| `0A 03` (4 B) | not sent | `wittypi_internal_voltage` (Witty Pi Vout) | | |

The ChirpStack codec (`config/chirpstack_device_profile_codec.js`) gives both
forms the same field names and units, so the server's mapping is unchanged. A
reading that is missing or not a number is left out. The compact channels come
last in the packet: a decoder that predates them stops there and loses only
these fields. Update the codec and the API before the nodes. The full sensor
packet in `tests/test_lora_small_payloads.py` (`FULL_SENSOR_DATA`) goes from
104 to 84 bytes.

---

## Server API Endpoints

All endpoints are on the FastAPI server (`../API`).  
Base path: `{server_url}/` (no version prefix currently).

### `POST /ip/uplink`

Receive sensor data from an IP device.

**Request body:**
```json
{
    "device_id": "watercam-001",
    "device_ts": 1712000000,
    "channels": [
        {"code": "02 01", "payload_hex": "00000050"},
        {"code": "05 01", "payload_hex": "0898"}
    ]
}
```

- `device_ts` — optional; if omitted, server time is used.
- `channels` — at least one required (or provide `payload_hex` for raw TTLoRa hex).
- Side effect: device `transport_type` is set to `"ip"` on first call.

**Response (201):**
```json
{
    "device_id": "watercam-001",
    "channel": "multi",
    "ts": "2026-04-14T12:00:00+00:00",
    "decoded": {
        "battery_pct": { "value": 80, "unit": "%" },
        "temperature_c": { "value": 22.0, "unit": "°C" }
    }
}
```

### `GET /ip/downlink/{device_id}`

The commands pending for the node (at most 8, oldest first), until they are acked.

**Response (200):**
```json
{"commands": [{"id": 412, "frame": "9c219118"}]}
```

Nothing pending: `{"commands": []}`.

### `POST /ip/ack`

The node's results for the commands it applied.

```json
{"device_id": "watercam-001", "acks": [{"id": 412, "result": 0}]}
```

Unknown ids, and ids of other devices, are ignored. The same `acks` list may ride
in an `/ip/uplink` body (acks that couldn't be posted earlier). `/ip/uplink` also
returns the pending `commands` in its reply when the body sets `want_command`.

### `POST /ip/device/{device_id}/transport`

Manually set the transport type for a device.

```json
{"transport_type": "ip"}
```

Usually not needed — `/ip/uplink` sets `transport_type = "ip"` automatically.

---

## Running the Tests

```bash
# From the project root:
python tests/test_ip_upload.py

# Or with pytest:
pytest tests/test_ip_upload.py -v

# Point at a different server:
WATERCAM_SERVER_URL=http://192.168.1.50:8000 python tests/test_ip_upload.py

# Use a different device ID (avoids polluting production data):
WATERCAM_DEVICE_ID=test-device-ci python tests/test_ip_upload.py
```

The tests **skip** (rather than fail) if the server is unreachable, so they are
safe to include in CI pipelines where the API may not be running.

Tests that create real readings in InfluxDB/SQLite — the `device_id` used during
testing will appear in the dashboard.  Use `WATERCAM_DEVICE_ID=...` to isolate
test traffic from production devices.

---

## Smoke-Testing the Transmitter Directly

```bash
python tools/transmit_ip.py
```

This encodes a minimal uplink (timestamp + battery + temperature) using values
from the script's `__main__` block and prints the server's response.  The server
URL and device ID come from `runtime_config.json`.

---

## TickTalkPython Integration

**Status: Complete** as of 2026-04-14.

Two `@SQify` functions have been added to `ticktalk_main.py`:

### `ip_uplink_transmit(bitmap, sensor_tracker)` (line ~1496)

Collects a subset of sensor data (AHT20 temperature/humidity, GPS, WittyPi
battery, flood bitmap, flood detect flag, and runtime threshold parameters),
encodes it as channel-coded hex, and POSTs to `/ip/uplink`.  IMU orientation
is intentionally omitted (no `03 01` channel).  Called from `ttmain` immediately
after `lora_return`:

```python
lora_return = lora_token_with_tracker(bitmap, sensor_tracker)
ip_return   = ip_uplink_transmit(lora_return, sensor_tracker)  # lora_return is the bitmap
shutdown_result = call_shutdown(ip_return)
```

The IP uplink runs after LoRa, and the shutdown waits for it, so an upload
(or a cellular bring-up) is never cut off.  A failure in IP never affects LoRa
and vice versa.

### Transport order: LoRa, WiFi, cellular

On by default. Each capture's IP uplink:

1. **LoRa.** While the mDot is joined (`AT+NJS?` through the LoRa daemon), the
   IP uplink is skipped, and a cellular session left up is taken down.
2. **WiFi.** Otherwise the server is tried over WiFi. NetworkManager prefers
   WiFi's route (metric 600) to the modem's (700). If a cellular session is up
   and WiFi has reconnected, the session is taken down first.
3. **Cellular.** If the server is still unreachable, `nmcli connection up`
   brings the modem's data connection up and the reading and queued backlog
   are sent. The connection then stays up for the session, until LoRa or WiFi
   returns or the Pi powers off. If the server can't be reached over it either,
   it is taken down straight away.

Whichever way it went, once a reading is delivered over IP the node collects
and applies the commands waiting. The uplink asks for them (`want_command`) and the
server's reply carries them in `commands`, which saves a separate request on a
metered link; without `commands` in the reply, the node polls `/ip/downlink`
instead. Each is applied once and acked with `POST /ip/ack`; acks that can't be
posted ride on the next uplink as `acks`. The wake-start poll runs
before the uplink and never brings up cellular, so a unit without WiFi gets its
commands here, after the capture rather than before it. Polling twice is safe:
the server offers a command until it is acked, and the node applies a frame once.

Only a connection with autoconnect **off** is managed. A unit whose `Quectel`
connection still autoconnects keeps cellular up as before, so a field unit
reached over cellular doesn't lose its link until it is set up for this.

**Capture ID.** The capture time, the moment `get_time` fired for that capture
(`tools/capture_time.py`), identifies it on every transport: `device_ts` in the
IP uplink, and channel `00 01` in the LoRa sensor packets. The LoRa bitmap
packet carries no ID, so that every byte goes to the bitmap. It is sent straight
after the full sensor packet, and the API gives it that packet's capture time
when the LoRaWAN frame counters are consecutive. Captures are at least 30 s
apart, so the capture time is unique per unit.

- **On the node:** while LoRa isn't joined, each capture is also queued for LoRa
  store-and-forward. When IP delivers a reading (live or from its own queue),
  the LoRa entry with the same capture time is removed.
- **On the server:** the API keeps the first arrival of each part of a capture
  (sensors, bitmap) and drops later ones, whichever transport they came by
  (API `app/capture_dedup.py`). This also covers what the node can't know: LoRa
  uplinks are unconfirmed, and an HTTP retry can post twice.

Set up on the unit (checks and remote-access caveats are in
[FIELD_DEPLOYMENT_GUIDE.md](FIELD_DEPLOYMENT_GUIDE.md#151-keep-the-cellular-modem-off-unless-lora-and-wifi-fail)):

```bash
sudo nmcli connection modify Quectel connection.autoconnect no   # no data at boot
sudo install -m 644 config/polkit/50-watercam-networkmanager.rules /etc/polkit-1/rules.d/
```

The polkit rule lets `pi` (ticktalk has no login session) control NetworkManager
connections. The modem stays registered on the network without a data
connection: that costs no measurable power (see
[POWER_ANALYSIS.md](POWER_ANALYSIS.md)) and keeps GPS working. Cellular is
kept as the last resort for **data**, not power: a wake over cellular used
126–474 KB on 006, of which our own traffic was about 10 KB; the rest was
Tailscale ([CELLULAR_DATA.md](CELLULAR_DATA.md)).

**Effect on the other units:** with the defaults, units with `ip_upload.enabled`
stop sending over IP while their LoRa is joined.

**Sensor data collected:**
- AHT20: `temperature_celsius` → channel `05 01`, `relative_humidity` → `06 01`
- GPS: `gps_lat` / `gps_lon` → channel `04 01`
- WittyPi: `battery_voltage` converted to % → channel `02 01`
- Bitmap: contents → channel `08 18`; water % of the mask → `07 37`; flood detected / growing (when enabled) → `07 17` / `07 27`
- Runtime params: area/stage thresholds, frequencies → channels `09 19`–`09 59`

**Reachability check:** `is_reachable()` is called before encoding; if the
server doesn't respond, the function returns `{"status": "unreachable"}` without
spending time on encoding.

### IP fallback when LoRa can't carry bitmaps

At DR0 the mDot's payload limit is 11 bytes and no bitmap fits: bitmaps wait in
the LoRa store-and-forward queue for a data rate with room, and the server gets
sensor packets but no bitmap. `tools/ip_fallback.py` then makes a capture go over
IP, **even with `ip_upload.enabled` false and LoRa healthy**, with the usual
WiFi-then-cellular order and power lock:

- **The node's rule.** The last bitmap delivered, over LoRa (the mDot sent the
  `08 18` packet, live or from the queue) or over IP (the server accepted it), is
  older than `ip_fallback_after_hours` (runtime parameter, default 12, 0 = off),
  and the LoRa queue is holding bitmaps back. A bitmap the LoRa send policy skips
  isn't queued, so it doesn't count. At most one such upload every
  `ip_fallback_min_interval_hours` (default 6), counted from the attempt, so a
  link that fails isn't retried every wake. A unit that never delivered a bitmap
  counts from the first time the rule ran.
- **The server's request**, downlink `17 97 NN` (3 bytes, so it fits DR0's 11):
  an IP upload in each of the next NN wakes, `17 97 00` cancels. One upload per
  wake (boot); the count goes down once an upload is delivered, and a request
  older than 7 days is dropped. The same NN again within 15 minutes is a resend
  (the API resends until it hears back) and doesn't restart the count. While some
  are left the node's status reply carries `"ipr":N`.

Each capture logs one line, `📶 IP fallback: ...`, with the decision and why. At
wake start `tools/wifi_policy.py` leaves WiFi on when an upload is likely, so it
can join during the capture before cellular is tried; mid-wake (a request that
just arrived over LoRa) the radio is turned on then. A cellular connection
brought up for a fallback upload is taken down afterwards unless
`ip_upload.enabled` is true. The uplink carries `"ip_fallback": true`, so the API
keeps the device on LoRa transport for its commands.

The upload needs `ip_upload.server_url`, `device_id` and `api_key` set; without a
`server_url` it is skipped with a log line. State is in `data/ip_fallback.json`
(written atomically under a lock): `lora_at`, `ip_at`, `first_seen`,
`fallback_at` and the server's `request`.

**Cellular data.** A wake that uploads over cellular costs about 90 KB, mostly
Tailscale ([CELLULAR_DATA.md](CELLULAR_DATA.md)). Every delivered upload restarts
the 12 hours, so the defaults mean at most about two uploads (~180 KB) a day
while LoRa can't carry bitmaps; a unit with WiFi in range costs no SIM data. Raise
`ip_fallback_after_hours` on a unit with little SIM data left, or set it to 0.

### `ip_downlink_poll_and_apply(lora_init)` (line ~1647)

Polls `/ip/downlink/{device_id}` at the start of each wake cycle and applies
any pending parameter-update commands (each acked, see above).  Called after `initialize_lora_integration`
so updated thresholds are in effect for the capture cycle that follows. It
doesn't bring up cellular; if the server is unreachable, the poll after the IP
uplink (above) collects the command instead:

```python
lora_init   = initialize_lora_integration(trigger)
ip_downlink = ip_downlink_poll_and_apply(lora_init)  # NEW
```

**Downlink command codes handled** (a frame is `[seq][code][value]...`: [SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md#command-frames)):

| Code | Name | Decoding | `set_parameter` target |
|---|---|---|---|
| `10 90` | `area_threshold_pct` | u8 direct | `area_threshold` |
| `11 91` | `stage_threshold_cm` | u16 direct | `stage_threshold` |
| `12 92` | monitoring interval | u16 minutes, 1-10080 | `monitoring_frequency` (min) |
| `13 93` | emergency capture interval | u8 minutes, 1-240 | `emergency_frequency` |
| `14 94` | flood-code interval | u8 minutes, 1-240 | `neighborhood_emergency_frequency` |
| `16 96` | `audio_recording_enabled` | u8 bool | `audio_recording_enabled` |
| `17 97` | IP upload in each of the next NN wakes, even with `ip_upload.enabled` false | u8 wakes, 0 = cancel ([below](#ip-fallback-when-lora-cant-carry-bitmaps)) | `data/ip_fallback.json` |
| `18 98` | remote debug session | u8 × 10 min, 0 = end ([REMOTE_DEBUG_SESSION.md](REMOTE_DEBUG_SESSION.md)) | request for `remote-debug.timer` |
| `18 99` | remote access over WiFi only | u8 × 10 min, 0 = end | request for `remote-debug.timer` |
| `18 9A` | remote access over WiFi, else cellular | u8 × 10 min, 0 = end | request for `remote-debug.timer` |
| `21 91` | emergency mode on | u8 hours, 0 = `emergency_max_hours` ([REMOTE_START_AND_EMERGENCY.md](REMOTE_START_AND_EMERGENCY.md)) | `emergency_mode`, `emergency_max_hours`, `emergency_since` |
| `99 99` | emergency mode off | no value | `emergency_mode` |
| `50 01` | status request | no value; the ack is the status reply | none |

Unrecognised codes are logged and ignored — forward-compatible with new server
commands.

### Enabling IP in the main application

Set `ip_upload.enabled = true` in `runtime_config.json`.  When disabled (the
default), both `@SQify` functions return immediately without making any network
calls — zero overhead for LoRa-only deployments.

---

## Installing the IP token

Generate the token on the dashboard (Management → select the unit → IP access
token → Generate; it is shown once), then from your own terminal run:

```bash
tailscale ssh -t pi@<unit-hostname> \
  '~/SU-WaterCam/venv/bin/python ~/SU-WaterCam/tools/set_ip_token.py'
```

and paste the token at the hidden prompt. The script checks that it looks like a
WaterCam token (`wct_…`), stores it as `ip_upload.api_key`, makes
`runtime_config.json` readable by its owner only (0600, which later config writes
keep), and prints only the token's last four characters. No restart is needed.
Keep the token out of shell history, chat and command lines: always paste it at
the prompt. To check it, send one upload and confirm the unit no longer appears
under unauthenticated requests on the dashboard (or `GET /ip/auth-status`).

## Known Gaps / Next Steps

1. **Full image upload** — not applicable.  Raw camera files (JPG/PGM/CSV) are
   never transmitted; the compressed flood bitmap via channel `08 18` is the
   intended image payload.
2. **Auth — done (API #28).** The server issues each device its own token and checks
   `Authorization: Bearer <token>` on uplinks and downlink polls. Install the token on
   the unit with `tools/set_ip_token.py`; no code change is needed on the node.
3. **GPS decoder bug — fixed (API commit 0a63091)** — `decode_gps_8b` in
   `API/app/decoders.py` had three bugs: 3-byte slices instead of 4-byte,
   `signed=False` which corrupted W-hemisphere longitudes, and raw integer
   field names instead of float degrees.  Fixed and covered by `TestGPSDecoder`
   in `API/tests/test_ip_transport.py`.
