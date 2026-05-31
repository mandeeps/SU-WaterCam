# 1NCE SIM Data Exhaustion — Diagnosis & Fixes

1NCE SIMs have a **500 MB lifetime data cap**. Once exhausted the modem shows good
signal and correct APN but the network rejects PDP context attachment — see
`quectel-1nce-debugging.md` for the modem-level symptoms.

This document covers why the 500 MB cap is being consumed faster than expected
across multiple deployed units.

---

## What the application sends over cellular

`ip_upload.enabled` is `false` in `runtime_config.json`. Both
`ip_uplink_transmit` and `ip_downlink_poll_and_apply` return
`{"status": "disabled"}` and make no HTTP requests. **The application itself
is not the cause.**

LoRa transmissions (via the MultiTech mDot) do not use the SIM at all.

---

## Root causes — in order of impact

### 1. Tailscale auto-update (systemic, highest impact)

Tailscale updates its own daemon binary automatically by default. Each update
is 20–50 MB. When a new release drops, all devices update on their next wake
window (7:00 or 21:00), hitting the 1NCE SIM on every unit simultaneously.

**Confirm:**
```bash
tailscale version
tailscale debug prefs | grep -i auto
```

**Fix — run on every deployed device:**
```bash
sudo tailscale set --auto-update=false
```

---

### 2. `apt-daily.timer` — automatic package list refresh (systemic)

Debian/RPi OS ships a built-in systemd timer that runs `apt-get update`
automatically. No `unattended-upgrades` package is required — the timer is
part of systemd itself. Each run fetches ~15–30 MB of package metadata from
Debian mirrors over whatever interface holds the default route (cellular on
field-deployed units). It fires at a randomized time each day and can land
inside a WittyPi wake window.

**Confirm:**
```bash
systemctl status apt-daily.timer apt-daily-upgrade.timer
journalctl -u apt-daily.service --since "7 days ago" | grep -E "Downloaded|Fetched|MB"
```

**Fix — mask both timers on deployed devices:**
```bash
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer
```

---

### 3. Tailscale SSH remote access sessions

The post-install procedure sets up Tailscale SSH for remote debugging. Every
SSH session during a wake window routes all traffic over the 1NCE SIM. Common
activities that consume significant data:

- `apt upgrade` or `apt install` — 10s to 100s of MB per run
- `rpi-update` — downloads firmware binaries, can be 50+ MB
- `git pull` — small, but still cellular
- `scp` or `rsync` of image files — raw camera images are several MB each

These are not always visible as "cellular usage" to the person doing the work
because Tailscale abstracts the underlying transport.

---

### 4. `filebrowser.service` — image directory served over HTTP

`filebrowser.service` starts after `network-online.target` and serves
`SU-WaterCam/images` over HTTP during every wake window. It is reachable via
Tailscale. Browsing the image directory or downloading files over a Tailscale
connection transfers data over the cellular interface.

**Fix on field-deployed units:**
```bash
sudo systemctl disable --now filebrowser.service
```

---

### 5. Route metric misconfiguration

`config/dhcpcd.conf` defines a static `usb0` interface (`10.42.0.2`, gateway
`10.42.0.1`). If the cellular route metric is not set correctly, cellular
becomes the default route for all traffic including bulk downloads. The
NetworkManager profile must have a higher metric than any other interface:

```bash
sudo nmcli con mod Quectel ipv4.route-metric 100
sudo nmcli con mod Quectel ipv6.route-metric 100
```

Verify the active routing table during a live session:
```bash
ip route show
```
The `wwan0` (or `wwp…`) default route should have metric 100; Ethernet/WiFi
should have a lower metric (higher priority).

---

## How to measure actual cellular usage

```bash
# Bytes in/out on the cellular interface since last boot
ip -s link show wwan0

# Tailscale traffic summary
tailscale status
tailscale debug metrics | grep bytes

# Recent apt-daily activity
journalctl -u apt-daily.service --since "30 days ago" | grep -E "Downloaded|Fetched|MB|error"

# Tailscale update history
journalctl -u tailscaled --since "30 days ago" | grep -i "update\|upgrade\|download"
```

---

## Recommended hardening checklist for deployed units

| Action | Command |
|--------|---------|
| Disable Tailscale auto-update | `sudo tailscale set --auto-update=false` |
| Mask apt timers | `sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer` |
| Disable filebrowser | `sudo systemctl disable --now filebrowser.service` |
| Verify cellular route metric | `sudo nmcli con mod Quectel ipv4.route-metric 100` |
| Avoid `apt upgrade` / `rpi-update` over SSH on cellular | Do package updates only when device is on a wired/WiFi connection |

---

## Related documents

- `quectel-1nce-debugging.md` — modem-level diagnosis when a SIM is exhausted
  (registration stuck, `packet service state: detached`)
- `runtime_config.json` — `ip_upload.enabled` controls application-level
  cellular uplink (currently `false`)
