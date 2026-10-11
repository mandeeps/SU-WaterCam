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

The server auto-sets `transport_type = "ip"` for any device that hits the
`/ip/uplink` endpoint, which makes the `/downlink` router queue commands
for polling instead of pushing them through ChirpStack.

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
  "cellular_connect_timeout_s": 90
}
```

| Key | Type | Description |
|---|---|---|
| `enabled` | bool | Master switch. Set `true` to activate IP transport. |
| `server_url` | str | Base URL of the FastAPI server, no trailing slash. |
| `api_key` | str | This unit's own IP API token, sent as `Authorization: Bearer <token>` on uplinks and downlink polls. Issued once per device on the dashboard (Management → IP access token). Required when the server runs `IP_AUTH=enforce`; until then a missing token is accepted and logged (API `docs/IP_TRANSPORT.md`). |
| `device_id` | str | Logical device identifier used for all uplinks and downlink polling. Must be unique per deployment. |
| `timeout_s` | int | Per-request timeout in seconds. |
| `retry_attempts` | int | Max POST attempts before giving up (backoff between attempts). |
| `retry_backoff_s` | float | Base sleep between retries. Before attempt N, waits `retry_backoff_s * 2^(N-1)` seconds (exponential backoff). |
| `fallback_to_lora` | bool | Intent flag for the TickTalkPython integration layer: if IP fails, fall back to LoRa. Not enforced by `transmit_ip.py` itself. |
| `only_if_lora_unavailable` | bool | Default `true`: skip the IP uplink while the mDot is joined (LoRa first). `false` sends over both. |
| `cellular_connection` | str | Default `"Quectel"`: the LTE modem's NetworkManager connection, brought up only when the server can't be reached over WiFi. Managed only if its autoconnect is off. `""` leaves the modem alone. See [Transport order](#transport-order-lora-wifi-cellular). |
| `cellular_connect_timeout_s` | int | How long `nmcli` may take to bring the connection up (default 90; re-attaching took 3–51 s on 006). |

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

GET `/ip/downlink/{device_id}` to retrieve the oldest queued command.

```python
result = tx.poll_downlink()
if result["success"] and result["command"]:
    cmd = result["command"]
    print(cmd["hex_payload"])   # encoded command to decode/apply
    print(cmd["queue_id"])      # DB id (already marked delivered)
    print(cmd["parts"])         # decoded parts list (may be None)
```

The server marks the command as delivered **atomically on retrieval** — there is
no separate acknowledge call. Once polled, the command is consumed.

**Returns:**
```python
{
    "success": True,
    "status_code": 200,
    "command": {                  # or None if nothing queued
        "queue_id": 42,
        "hex_payload": "...",
        "parts": [...],
        "created_at": "2026-04-14T12:00:00+00:00"
    },
    "error": None
}
```

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
| `04 01` | `gps_block` | 8 | int32 lat\_microdeg, int32 lon\_microdeg |
| `05 01` | `temperature_c` | 2 | int16 (value × 100) |
| `06 01` | `humidity_pct` | 1 | uint8 (0–100) |
| `07 17` | `camera_flood_detect` | 4 | uint32 bool |
| `07 27` | `camera_new_local_max` | 4 | uint32 bool |
| `08 18` | `camera_flood_bitmap` | variable | raw bitmap bytes |
| `09 19` | `sr_area_threshold_pct` | 4 | uint32 |
| `09 29` | `sr_stage_threshold_cm` | 4 | uint32 (cm) |
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

Poll for the oldest pending queued command.

**Response (200) — command pending:**
```json
{
    "command": {
        "queue_id": 42,
        "hex_payload": "09192A...",
        "parts": [...],
        "created_at": "2026-04-14T11:55:00+00:00"
    },
    "message": "Command retrieved successfully"
}
```

**Response (200) — nothing queued:**
```json
{"command": null, "message": "No pending commands"}
```

The command is marked delivered **atomically** when retrieved. No ACK needed.

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
   brings the modem's data connection up, the reading and queued backlog are
   sent, and any downlink command is collected (the wake-start poll had no
   route). The connection then stays up for the session, until LoRa or WiFi
   returns or the Pi powers off. If the server can't be reached over it either,
   it is taken down straight away.

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
- Bitmap: contents → channel `08 18`; flood detect flag → channel `07 17`
- Runtime params: area/stage thresholds, frequencies → channels `09 19`–`09 59`

**Reachability check:** `is_reachable()` is called before encoding; if the
server doesn't respond, the function returns `{"status": "unreachable"}` without
spending time on encoding.

### `ip_downlink_poll_and_apply(lora_init)` (line ~1647)

Polls `/ip/downlink/{device_id}` once per wake cycle and applies any queued
parameter-update commands.  Called after `initialize_lora_integration` so
updated thresholds are in effect for the capture cycle that follows:

```python
lora_init   = initialize_lora_integration(trigger)
ip_downlink = ip_downlink_poll_and_apply(lora_init)  # NEW
```

**Downlink command codes handled** (from server `app/encoders.py`):

| Code | Name | Decoding | `set_parameter` target |
|---|---|---|---|
| `10 90` | `area_threshold_pct` | u8 direct | `area_threshold` |
| `11 91` | `stage_threshold_cm` | u16 direct | `stage_threshold` |
| `12 92` | `monitoring_freq_h` | u8 index → `[1,3,6,24,72]` hrs → ×60 | `monitoring_frequency` (min) |
| `13 93` | `emergency_freq_min` | u8 index → `[2,5,10]` min | `emergency_frequency` |
| `14 94` | `flood_code_freq_min` | u8 index → `[10,20,30,40,50,60]` min | `neighborhood_emergency_frequency` |
| `16 96` | `audio_recording_enabled` | u8 bool | `audio_recording_enabled` |
| `18 98` | remote debug session | u8 × 10 min, 0 = end ([REMOTE_DEBUG_SESSION.md](REMOTE_DEBUG_SESSION.md)) | request for `remote-debug.timer` |
| `21 91` | emergency mode on | u8 hours, 0 = `emergency_max_hours` ([REMOTE_START_AND_EMERGENCY.md](REMOTE_START_AND_EMERGENCY.md)) | `emergency_mode`, `emergency_max_hours`, `emergency_since` |
| `99 99` | emergency mode off | no value | `emergency_mode` |
| `21` | emergency mode on (old code, kept during the transition) | no value | `emergency_mode` |

Unrecognised codes are logged and ignored — forward-compatible with new server
commands.

### Enabling IP in the main application

Set `ip_upload.enabled = true` in `runtime_config.json`.  When disabled (the
default), both `@SQify` functions return immediately without making any network
calls — zero overhead for LoRa-only deployments.

---

## Known Gaps / Next Steps

1. **Full image upload** — not applicable.  Raw camera files (JPG/PGM/CSV) are
   never transmitted; the compressed flood bitmap via channel `08 18` is the
   intended image payload.
2. **Auth — done (API #28).** The server issues each device its own token and checks
   `Authorization: Bearer <token>` on uplinks and downlink polls. Put the token in
   `ip_upload.api_key`; no code change is needed on the node.
3. **GPS decoder bug — fixed (API commit 0a63091)** — `decode_gps_8b` in
   `API/app/decoders.py` had three bugs: 3-byte slices instead of 4-byte,
   `signed=False` which corrupted W-hemisphere longitudes, and raw integer
   field names instead of float degrees.  Fixed and covered by `TestGPSDecoder`
   in `API/tests/test_ip_transport.py`.
