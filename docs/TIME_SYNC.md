# Time on the units

Units don't run NTP (`systemd-timesyncd` is disabled, chrony and ntp aren't
installed). The clock comes from these sources, each later one only when it can
do better:

| When | Source | Resolution | Code |
|---|---|---|---|
| Every boot, first | The Witty Pi RTC, copied into the system clock by the Witty Pi daemon (or the other way round if the RTC's time is bad) | 1 s, drifts (005: 2 s in 4 days) | `~/wittypi/daemon.sh` (stock) |
| Every boot, +30 s | **Network time** (HTTP `Date` header) when there is internet then (WiFi; cellular isn't up at boot), else **GPS time** if the modem already has power | 1 s / better than 0.1 s | `config/wittypi/syncTime.sh` |
| Once a day, after the join | **LoRa network time** (DeviceTimeReq) when the LoRa link is healthy | about 0.3 s | `tools/lora_time.py`, from `lora_daemon` |
| After an IP upload, once per boot | The **server's** `Date` header in the reply, when nothing above set the time this boot | 1 s | `tools/transmit_ip.py` `correct_clock` |
| The daily GPS window | **GPS time** from the fix, when nothing above set the time this boot | better than 0.1 s | `tools/gps_window.py` |

Each sets the system clock and the Witty Pi RTC through the root-owned
`/usr/local/sbin/watercam-set-clock` (doas), which records the source in
`/run/watercam-time-source` (`network`, `gps`, `lora` or `server`). A boot with
no source at all is marked "clock unverified" (`tools/capture_time.py`, uplink
`01 0D`).

## LoRa network time

The mDot's `AT+GPSTIME` sends an empty uplink carrying a LoRaWAN DeviceTimeReq.
The network server (our ChirpStack answers it) replies with a MAC-only
DeviceTimeAns in the first receive window, and the mDot prints the time in
milliseconds since the GPS epoch (Unix time = ms / 1000 + 315964800 - 18 leap
seconds). Measured on 005 (2026-11-10):

- 1.20 s from the command to the answer, every time;
- three answers within about 15 ms of each other;
- 0.2-0.3 s behind true time (an NTP-synchronised workstation). LoRaWAN times
  the answer to the end of the uplink, about a second before it arrives.

The answer is authenticated by the device's LoRaWAN session keys and uses no
data plan. Each ask costs one uplink and one downlink, so a unit asks at most
once a day (`lora_time.INTERVAL_S`, kept in `data/lora_time.json` across boots).

`lora_daemon` asks once the mDot has joined, in the background. On a healthy
link (`tools/lora_health.py`: at least 5 dB of margin, recent link checks
passing) LoRa time is used **ahead of IP time**: it is finer than the HTTP
`Date` header and doesn't depend on the upload. An offset of 1 s or less is left
alone but counts as verified (the server's correction then steps aside this
boot); a larger one is corrected. It doesn't override GPS time set this boot,
and an answer more than a day off is refused unless the RTC is known to be bad,
as for the server's time.

### `lora_time_sync` (runtime_config.json)

| Value | Healthy link | Weak link |
|---|---|---|
| `healthy` (default) | use LoRa time | don't ask: IP, GPS and the RTC as before |
| `measure` | use LoRa time | ask, log the answer, don't use it |
| `always` | use LoRa time | use LoRa time |
| `off` | don't ask | don't ask |

## The time sync log

`data/time_sync_log.jsonl` (newest 1000 lines) records each LoRa attempt
(`"source": "lora"`: mode, the link's health and margin, whether it asked,
answered and was applied, `offset_s` = network time minus the clock, the round
trip) and each GPS fix (`"source": "gps"`: `offset_s` = GPS time minus the
clock, `clock_from` = what set the clock this boot, whether GPS set it). GPS is
logged even when it doesn't set the clock, so on a unit LoRa set the clock,
the GPS fix's `offset_s` is LoRa time's error.

## Evaluation: LoRa or GPS time on weak links

On a healthy link LoRa time is the better choice: about 0.3 s, a second of
radio, no modem. On a weak link the question is open: an ask can go unanswered
(the uplink not heard, or the answer lost), and every retry is another uplink at
a slow data rate; the GPS window instead powers the modem (USB on, 0.6-0.9 W)
for up to its window but works anywhere with sky.

**Procedure**

1. Pick the units whose LoRa link is weak or marginal: `data/lora_health.json`
   shows failed or low-margin link checks, or the dashboard's link history does.
2. Set `lora_time_sync` to `measure` on them. The GPS window stays as it is
   (daily, `gps_fix_interval_h`), so both run on the same unit. With the daily
   limit, the trial costs one extra uplink and downlink a day per unit.
3. After 2-4 weeks, copy `data/time_sync_log.jsonl` (with the usual log copy)
   and compare, per unit:
   - **availability**: answered asks / asks, against the link margin at the time;
   - **agreement**: LoRa `offset_s` against the GPS `offset_s` of the same boot
     (both are against the same clock, so their difference is LoRa time's error);
   - **cost**: uplinks and downlinks per answered ask, against the GPS window's
     modem-on time.
4. Decide per unit or per link quality:
   - LoRa answers on most boots it asks (say 90 % or more) and agrees with GPS
     within 1 s: set `always`, and the GPS window's daily fix could become less
     frequent (it is still needed for the position);
   - otherwise: keep `healthy`, so weak links rely on GPS time.

The margin a weak link still answers at would also be a better threshold than
the 5 dB the link health uses for data.
