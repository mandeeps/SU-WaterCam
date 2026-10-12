# Node security

A node sits unattended in the field. Assume someone can pick it up, pull its SD
card or attach to its serial port. The aim is that one lost unit costs one
unit: everything it holds can be revoked without touching the others.

## What a node exposes

- **Over the network:** nothing but `tailscaled`. OpenSSH and filebrowser aren't
  running; remote access is Tailscale SSH, governed by the tailnet ACL. Multicast
  name responders are off (`config/systemd-resolved/90-watercam.conf`).
- **Physically:** the SD card (unencrypted: the Pi has no secure boot or TPM, and
  the unit must boot unattended, so disk encryption would need its key on the same
  card) and the serial console (`console=serial0`), kept on purpose for field
  repairs.
- **`pi` is effectively root.** It has passwordless `sudo` (the Raspberry Pi OS
  default), and root services run code from `/home/pi`. The software relies on
  that, so a login as `pi` must be treated as root.

## The serial console stays: give each unit its own strong password

Serial access is how a unit is fixed in the field when the network is down, so it
stays enabled. It is a root login for anyone holding the unit, so:

- **Every unit gets its own `pi` password**, at least 16 random characters, set on
  the bench (`passwd`). Never the image default, and never one shared between units:
  one unit's password must not open the others.
- **Keep the passwords in the team's password manager**, one entry per unit
  (`ufo-01-01-NNN serial`), not in this repo, the SD image or a shared document.
- **Change it** when someone who knew it leaves the project, or after the unit was
  out of your hands (lost and found, returned from a partner).

## Secrets on the SD card

| Secret | Where | Revoke by |
|---|---|---|
| Tailscale identity | `/var/lib/tailscale/` | removing the machine in the Tailscale admin console |
| API device token | `runtime_config.json` (`ip_upload.api_key`, mode 600) | dashboard: Management → the device → Revoke token |
| LoRaWAN keys | in the mDot | deleting the device in ChirpStack (and its keys) |
| WiFi passwords | `/etc/NetworkManager/system-connections/` | changing the network's password, if it isn't a per-person or guest login |
| `pi` password | `/etc/shadow` (hashed) | nothing to revoke if it was unique to the unit |

Prefer WiFi logins that are unique to the unit or a guest network, so a lost unit
never means changing a password other devices use.

## When a unit is lost, stolen or tampered with

Do these the same day, in this order:

1. **Tailscale:** remove the machine in the admin console (Machines → `ufo-01-01-NNN`
   → Remove). This is the one that matters most: it cuts the unit off from the API
   server and everything else on the tailnet.
2. **API token:** revoke it on the dashboard (Management → select the device →
   Revoke token). With `IP_AUTH=enforce`, the token stops working at once.
3. **LoRaWAN:** in ChirpStack, disable the device (the dashboard then shows it as
   disabled) or delete it, and change its keys before it is ever used again.
4. **WiFi:** change the password of any network the unit knew whose password other
   devices share.
5. **Note it** in the deployment log: when, where, and what was done.

A recovered unit is reflashed from the release image before it goes back out, with a
new Tailscale login, token, `pi` password and LoRaWAN keys.

## Software updates

- **Releases are signed** (`docs/RELEASES.md`): a node checks the tag's signature
  before checking it out, so it only installs what a maintainer released.
- **Security patches** come with releases, applied on the bench over WiFi
  (`docs/RELEASES.md`, making a release). Nodes don't run `unattended-upgrades`: it
  would download packages over cellular. Tailscale is the one internet-facing
  component, so keep it current at every release.
