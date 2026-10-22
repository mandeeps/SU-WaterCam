# https://www.uugear.com/forums/technial-support-discussion/witty-pi-4-how-to-synchronise-time-with-internet-on-boot/
#
[ -z $BASH ] && { exec bash "$0" "$@" || exit; }
#!/bin/bash
#
# Script to write network time, or failing that GPS time, to system and the RTC
#
# With the cellular modem kept off unless LoRa and WiFi fail, a unit without WiFi
# has no internet at boot, so network time alone would never correct its clock
# (drift, and the hour after a daylight-saving change). GPS time needs no data
# connection. With neither, the RTC is left alone: writing the system time back
# (as the stock script did) only adds up to a second of error per boot.

# include utilities script in same directory
my_dir="`dirname \"$0\"`"
my_dir="`( cd \"$my_dir\" && pwd )`"
if [ -z "$my_dir" ] ; then
  exit 1
fi
. $my_dir/utilities.sh

# wait long enough so the whole system gets stable before processing
sleep 30

# 1. network time (HTTP Date header, about 1 s)
net_ts=$(get_network_timestamp)
if [[ "$net_ts" != "-1" ]]; then
  log '  Applying network time to system...'
  sudo date -u -s @$net_ts >/dev/null
  system_to_rtc
  echo network > /run/watercam-time-source   # tools/transmit_ip.py correct_clock skips then
  exit 0
fi

# 2. GPS time, from gpsd, only if the modem already has power: USB is off unless
#    something needs it (tools/usb_power.py), and the RTC keeps time well enough.
#    The daily GPS window sets the clock from its fix when nothing else did
#    (tools/gps_window.py).
if [[ ! -e /dev/ttyUSB1 ]]; then
  log '  No network time; the modem is off, so no GPS time: system time and RTC left as they are.'
  exit 0
fi
gps_time=$(timeout 100 python3 $my_dir/gps_time.py 90 2>/dev/null)
gps_ts=$(date -u -d "$gps_time" +%s 2>/dev/null)
if [[ -n "$gps_time" && -n "$gps_ts" && "$gps_ts" -gt 1767225600 ]]; then   # after 2026-01-01
  log "  Applying GPS time ($gps_time) to system..."
  sudo date -u -s @$gps_ts >/dev/null
  system_to_rtc
  echo gps > /run/watercam-time-source
  exit 0
fi

log '  No network or GPS time: system time and RTC left as they are.'
