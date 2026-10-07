# Cellular Data Use

**Measured:** 2026-10-07 on unit 006 (T-Mobile LTE, Quectel EC25), by recording
`wwan0` and `tailscale0` with `tcpdump` during a real ticktalk wake with WiFi off.
`tailscale0` carries our own traffic before encryption; the rest of what crosses
`wwan0` is overhead. Two wakes of about 100 s each.

Cellular is the last resort: LoRa first, then WiFi (see
[IP_TRANSMISSION.md](IP_TRANSMISSION.md#transport-order-lora-wifi-cellular)).

## Per wake

| | Run 1 | Run 2 |
|---|---|---|
| **Total on `wwan0`** | **474 KB** | **126 KB** |
| Our traffic (API requests, DNS) | 10 KB | 10 KB |
| Tailscale probing every relay (DERP) region over HTTPS | ~280 KB | 0 |
| Tailscale's home relay (no direct path to the API server on cellular, so uploads are relayed) | 64 KB | 42 KB |
| Tailscale coordination server (network map resync) | 33 KB | 44 KB |
| Tailscale direct-path probes to the API server's private addresses (Docker bridges, LAN), which can't answer over cellular | 80 KB | 28 KB |
| Tailscale direct-path probes to the server's public address | 14 KB | 8 KB |
| Tailscale log uploads | 0 | 0 |

- **Most of it comes in the first 15 s after the link comes up** (328 KB and
  72 KB). Whether Tailscale probes every relay region varies: run 2 followed a
  `tailscaled` restart and didn't.
- **While the link stays up,** Tailscale costs about 0.4–2 KB/s (1.5–7 MB an
  hour), mostly the private-address probes. A normal wake keeps cellular up for
  about a minute; in emergency mode the unit stays on, so cellular can stay up
  for hours.
- **Turning off log uploads** (`--no-logs-no-support`) made no difference: no
  log traffic was seen either way.

## Options not yet tried (Tailscale stays, for remote access)

| Option | Saves (estimate) | Needs |
|---|---|---|
| Disable unneeded relay regions in the tailnet policy (`derpMap`, region set to `null`), keeping the home region and a few nearby | up to ~220 KB when the region probing happens | Tailnet admin. Applies to every device on the tailnet. |
| On the node, drop RFC1918 destinations on `wwan0` (nftables) so the private-address probes never reach the modem | 28–80 KB per wake, and most of the steady cost | Node change; check `tailscaled` copes with the dropped sends |
| Tighten the ACL so `tag:watercam` only sees the API server and admin machines (006 sees 11 peers, mostly shared-in devices) | ~15–25 KB, plus fewer updates while up | Tailnet admin. `config/tailscale-acl.txt` is out of date: refresh it from the admin console first. |
| Inbound UDP to the API server's Tailscale port through the campus firewall, so a direct path forms | ~10–25 KB | Campus IT |
| Drop the `/health` check before each upload; one POST per wake | ~5 KB | Node change |

Together these might bring a cellular wake to roughly 40–70 KB. That is an
estimate; confirm it with another recorded wake.

The baseline cost of Tailscale reconnecting its coordination and relay links,
about 30–40 KB per link change, remains while it runs.
