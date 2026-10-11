# Remote debug session: Tailscale SSH over cellular, started over LoRa

An admin can ask a deployed node, over LoRa, to bring its cellular connection up
and stay reachable with `tailscale ssh` for a set time, even if the Pi is off.
The node stops its capture workflow, so nothing shuts it down mid-session, and
everything goes back to normal when the admin ends the session or the time runs
out.

It is a standalone command, separate from emergency mode (which keeps capturing)
and from the planned maintenance mode (#117, Wi-Fi only).

## Command

`18 98 NN`, on fPort 10 like the other downlinks. It has the same meaning over IP.

| `NN` | Meaning |
|---|---|
| `01`–`18` (hex) | Start the session, or extend a running one, for `NN × 10` minutes (10 min to 4 h) from now |
| `00` | End the session and restore normal operation |

Values above `18` are capped at 4 h. On the node, the LoRa decoder sees it as
channel `18`, command `98`, value `NN`, read as hex.

## Sequence

```
admin: dashboard "Remote debug" (minutes, wake if off)
API:   send 18 98 NN (Class C, delivered at once)
         node awake? -> lora_daemon starts the session -> status uplink within ~2 min
         no status after 60 s and "wake" ticked:
           send "!"         mDot pulses the Witty Pi switch: Pi boots (~60 s)
           resend 18 98 NN every 20 s for 5 min, until a status uplink says rd > 0
node:  1. clear the Witty Pi shutdown alarm       (the Pi stays on past its window)
       2. stop ticktalk.service                   (no capture load, no call_shutdown)
       3. nmcli connection up Quectel             (pi may do this: polkit rule)
       4. wait for Tailscale: BackendState Running and Self.Online (up to 120 s)
       5. LoRa status uplink {"dbg":1,"rd":<min left>,"tn":<tailscale 0/1>,"cl":<cellular 0/1>}
admin: tailscale ssh pi@ufo-01-01-NNN...
end:   dashboard "End remote debug" (18 98 00), or on the node:
         ~/SU-WaterCam/venv/bin/python ~/SU-WaterCam/tools/remote_debug.py stop
       or the time runs out (checked every 30 s by remote-debug.timer)
node:  1. cellular down, if it is managed on demand (Quectel autoconnect off)
       2. re-arm the Witty Pi from the installed schedule.wpi (runScript.sh)
       3. start ticktalk.service: a normal cycle, then its usual shutdown
       4. LoRa status uplink with rd 0
```

`remote_debug.py stop --shutdown` skips the capture cycle and powers off at once;
the Witty Pi wakes the Pi at its next scheduled slot.

### Why the API resends after `!`

The mDot does not pass `!` to the Pi, and it drops any downlink that arrives
while the Pi is off. A woken Pi runs its normal boot: `lora_daemon` starts first,
then ticktalk, which shuts the Pi down after its cycles (a few minutes). The
resends make sure `18 98` reaches `lora_daemon` in that gap. The API sends the
plain command first and only sends `!` when there is no answer: `!` to a running
Pi is harmless for the power (the mDot checks the Pi's GPIO5 and does nothing),
but the Pi reads the mDot's log line about it and turns emergency mode on.

The Witty Pi itself doesn't cut a woken Pi short: booted between slots, it arms
the shutdown for the end of the next ON slot, and `recovery_boot` only defers
boots caused by power returning, not switch presses.

## Node pieces

- **`tools/remote_debug.py`**: `start --minutes N`, `stop [--shutdown]`,
  `status`, `tick`. State in `data/remote_debug.json`: end time, what started
  it, and whether cellular was already up. Written atomically.
- **`lora_daemon`**: on `18 98 NN`, runs `remote_debug.start()` or `stop()` on
  a thread, so the serial listener isn't blocked while Tailscale connects.
- **IP downlink**: `18 98 NN` writes a request file that the timer picks up, since
  ticktalk can't stop itself and keep running the session.
- **`remote-debug.timer`** runs `remote_debug.py tick` every 30 s as `pi`. It
  applies a pending request and ends an expired session. It doesn't depend on
  `lora_daemon`, which a node without an mDot doesn't run.
- **Privileges**: `pi` stops and starts ticktalk with
  `doas systemctl stop|start ticktalk.service` (two nopass rules added to
  `config/doas.conf`), falling back to `sudo -n`. The Witty Pi calls are the ones
  emergency mode already uses. `nmcli` needs the polkit rule from section 1.5.1
  of the deployment guide.

## Safety

- **Every session ends:** 4 h maximum, and the timer ends an expired session even
  after a reboot or a `lora_daemon` crash.
- **Restore only re-arms the installed schedule.** It never writes `schedule.wpi`.
  (`apply_emergency_schedule(False)` regenerates the file from config values, so
  it isn't used here.)
- **Restarting a node mid-session doesn't strand it:** at boot, `tick` sees the
  session is still running and keeps ticktalk stopped. A session that expired
  while the Pi was off is ended on the first tick.
- **Data rate:** the status uplink is trimmed to the mDot's payload limit, keeping
  `{"dbg":1,"rd":N}` (about 17 B), which is what the API confirms with. That fits at
  SF9 or faster. At SF10 (11 B) nothing useful fits, so the node sends no status
  and the API can't confirm; the session still starts.
- **Cost:** a session uses roughly 1–5 MB of the 500 MB SIM (Tailscale overhead
  plus the SSH traffic) and keeps the Pi on at about 2–2.6 W. Use short sessions
  on units with marginal power.

## Install on a node

```bash
cd ~/SU-WaterCam
sudo cp config/remote-debug.service config/remote-debug.timer /etc/systemd/system/
sudo cp config/doas.conf /etc/doas.conf
sudo systemctl daemon-reload && sudo systemctl enable --now remote-debug.timer
sudo systemctl restart lora_daemon
```
