# Remote start and emergency mode

Two separate commands, each with one meaning whatever the Pi's power state.

| Downlink (fPort 10) | Meaning | Who handles it |
|---|---|---|
| `!` (0x21, exactly one byte) | **Remote start**: power a sleeping Pi on | The mDot. If the Pi is off (GPIO5/PA_6 low) it pulses the Witty Pi switch (PB_1, 1.5 s); if the Pi is on it does nothing. It never forwards the byte. |
| `21 91 HH` | **Emergency mode on** for `HH` hours (hex, `01`–`A8`, so up to 168 h); `21 91 00` uses `emergency_max_hours` (default 24) | The node, over LoRa or IP. It answers with its status (`{"dbg":1,...,"em":1}`). |
| `99 99` | **Emergency mode off** | The node, over LoRa or IP. It answers with its status. |

## Why they were split

`!` used to mean both. A sleeping Pi woke for one normal cycle and was **not** in
emergency mode, because the mDot doesn't forward `!` and its log line about it is
printed before the Pi is up. A Pi that was already on read that log line and
**turned emergency mode on**. So "Enable Emergency Mode" on the dashboard did nothing
lasting for a sleeping node, and waking a node that happened to be on put it into
emergency mode, which clears its Witty Pi shutdown alarm.

Now the node only logs the mDot's `EMERGENCY: PA_6 pin state: N` line. A bare
`21` payload, which reaches the Pi when the mDot's Class C processor is off
(`AT+CPROC=0`), is logged the same way.

## Emergency mode for a sleeping node

The mDot drops downlinks while the Pi is off, so the API delivers `21 91 HH` the
way it delivers a remote debug request: send it; if the node doesn't answer within
60 s, send `!` and resend the command every 20 s for up to 5 minutes, until the
node's status uplink shows `em: 1`. A woken Pi runs `lora_daemon` before ticktalk,
so the command arrives before the normal cycle ends.

## While in emergency mode

- `call_shutdown` skips the iteration limit and the Witty Pi shutdown alarm is
  cleared, so the unit keeps capturing.
- It ends after `HH` hours (`emergency_since` + `emergency_max_hours`), on `99 99`,
  or when the dashboard turns it off. Ending it re-arms the installed
  `schedule.wpi` (it never rewrites the file).

## Transition

Nodes accept the old IP code `21` as emergency on until the API sends `21 91`
everywhere. Update the nodes before the API: an old node ignores `21 91` (so no
emergency mode), and a new node no longer treats `!` as emergency mode.
