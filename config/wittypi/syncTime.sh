# https://www.uugear.com/forums/technial-support-discussion/witty-pi-4-how-to-synchronise-time-with-internet-on-boot/
#
[ -z $BASH ] && { exec bash "$0" "$@" || exit; }
#!/bin/bash
#
# Script to set the system clock and the RTC at boot, from the first trustworthy source:
#
#   LoRa time  lora_daemon's answer (lora_answer.py), applied as soon as it arrives
#   network    our API server or public https servers (network_time.py); replaces
#              LoRa time only when they disagree by more than NET_AGREE_S, its own error
#   GPS        only when neither came and the modem already has power
#   LoRa time  a late answer, waited for up to LORA_LATE_WAIT_S when nothing else came
#
# With the cellular modem kept off unless LoRa and WiFi fail, a unit without WiFi
# has no internet at boot, so network time alone would never correct its clock
# (drift, and the hour after a daylight-saving change). LoRa and GPS time need no
# data connection. With none, the RTC is left alone: writing the system time back
# (as the stock script did) only adds up to a second of error per boot. Later in
# the wake the server's time (tools/transmit_ip.py correct_clock) and the daily GPS
# window (tools/gps_window.py) still correct a clock that came from LoRa or the RTC.
# docs/TIME_SYNC.md has the whole picture.

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
LORA_ERR=${WATERCAM_LORA_ERR:-/run/watercam-lora-time.err}
# Present once this script has finished: lora_daemon stops waiting to see whether
# its answer was used, and doesn't ask when it is too late to be (tools/lora_time.py)
SYNC_DONE=${WATERCAM_TIME_SYNC_DONE:-/run/watercam-time-sync-done}
NET_AGREE_S=2
rm -f $SYNC_DONE
trap 'touch $SYNC_DONE' EXIT

# The Witty Pi daemon found the RTC's time bad: it has already copied the
# (possibly days-old) system time into the RTC, so a large correction must be
# allowed, here and by LoRa time and the server's (tools/lora_time.py,
# tools/transmit_ip.py). Marked before the wait so they see it too.
rtc_verdict=$(tail -c 8192 $my_dir/wittyPi.log 2>/dev/null | grep -aoE 'RTC has bad time|RTC has good time' | tail -1)
if [[ "$rtc_verdict" == "RTC has bad time" && ! -e $RTC_UNTRUSTED ]]; then
  echo "Witty Pi: RTC has bad time" > $RTC_UNTRUSTED
fi

# LoRa time: apply lora_daemon's answer once. Returns 0 when the clock now comes
# from LoRa time, 2 when the answer was refused, 3 while the daemon is still
# asking, 4 when no answer is coming (lora_answer.py's exit statuses).
lora_tried=
try_lora() {
  [[ -n $lora_tried ]] && return 4
  local out rc ts off
  out=$(python3 $my_dir/lora_answer.py 2>$LORA_ERR); rc=$?
  case $rc in
    0) lora_tried=1
       read -r ts off <<< "$out"
       log "  Applying LoRa time (the clock was $off s off) to system..."
       sudo date -u -s @$ts >/dev/null
       system_to_rtc
       echo lora > $TIME_SOURCE   # verified: capture_time, correct_clock and the GPS window see it
       return 0 ;;
    1) lora_tried=1
       log "  $(head -c 200 $LORA_ERR 2>/dev/null)"
       return 2 ;;
    3) return 3 ;;
    *) return 4 ;;
  esac
}

# wait long enough so the whole system gets stable before processing, taking
# LoRa time as soon as it comes (lora_daemon asks once the mDot has joined)
wait_end=$((SECONDS + ${SYNC_WAIT_S:-30}))
while (( SECONDS < wait_end )); do
  try_lora
  sleep 1
done
try_lora

# 1. network time: our API server (tailnet or https), else public https servers
#    with the certificate checked; never plain http, which a captive portal or
#    access point can answer. Timed out, and checked like the other sources
#    (network_time.py).
net=$(python3 $my_dir/network_time.py 2>$NET_ERR)
if [[ -n "$net" ]]; then
  read -r net_ts net_from <<< "$net"
  if [[ "$(cat $TIME_SOURCE 2>/dev/null)" == "lora" ]] &&
     awk -v a="$net_ts" -v b="$(date +%s.%N)" -v t=$NET_AGREE_S 'BEGIN { d = a - b; exit !(d <= t && d >= -t) }'; then
    log "  Network time ($net_from) agrees with LoRa time: kept LoRa time (finer)."
    exit 0
  fi
  log "  Applying network time ($net_from) to system..."
  sudo date -u -s @$net_ts >/dev/null
  system_to_rtc
  echo network > $TIME_SOURCE   # tools/transmit_ip.py correct_clock and the GPS window skip then
  exit 0
fi
log "  No network time: $(head -c 200 $NET_ERR 2>/dev/null)"
if [[ "$(cat $TIME_SOURCE 2>/dev/null)" == "lora" ]]; then
  log "  Clock set from LoRa time."
  exit 0
fi

# 2. GPS time, from gpsd, only if the modem already has power: USB is off unless
#    something needs it (tools/usb_power.py), and the RTC keeps time well enough.
#    The daily GPS window sets the clock from its fix when nothing else did
#    (tools/gps_window.py).
if [[ ! -e /dev/ttyUSB1 ]]; then
  log '  No network time; the modem is off, so no GPS time.'
else
gps_time=$(timeout 100 python3 $my_dir/gps_time.py 90 2>/dev/null)
gps_ts=$(date -u -d "$gps_time" +%s 2>/dev/null)
if [[ -n "$gps_time" && -n "$gps_ts" && "$gps_ts" -gt 1767225600 ]]; then   # after 2026-01-01
  log "  Applying GPS time ($gps_time) to system..."
  sudo date -u -s @$gps_ts >/dev/null
  system_to_rtc
  echo gps > $TIME_SOURCE
  exit 0
fi
fi

# 3. a late LoRa answer: the daemon is still waiting for the join or the network's reply
late_end=$((SECONDS + ${LORA_LATE_WAIT_S:-60}))
while true; do
  try_lora; rc=$?
  (( rc == 0 )) && exit 0
  (( rc != 3 || SECONDS >= late_end )) && break   # refused, none coming, or waited long enough
  sleep 2
done

log '  No LoRa, network or GPS time: system time and RTC left as they are.'
