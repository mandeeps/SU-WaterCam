# Time on the units

Units don't run NTP (`systemd-timesyncd` is disabled, chrony and ntp aren't
installed). This is deliberate: the Witty Pi documentation says NTP interferes with
the Witty Pi's own timekeeping, and the Witty Pi software turns NTP off itself
(`timedatectl set-ntp 0` when it copies the RTC to the system clock).

The rule: the clock takes the **first trustworthy source** that answers, and a
later source replaces it only when the two disagree by more than the later
one's own error. When they disagree, IP and GPS time win over LoRa time. A unit
with an unreliable LoRa link (006) still gets LoRa time when an answer gets
through, which is as accurate as on a strong link, and otherwise falls through
to IP time; no setting is needed for that.

| When | Source | Resolution | Code |
|---|---|---|---|
| Every boot, first | The Witty Pi RTC, copied into the system clock by the Witty Pi daemon (or the other way round if the RTC's time is bad) | 1 s, drifts (005: 2 s in 4 days) | `~/wittypi/daemon.sh` (stock), `tools/rtc_early_clock.py` |
| As soon as it answers, once a day | **LoRa network time** (DeviceTimeReq), asked by `lora_daemon` once the mDot has joined and applied by `syncTime.sh` | about 10 ms after its bias (0.21 s) | `tools/lora_time.py`, `config/wittypi/lora_answer.py`, `syncTime.sh` |
| Every boot, +30 s | **Network time**: the API server's time (tailnet or https; its `X-Server-Time` header, in ms), or public https servers' `Date` header when it is down. Replaces LoRa time only when they disagree by more than 2 s | about 0.1 s (API), 0.5 s (`Date`) | `config/wittypi/syncTime.sh`, `network_time.py` |
| Then, with neither | **GPS time**, if the modem already has power; otherwise a late LoRa answer, waited for up to 60 s | better than 0.1 s | `syncTime.sh`, `gps_time.py` |
| After an IP upload, once per boot | The **server's** time in the reply (`X-Server-Time`, else `Date`), unless network or GPS time set the clock this boot; it replaces LoRa time or the RTC when more than 2 s off, set to the millisecond | 0.1-0.3 s over cellular (1 s with `Date` only) | `tools/transmit_ip.py` `correct_clock` |
| The daily GPS window | **GPS time** from the fix, unless network, GPS or server time set the clock this boot; it replaces LoRa time or the RTC when 2 s or more off | better than 0.1 s | `tools/gps_window.py` |

LoRa time usually answers within `syncTime.sh`'s 30 s wait (the mDot stays
joined between wakes), so on most boots the clock is right before the first
capture, on units with or without IP.

### By unit configuration

| Unit | Clock comes from |
|---|---|
| Strong LoRa and WiFi | LoRa time; network time checks it |
| Unreliable LoRa and IP (006) | LoRa time when an answer gets through, else network time (WiFi) or the server's time after the first upload (cellular); IP wins any disagreement |
| LoRa only | LoRa time; a late answer (slow join) is waited for, and an answer that came too late is asked for again after 6 h |
| Cellular only, no LoRa | the RTC until the first upload, then the server's time; the daily GPS fix. The modem is off at boot, so there is no network or GPS time then, and captures before the upload are flagged `clock_unverified` (off only by the RTC's drift since the last wake) |
| WiFi only | network time at boot |
| Bench unit (ticktalk off) | as above: `syncTime.sh` and `lora_daemon` don't depend on ticktalk |

Network time never comes from plain http outside the tailnet, and over the
tailnet only while Tailscale is running and the server is one of its peers (the
rule `transmit_ip` uses for the server's time; before Tailscale is up, the public
https servers answer): a captive portal or an access point's own clock could
answer otherwise, and as "network time" the
answer would keep the other sources out for the boot and go into the RTC. Each
request times out after 8 s and the whole lookup after 30 s, so WiFi without
internet no longer holds the boot sync for minutes (it runs in the foreground
when the RTC was bad, with the next wake unarmed until it returns).

Every source refuses a time before 2026, and a step of more than a day while
the RTC looks valid (`/run/watercam-rtc-untrusted` absent and the clock after
2026-01-01). The RTC counts as not valid when `rtc_early_clock.py` couldn't
read it or found it implausible, or when the Witty Pi daemon logged "RTC has
bad time" (`syncTime.sh` marks that, at boot, before the others run).

