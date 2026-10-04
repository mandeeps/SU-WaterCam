#!/bin/bash
# Load the node in phases while sampling the WittyPi and the Pi's throttle flags,
# then summarise with tools/power_test_report.py.
#
#   idle    10 s with nothing extra running
#   infer   5 segmentations through the SegFormer daemon (skipped if it isn't running)
#   stress  30 s with all four cores busy
#   ping    --modem only: 25 s of tiny pings over wwan0
#   full    --modem only: 45 s of segmentation, camera captures, 4-core stress and pings
#   cool    10 s to settle
#
# The modem phases send 8-byte pings to 1.1.1.1 over wwan0 only: about 10 KB in
# total, counted and printed at the end. Nothing is uploaded and no capture is saved.
#
#   tools/power_load_test.sh                 # compute load only
#   tools/power_load_test.sh --modem         # add the cellular phases
#   tools/power_load_test.sh -t images/<capture>/final_5_band.tiff -o /tmp/run.csv
#
# Run it on the supply you want to measure, then compare the mV/A slopes between
# runs (see docs/UNIT006_POWER_FAILURE.md).
set -u

I2CGET=${I2CGET:-/usr/sbin/i2cget}   # overridable so tests can stand in a fake bus
SOCK=/run/segformer/segformer.sock

# One WittyPi register as a decimal number, or nothing if the read failed.
wp() {
  local v
  v=$("$I2CGET" -y 1 0x08 "$1" 2>/dev/null) || return 0
  [[ $v =~ ^0x[0-9a-fA-F]+$ ]] && printf "%d" "$v"
}

# A voltage or current ("4.86"), or empty if any read failed: a failed read must
# not become 0.00, which would wreck the minimum and the mV/A fit. The integer is
# read again after the hundredths and the pair retried if it moved, since the
# WittyPi can update between the two reads (4.99 -> 5.00 read as 4.00).
reading() {
  local i d again try
  for try in 1 2 3; do
    i=$(wp "$1"); d=$(wp $(($1 + 1))); again=$(wp "$1")
    [ -n "$i" ] && [ -n "$d" ] && [ -n "$again" ] || return 0
    if [ "$i" = "$again" ]; then printf "%s.%02d" "$i" "$d"; return 0; fi
  done
}

main() {
  REPO=$(cd "$(dirname "$0")/.." && pwd)
  OUT=~/powertest_$(date +%Y%m%d-%H%M%S).csv
  TIFF=""
  MODEM=0

  while [ $# -gt 0 ]; do
    case "$1" in
      -m|--modem) MODEM=1 ;;
      -t|--tiff)  TIFF=$2; shift ;;
      -o|--out)   OUT=$2; shift ;;
      -h|--help)  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
  done

  # Fail now, not after two minutes of load, if the CSV can't be written.
  if ! { echo "t,phase,throttled,arm_hz,vin,vout,iout,temp_c" > "$OUT"; } 2>/dev/null; then
    echo "Cannot write $OUT" >&2; exit 2
  fi
  if [ -z "$TIFF" ]; then
    TIFF=$(ls -t "$REPO"/images/*/final_5_band.tif* 2>/dev/null | head -1)
  fi
  WORK=$(mktemp -d)
  PHASE=$WORK/phase
  LOOPS=()
  trap cleanup EXIT

  CAN_SEGMENT=0
  if [ -S "$SOCK" ] && [ -n "$TIFF" ] && [ -f "$TIFF" ]; then
    CAN_SEGMENT=1
    echo "capture for segmentation: $TIFF"
  else
    echo "skipping segmentation: needs the SegFormer daemon ($SOCK) and a final_5_band.tiff (-t)" >&2
  fi
  if [ $MODEM -eq 1 ] && [ ! -e /sys/class/net/wwan0 ]; then
    echo "no wwan0 interface; running without the modem phases" >&2
    MODEM=0
  fi
  local rx0=0 tx0=0
  if [ $MODEM -eq 1 ]; then
    rx0=$(cat /sys/class/net/wwan0/statistics/rx_bytes); tx0=$(cat /sys/class/net/wwan0/statistics/tx_bytes)
  fi

  echo idle > "$PHASE"; sampler & LOOPS+=($!)
  sleep 10

  if [ $CAN_SEGMENT -eq 1 ]; then
    echo infer > "$PHASE"
    for _ in 1 2 3 4 5; do segment_once; done
  fi

  echo stress > "$PHASE"; burn 30; sleep 30

  if [ $MODEM -eq 1 ]; then
    echo ping > "$PHASE"; pings 50
    echo full > "$PHASE"
    touch "$WORK/run_cam" "$WORK/run_seg"
    local full_loops=()
    ( while [ -e "$WORK/run_cam" ]; do rpicam-still -n -t 300 -o "$WORK/cam.jpg" >/dev/null 2>&1; done ) &
    full_loops+=($!)
    if [ $CAN_SEGMENT -eq 1 ]; then
      ( while [ -e "$WORK/run_seg" ]; do segment_once >/dev/null 2>&1; done ) &
      full_loops+=($!)
    fi
    LOOPS+=("${full_loops[@]}")
    burn 45; pings 90
    rm -f "$WORK/run_cam" "$WORK/run_seg"
    # Let the last capture/segmentation finish here, not in the cool-down samples.
    wait "${full_loops[@]}" 2>/dev/null
  fi

  echo cool > "$PHASE"; sleep 10

  if [ $MODEM -eq 1 ]; then
    echo "wwan0 data used: rx $(( $(cat /sys/class/net/wwan0/statistics/rx_bytes) - rx0 )) B," \
         "tx $(( $(cat /sys/class/net/wwan0/statistics/tx_bytes) - tx0 )) B"
  fi
  echo "samples: $OUT"
  python3 "$REPO/tools/power_test_report.py" "$OUT"
}

cleanup() {
  exec 2>/dev/null   # the script is ending; don't let bash announce the killed sampler
  rm -f "$WORK"/run_*
  for pid in "${LOOPS[@]}"; do
    pkill -P "$pid" 2>/dev/null   # children (rpicam-still, python3) first, so none is orphaned
    kill "$pid" 2>/dev/null
  done
  wait 2>/dev/null
  rm -rf "$WORK"
}

sampler() {
  while :; do
    echo "$(date +%s.%N | cut -c1-14),$(cat "$PHASE"),$(vcgencmd get_throttled | cut -d= -f2),$(vcgencmd measure_clock arm | cut -d= -f2),$(reading 1),$(reading 3),$(reading 5),$(vcgencmd measure_temp | grep -o '[0-9.]*')" >> "$OUT"
    sleep 0.3
  done
}

segment_once() {
  python3 - "$TIFF" "$SOCK" "$WORK/mask.png" <<'PY'
import json, socket, sys, time
s = socket.socket(socket.AF_UNIX)
s.connect(sys.argv[2]); s.settimeout(120)
t = time.time()
s.sendall((json.dumps({"tiff_path": sys.argv[1], "output_path": sys.argv[3]}) + "\n").encode())
s.shutdown(socket.SHUT_WR)
data = b""
while chunk := s.recv(4096):
    data += chunk
print("  segmentation:", data.decode().strip(), f"round trip {(time.time() - t) * 1000:.0f} ms")
PY
}

burn() { for _ in 1 2 3 4; do timeout "$1" sh -c 'while :; do :; done' & LOOPS+=($!); done; }
pings() { ping -I wwan0 -s 8 -i 0.5 -c "$1" -q 1.1.1.1 2>&1 | grep -E 'transmitted|unreachable|unknown' | sed 's/^/  ping: /'; }

# Run only when executed, so tests can source the helpers above.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
