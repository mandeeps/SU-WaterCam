# Remote start and emergency mode

Two separate commands, each with one meaning whatever the Pi's power state.

| Downlink (fPort 10) | Meaning | Who handles it |
|---|---|---|
| `!` (0x21, exactly one byte) | **Remote start**: power a sleeping Pi on | The mDot. If the Pi is off (GPIO5/PA_6 low) it pulses the Witty Pi switch (PB_1, 1.5 s); if the Pi is on it does nothing. It never forwards the byte. |
| `[seq] 21 91 HH` | **Emergency mode on** for `HH` hours (hex, `01`–`A8`, so up to 168 h); `21 91 00` uses `emergency_max_hours` (default 24) | The node, over LoRa or IP. It acks with its status (`{"ak":seq,"dbg":1,"em":1,...}`). |
| `[seq] 99 99` | **Emergency mode off** | The node, over LoRa or IP. It acks with its status. |

`21 91` and `99 99` are command frames: a leading `seq` byte, then the blocks
([SYSTEM_REFERENCE.md](SYSTEM_REFERENCE.md#command-frames)). The server picks the
seq; a frame typed by hand uses seq `00`, for example `00219118` (on for 24 h) and
`009999` (off). A resent frame (same seq and bytes) is acked again but not applied
again, so a resend does not restart the emergency timer; seq `00` is applied every
time.

## Why they were split

`!` used to mean both. A sleeping Pi woke for one normal cycle and was **not** in
emergency mode, because the mDot doesn't forward `!` and its log line about it is
printed before the Pi is up. A Pi that was already on read that log line and
**turned emergency mode on**. So "Enable Emergency Mode" on the dashboard did nothing
lasting for a sleeping node, and waking a node that happened to be on put it into
emergency mode, which takes its Witty Pi shutdown alarm out of the schedule.

Now the node only logs the mDot's `EMERGENCY: PA_6 pin state: N` line. A bare
`21` payload, which reaches the Pi when the mDot's Class C processor is off
(`AT+CPROC=0`), is logged the same way.

## Emergency mode for a sleeping node

The mDot drops downlinks while the Pi is off, so the API delivers `21 91 HH` the
way it delivers a remote debug request: send it; if the node doesn't answer within
60 s, send `!` and resend the command every 20 s for up to 5 minutes, until the
node's status uplink acks it (`ak` equal to the frame's seq; `em: 1` shows it took
effect). Over IP the command is offered in every reply until `POST /ip/ack`. A woken Pi runs `lora_daemon` before ticktalk,
so the command arrives before the normal cycle ends.

## While in emergency mode

- `call_shutdown` skips the iteration limit, and the Witty Pi's shutdown alarm moves
  from the schedule to a backstop an hour after emergency mode should end, with
  the startup alarm 10 minutes after that, so the unit keeps capturing.
  `call_shutdown` re-arms the backstop each iteration, so a resend moves it out
  too. Three layers end emergency mode: the graph at the time limit,
  `max_awake.py` 15 minutes later if the graph hangs, and the Witty Pi's backstop
  an hour later if the Pi itself is stuck (#209); the startup alarm then brings
  the unit back by itself. With `emergency_max_hours` 0 (no limit) the shutdown
  alarm is cleared instead.
- It ends after `HH` hours (`emergency_since` + `emergency_max_hours`), on `99 99`,
  or when the dashboard turns it off. Ending it re-arms the installed
  `schedule.wpi` (it never rewrites the file). With none installed it puts one
  back: `~/wittypi/schedule.wpi.deploy`, else the newest `schedule.wpi.deploy-*`
  (in `~/wittypi` or `~`), else the newest `~/wittypi/schedule.wpi.bak-*`, else
  `config/wittypi/watercam_daylight_2h.wpi`; only if none can be installed does it
  generate one from the `wittypi_*` settings.
- If the capture graph is hung and can't end it, `max-awake.timer`
  (`tools/max_awake.py`) does: 15 minutes past that deadline it turns
  `emergency_mode` off and powers the unit off; the next slot is armed on the way down.

## Older nodes

A bare `21` payload (the IP command before `21 91`) and frames without a seq byte
are no longer accepted. Update the nodes and the API together: a node running older
software ignores frames that start with a seq byte, and a new node no longer treats
`!` as emergency mode.