`syncTime.sh` runs as root and sets the system clock and the Witty Pi RTC itself;
the server's time and the GPS window go through the root-owned
`/usr/local/sbin/watercam-set-clock` (doas). Each records the source in
`/run/watercam-time-source` (`lora`, `network`, `gps` or `server`). A boot with
no source at all is marked "clock unverified" (`tools/capture_time.py`, uplink
`01 0D`).

## LoRa network time

The mDot's `AT+GPSTIME` sends an empty uplink carrying a LoRaWAN DeviceTimeReq.
The network server (our ChirpStack answers it) replies with a MAC-only
DeviceTimeAns in the first receive window, and the mDot prints the time in
milliseconds since the GPS epoch (Unix time = ms / 1000 + 315964800 - 18 leap
seconds).

**Measured on 005 against NTP (2026-11-10 17:43 EST).** Ten asks, each between
two NTP offset measurements on the unit (pool.ntp.org, the best of four by
delay, 16-130 ms), so the unit's own clock error cancels out. Data rate DR0
(SF10), RX1 delay 1 s:

| | Result |
|---|---|
| Answered | 9 of 10 (one "Server time not received") |
| Command to answer | 1.102-1.103 s |
| LoRa time minus true time, at the answer | **+0.214 s** mean, 0.009 s standard deviation (+0.200 to +0.226) |

Earlier the same day (two sessions of three, against the workstation's clock
over ssh) gave +0.20 to +0.22 s on five asks and +0.82 s on one that took 2.5 s.

So LoRa time is a steady 0.21 s ahead: a fixed bias, not noise. The likely
cause is the network server rather than its clock. ChirpStack holds each uplink
for its deduplication delay (200 ms by default) to collect it from every
gateway, and, when the gateway gives no GPS timestamp, answers DeviceTimeReq
with its own time after that. Add the backhaul and it comes to about 0.21 s.
That also means libmdot's `getGPSTime()` returns the time at the answer (the
time since the uplink added on), and that ChirpStack's clock is right to about
10 ms. The other reading, an uncorrected uplink time and a ChirpStack clock
1.3 s fast, would have to match the default delay by chance. Checking
`deduplication_delay` in ChirpStack's configuration would settle it.

`lora_time` subtracts the bias from every answer: **`lora_time_bias_s`** in
`runtime_config.json`, default 0.21 s, settable from -2 to 2 s. Set it again if
ChirpStack's deduplication delay changes, or to about 0 if a unit's gateway
sends GPS timestamps; the GPS fixes in the time sync log show what is left. With
the bias taken out, LoRa time is good to about 10 ms on 005.

The API server runs NTP (systemd-timesyncd, offset under 1 ms when checked);
ChirpStack runs on a different server, whose clock was only checked this way (above).

The answer is authenticated by the device's LoRaWAN session keys and uses no
data plan. Each ask costs one uplink and one downlink, so a unit asks at most
once a day (`lora_time.INTERVAL_S`, kept in `data/lora_time.json` across boots).

`lora_daemon` asks once the mDot has joined, in the background, and writes the
answer to `/tmp/watercam-lora-answer.json`: the network's time when it arrived
(bias subtracted) and the time since boot (`CLOCK_BOOTTIME`) at that moment.
`syncTime.sh` (root) applies it through `lora_answer.py`, which carries it to
the present with the time since boot, so nothing is lost to the delay or to
anything that stepped the clock in between. `lora_daemon` runs sandboxed with
no privileges and never sets the clock itself. An answer from another boot, more
than 30 minutes old, or more than a day off while the RTC looks valid is refused.

While it waits for the join the daemon marks the file "asking", so `syncTime.sh`
waits up to 60 s more for it when nothing else set the clock; "none" means no
answer is coming this boot (off, asked within the day, or the join took too
long). Once `syncTime.sh` has finished (`/run/watercam-time-sync-done`), nothing
would apply an answer, so the daemon doesn't ask. An answer that came too late
to be used is asked for again after 6 h; an unanswered ask waits the full day.
The mDot refuses the command at once (a plain `ERROR`, nothing sent) while it is
busy, as in the first seconds after the Pi boots; such a refusal is retried up
to three times, 10 s apart, at no airtime cost.

### `lora_time_sync` (runtime_config.json)

`on` (default) or `off`. The values of the earlier design (`healthy`, `measure`,
`always`) mean `on`. Set `off` on a unit whose airtime is short: it saves one
uplink and downlink a day.

## When a source goes away

