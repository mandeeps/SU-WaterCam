# System reference

## Command frames

The server controls a node with command frames. A frame is the same over LoRa
(fPort 10 downlink) and IP (hex in `/ip/downlink` and `/ip/uplink` replies), and
one parser reads both (`tools/transmit_ip.py`: `parse_command_frame`,
`apply_command_frame`):

    [seq u8][code HH LL][value]...[code HH LL][value]

Blocks have no length byte; each code has a fixed value width. A frame is valid
only if everything after `seq` parses as known blocks, otherwise nothing in it is
applied.

**seq.** 1 to 255 identifies the command; the server derives it from the command's
id. `00` means unsequenced (a frame typed by hand): it is applied every time, never
deduplicated, and acked as `ak: 0`.

| Code | Width (bytes) | Meaning | Accepted values |
|---|---|---|---|
| `10 90` | 1 | area threshold, % | 0-255 stored as sent |
| `11 91` | 2 | stage threshold, cm (u16) | 0-65535 |
| `15 95` | 7 | wake schedule: start HH MM, end HH MM, interval u16 min, awake u8 min (site local time; `tools/schedule_control.py`) | interval 15-1440, awake 5-60 and < interval |
| `13 93` | 1 | emergency capture interval, minutes | 1-240 |
| `14 94` | 1 | flood-code interval, minutes | 1-240 |
| `16 96` | 1 | audio recording | 0 or 1 |
| `17 97` | 1 | IP upload in each of the next NN wakes | 0 cancels |
| `18 98` / `18 99` / `18 9A` | 1 | remote debug session, NN x 10 min (cellular / WiFi only / WiFi else cellular) | 0 ends |
| `21 91` | 1 | emergency mode on for HH hours | 0 = `emergency_max_hours`; capped at 168 |
| `50 01` | 0 | status request: the ack is the node's status reply | |
| `99 99` | 0 | emergency mode off | |

The table is `DOWNLINK_CODE_LENGTHS` in `tools/transmit_ip.py`. A value outside its
range is skipped and the rest of the frame is still applied. Examples (seq `00`,
as typed by hand): `005001` status request, `00219118` emergency for 24 h,
`009999` emergency off, `0012920168` monitoring every 360 min.

The mDot's own frames carry no seq and never reach the Pi: `!` (exactly one byte)
powers a sleeping Pi on, and `1B 9B HH` sets its watchdog. See
[REMOTE_START_AND_EMERGENCY.md](REMOTE_START_AND_EMERGENCY.md).

### Result codes

Each frame gets one result: `0` applied, `1` partly applied (some blocks out of
range), `2` rejected (invalid frame, or nothing applied).

### Dedup

The server keeps resending a command until the node acks it, over LoRa and by
offering it again in every IP reply. So the node applies a frame once:
`data/applied_commands.json` holds the last 64 applied frames as `(seq, hash of
the frame, time, result)` for 7 days (`tools/command_log.py`). A frame with the
same seq and hash is not applied again; it is acked again with the stored result.
A repeated `21 91` therefore does not restart the emergency timer. A frame with a
different hash under a reused seq counts as a new command.

### Acks

**LoRa.** One status reply per frame, JSON, sent raw. It leads with the ack, so
the ack survives trimming to the mDot's payload limit:

    {"ak":123}                      applied (10 bytes; fits even at DR0, 11 bytes)
    {"ak":123,"ar":2}               rejected (`ar` is left out when 0)
    {"ak":123,"dbg":1,"em":1,...}   the rest as room allows

Key order: `ak`, `ar`, `dbg`, `em`, `ipr`, `rd`, then the other status fields
(`tools/lora_debug_integration.py`). A status reply that answers no command (such
as the remote debug start) has no `ak`.

**IP.** The reply to `/ip/uplink` (when the uplink sets `want_command`) and
`GET /ip/downlink/{device_id}` return the pending commands, oldest first:

    {"commands": [{"id": 412, "frame": "9c219118"}, ...]}

The node applies each in order, then posts the results at once:

    POST /ip/ack  {"device_id": "...", "acks": [{"id": 412, "result": 0}, ...]}

### Pending acks

If `/ip/ack` fails (no route, non-2xx), the acks are kept in
`data/pending_acks.json` (last 64) and added to the next `/ip/uplink` body as
`"acks": [...]`. They stay there, and go again with each later uplink, until the
server answers 2xx. Acks are never sent to a server that is not trusted (see
[NODE_SECURITY.md](NODE_SECURITY.md)); commands from such a server are not
applied either.

## Wake schedule (15 95)

The dashboard sets the wake schedule with one command (7-byte value): a daily window, an interval and an awake time, in the site's local time. Wakes are start, start + interval, ... while a wake begins before end; start == end is all day (a last wake with no off time before the next day's first is dropped), end < start crosses midnight.

The node (`tools/schedule_control.py`, as `pi`, no root needed) writes `~/wittypi/schedule.wpi` as one day of ON/OFF states from BEGIN (the old file kept as `schedule.wpi.prev`), and arms the next wake with the shutdown path's code (`tools/recovery_boot.py`). If writing or arming fails, the old file is put back and the command is acked rejected. The Witty Pi daemon reads the new file at the next boot.

The Witty Pi counts seconds from BEGIN, so the node rewrites the file when the local UTC offset changes (DST): `ticktalk.service` runs `tools/schedule_control.py --refresh` before it starts. The active schedule is reported as `09 79` (the same 7 bytes) in sensor packets and IP uplinks, and as `sc` ("HHMM-HHMM/interval/awake") in the status reply.
