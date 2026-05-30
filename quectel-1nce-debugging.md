# Quectel EC25 + 1NCE SIM Debugging Reference

## Symptoms that led here

- Modem visible in `mmcli -L` but registration state stuck at `searching`
- `packet service state: detached` despite good signal
- `mmcli --simple-connect` returning `NetworkTimeout` or `Timeout was reached`
- No change after reboot or ModemManager restart

---

## Debugging procedure

### 1. Check ModemManager state

```bash
mmcli -m 0 | grep -E "state|packet|registration|signal"
```

Key fields to check:
- `state` should reach `registered` before a connection is possible
- `registration: searching` with `packet service state: detached` means the modem is not attaching to any network
- Signal quality percentage gives a rough sanity check

### 2. Stop ModemManager and NetworkManager

ModemManager holds the AT ports open. Stop both before using tio directly.

```bash
sudo systemctl stop ModemManager NetworkManager
```

### 3. Connect to the AT port

The Quectel EC25 exposes multiple ttyUSB ports. Their roles:

| Port | Role |
|------|------|
| ttyUSB0 | ignored by ModemManager |
| ttyUSB1 | GPS NMEA output |
| ttyUSB2 | AT commands |
| ttyUSB3 | AT commands |

```bash
tio -b 115200 /dev/ttyUSB2
```

Exit tio with `ctrl-t q`.

### 4. Key AT commands

**Registration status (GSM/UMTS):**
```
AT+CREG?
```
Response format: `+CREG: <n>,<stat>`
- stat 0 = not registered, not searching
- stat 1 = registered, home
- stat 2 = searching
- stat 3 = registration denied
- stat 5 = registered, roaming

**Registration status (LTE):**
```
AT+CEREG?
```
Same stat values as above. Use this for LTE/4G status.

**Signal quality:**
```
AT+CSQ
```
Returns `+CSQ: <rssi>,<ber>`. RSSI values: 0-9 very weak, 10-14 marginal, 15-19 good, 20-30 strong, 99 no signal.

**PDP context / APN:**
```
AT+CGDCONT?
```
Context 1 should show `iot.1nce.net` for 1NCE SIMs. If it shows something else or is blank, the attach APN is wrong.

**Operator selection mode:**
```
AT+COPS?
```
Mode 0 = automatic (correct). Mode 1 = manual (may be stuck on a specific operator).

**Force registration onto a specific operator:**
```
AT+COPS=1,2,"310410"
```
310410 is AT&T. This will either register or return an error code indicating why registration is being rejected. Useful for getting a specific failure reason rather than staying silently in searching state.

**Band configuration:**
```
AT+QCFG="band"
```
Returns `0x<gsm_bands>,0x<lte_bands>,0x<tds_bands>`. For AT&T US, LTE bands 2, 4, and 12 (hex `0x80a`) are the primary ones.

**SIM IMSI:**
```
AT+CIMI
```
Returns the 15-digit IMSI. For 1NCE SIMs this starts with `901405`. Use this to identify the SIM if you have multiple.

**SIM ICCID:**
```
AT+CCID
```
Returns the ICCID (the number printed on the physical SIM card). Use this to look up the SIM in the 1NCE portal at https://portal.1nce.com.

---

## 1NCE SIM-specific checks

1NCE SIMs have a **lifetime data limit of 500MB**. Once exhausted, the SIM will still attempt registration but the network will reject packet data attachment. The modem will show good signal and correct APN configuration but stay in `searching`/`detached` state.

**Check SIM status:**
- Log into https://portal.1nce.com
- Find the SIM by ICCID (from `AT+CCID`)
- Check: activation status, data usage, roaming status

**1NCE IMSI prefix:** `901405` (MCC 901, MNC 40)

**1NCE US roaming partner:** AT&T (operator code `310410`)

If the SIM shows as active with remaining data but the modem still won't register, try forcing registration onto AT&T explicitly with `AT+COPS=1,2,"310410"` to get a specific rejection error code.

---

## Trixie-specific issues

After upgrading from Bookworm to Trixie (systemd 257, ModemManager 1.24):

**Interface rename:** systemd renames `wwan0` to a predictable name like `wwp1s0u1u3i4`. Fix with a `.link` file:

`/etc/systemd/network/10-wwan.link`
```ini
[Match]
Driver=qmi_wwan
Path=platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.4

[Link]
Name=wwan0
```

Confirm the correct `Path=` value with:
```bash
udevadm info /sys/class/net/<interface> | grep "^E: ID_PATH="
```

**Initial EPS bearer APN:** Newer ModemManager is stricter about the LTE attach APN. Set it explicitly before connecting:

```bash
sudo mmcli -m 0 --3gpp-set-initial-eps-bearer-settings="apn=iot.1nce.net"
```

**AT command access:** `--command` flag requires debug mode in ModemManager 1.24. Use tio on ttyUSB2 directly instead.

---

## GPS with exhausted or missing SIM

The EC25 GNSS receiver is independent of the cellular data stack. It communicates
directly with satellites and does not need a PDP context, a working SIM, or a data
session. NMEA sentences stream on `ttyUSB1` as long as GNSS is enabled via AT command.

**What breaks when the SIM is exhausted:**
- PDP context attachment is rejected by the network → `wwan0` never gets an IP
- `network-online.target` is never reached

**What does NOT break:**
- `ttyUSB1`, `ttyUSB2`, `ttyUSB3` — still accessible
- GNSS once enabled with `AT+QGPS=1`

### Old `gps.service` problem

The existing `config/gps.service` has:
```
Requires=network-online.target
After=network-online.target
```
This gates GPS initialisation on network connectivity, so GPS never starts when
the SIM is exhausted.

### Replacement: `quectel-gnss.service`

`config/quectel-gnss.service` + `config/quectel-gnss-enable` replace the old service.
The new service waits only for `dev-ttyUSB2.device` (the USB modem appearing) and
sends `AT+QGPS=1` directly to the AT port. The `77-mm-quectel-ignore-gps.rules` udev
rule already prevents ModemManager from claiming the ttyUSB ports, so the write
succeeds without stopping MM.

**Install on the Pi:**
```bash
sudo cp config/quectel-gnss-enable /usr/local/bin/
sudo chmod +x /usr/local/bin/quectel-gnss-enable
sudo cp config/quectel-gnss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable gps.service          # disable old service
sudo systemctl enable --now quectel-gnss.service
```

**Verify GNSS is running:**
```bash
# Should show flowing $GPGGA / $GPRMC lines within ~30 s of modem appearing
cat /dev/ttyUSB1
```

**`gpsd` must read from `ttyUSB1`** — set in `/etc/default/gpsd`:
```ini
DEVICES="/dev/ttyUSB1"
GPSD_OPTIONS="-n"
```

---

## Restore normal operation after debugging

```bash
sudo systemctl start ModemManager NetworkManager
```

NetworkManager will pick up the Quectel connection profile and attempt to connect automatically.

---

## Quick reference: connection profile setup

```bash
sudo nmcli connection add type gsm ifname cdc-wdm0 con-name Quectel apn iot.1nce.net
sudo nmcli con mod Quectel ipv4.route-metric 100
sudo nmcli con mod Quectel ipv6.route-metric 100
sudo nmcli con mod Quectel connection.autoconnect yes
```

Setting route metric to 100 ensures Ethernet and WiFi are preferred over cellular when available.
