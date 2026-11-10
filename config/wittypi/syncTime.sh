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

# (overridable only for tests/test_network_time.py)
TIME_SOURCE=${WATERCAM_TIME_SOURCE:-/run/watercam-time-source}
RTC_UNTRUSTED=${WATERCAM_RTC_UNTRUSTED:-/run/watercam-rtc-untrusted}
NET_ERR=${WATERCAM_NET_ERR:-/tmp/watercam-network-time.err}

# The Witty Pi daemon found the RTC's time bad: it has already copied the
# (possibly days-old) system time into the RTC, so a large correction must be
# allowed, here and by LoRa time and the server's (tools/lora_time.py,
# tools/transmit_ip.py). Marked before the wait so they see it too.
rtc_verdict=$(tail -c 8192 $my_dir/wittyPi.log 2>/dev/null | grep -aoE 'RTC has bad time|RTC has good time' | tail -1)
if [[ "$rtc_verdict" == "RTC has bad time" && ! -e $RTC_UNTRUSTED ]]; then
  echo "Witty Pi: RTC has bad time" > $RTC_UNTRUSTED
fi

# wait long enough so the whole system gets stable before processing
sleep ${SYNC_WAIT_S:-30}

# LoRa time (lora_daemon, about 0.3 s) usually comes first and is finer than
# network time; GPS time is finer still. Don't overwrite either.
prior=$(cat $TIME_SOURCE 2>/dev/null)
if [[ "$prior" == "lora" || "$prior" == "gps" ]]; then
  log "  Clock already set from $prior time this boot: network time not needed."
  exit 0
fi

# 1. network time: our API server (tailnet or https), else public https servers
#    with the certificate checked; never plain http, which a captive portal or
#    access point can answer. Timed out, and checked like the other sources
#    (network_time.py).
net=$(python3 $my_dir/network_time.py 2>$NET_ERR)
if [[ -n "$net" ]]; then
  read -r net_ts net_from <<< "$net"
  log "  Applying network time ($net_from) to system..."
  sudo date -u -s @$net_ts >/dev/null
  system_to_rtc
  echo network > $TIME_SOURCE   # tools/transmit_ip.py correct_clock and the GPS window skip then
  exit 0
fi
log "  No network time: $(head -c 200 $NET_ERR 2>/dev/null)"

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
  echo gps > $TIME_SOURCE
  exit 0
fi

log '  No network or GPS time: system time and RTC left as they are.'