| LoRa gateway (or ChirpStack) | WiFi | API server | Clock comes from |
|---|---|---|---|
| up | any | any | LoRa time (network time checks it) |
| down | up | up | network time from the API server |
| down | up | down | network time from public https servers |
| down | down | up | the server's reply to a cellular upload, else the daily GPS fix |
| down | down | down | the daily GPS fix, else the RTC alone |

- **Gateway down.** The ask goes unanswered: one uplink spent, nothing applied,
  and the next ask is a day later. Network, server and GPS time run as before.
  A network server that is down looks the same.
- **WiFi down,** or up with no internet: no network time (each request times
  out). LoRa time, the server's time over cellular or GPS takes over.
- **API server down:** network time from public https servers; no server time
  after uploads (they queue). LoRa time doesn't depend on it: ChirpStack runs
  on a different server.
- **Both servers unreachable** (they share a network, so an outage there takes
  out both): no LoRa time and no server time. Units
  with internet still get network time from public https servers; the others
  rely on the daily GPS fix.
- **Everything down:** the RTC drifts (about 0.5 s a day) and is an hour off
  after a daylight-saving change. Captures are flagged `clock_unverified`
  (uplink `01 0D`) until a source confirms the clock.

LoRa time and the server's time both come from our servers' clocks, so those
need NTP. The GPS lines in the time sync log would show if one drifted.

## The server's time: `X-Server-Time`, not `Date`

`Date` is whole seconds, floored, and uvicorn caches it and refreshes it once a
second: measured on 005 (2026-11-20), the API's ran 0.3-1.2 s behind its
NTP-synced clock, and a clock set from it by `correct_clock` (whole seconds,
without the +0.5 s) ended up 0.3-2.0 s behind. So the API sends `X-Server-Time`
(Unix time in ms, stamped as the reply goes out) on the two replies units read
the time from, `GET /health` and `POST /ip/uplink` (API `app/server_time.py`).
`network_time.py` and `correct_clock` use it, plus the reply's way back, and fall
back to `Date` + 0.5 s when it is missing (an older server, or public servers).

The way back is taken as half the round trip, but at most 0.25 s
(`RETURN_LEG_MAX_S`). The server stamps its time as the reply leaves, so most of
a slow round trip comes before the stamp: setting up a fresh cellular link,
sending an upload's body, the server's own work. In cold boots of the bench unit
over cellular (2026-11-22, LoRa time as the reference), the way back was
0.15-0.21 s every time. With half the round trip, a first request with a fast
reply (0.3-0.5 s round trip) left the clock within 0.03 s, but slow ones
(1.25 s and 5.7 s) left it 0.5 s and 2.7 s ahead. A 2.7 s error is also more
than `CLOCK_TOLERANCE_S`, so it could replace a good clock from LoRa time. With
the cap, all four would have been within about 0.1 s. A unit whose way back is
longer ends up behind by the excess: for one, when Tailscale can't connect
directly and relays its traffic through a DERP server.
`watercam-set-clock` takes fractional seconds; `correct_clock` falls
back to whole seconds if the installed copy is older.

## The time sync log

`data/time_sync_log.jsonl` (newest 1000 lines) records each LoRa attempt
(`"source": "lora"`: whether it was answered, `offset_s` = network time minus
the clock when it arrived, the round trip, whether `syncTime.sh` applied it, and
`clock_from` = what the clock came from in the end) and each GPS fix
(`"source": "gps"`: `offset_s` = GPS time minus the clock, `clock_from` = what
set the clock this boot, whether GPS set it). GPS is logged even when it doesn't
set the clock, so on a unit where LoRa set the clock, the GPS fix's `offset_s`
is LoRa time's error. On a unit where network time set the clock first, the
LoRa line's `offset_s` against it is logged the same way.

## Evaluation: LoRa time on weak links

Every unit with `lora_time_sync` on asks once a day and logs the answer, used or
not, so the data for weak links collects without a special setting. After 2-4
weeks, copy `data/time_sync_log.jsonl` (with the usual log copy) and compare,
per unit:

- **availability**: answered asks / asks, against the link margin at the time
  (`data/lora_health.json`);
- **agreement**: LoRa `offset_s` against the GPS `offset_s` of the same boot
  (both are against the same clock, so their difference is LoRa time's error);
- **cost**: uplinks and downlinks per answered ask.

A unit that rarely gets an answer only costs one uplink a day; set `off` there
if its airtime is short.
