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

REPO=$(cd "$(dirname "$0")/.." && pwd)
SOCK=/run/segformer/segformer.sock
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

if [ -z "$TIFF" ]; then
  TIFF=$(ls -t "$REPO"/images/*/final_5_band.tif* 2>/dev/null | head -1)
fi
WORK=$(mktemp -d)
PHASE=$WORK/phase
LOOPS=()

cleanup() {
  rm -f "$WORK"/run_*
  for pid in "${LOOPS[@]}"; do kill "$pid" 2>/dev/null; done
  wait 2>/dev/null
  rm -rf "$WORK"
}
trap cleanup EXIT

wp() { printf "%d" "$(/usr/sbin/i2cget -y 1 0x08 "$1" 2>/dev/null)"; }
reading() { echo "$(wp "$1").$(printf %02d "$(wp $(($1 + 1)))")"; }

sampler() {
  echo "t,phase,throttled,arm_hz,vin,vout,iout,temp_c" > "$OUT"
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
[ $MODEM -eq 1 ] && rx0=$(cat /sys/class/net/wwan0/statistics/rx_bytes) tx0=$(cat /sys/class/net/wwan0/statistics/tx_bytes)

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
  ( while [ -e "$WORK/run_cam" ]; do rpicam-still -n -t 300 -o "$WORK/cam.jpg" >/dev/null 2>&1; done ) & LOOPS+=($!)
  if [ $CAN_SEGMENT -eq 1 ]; then
    ( while [ -e "$WORK/run_seg" ]; do segment_once >/dev/null 2>&1; done ) & LOOPS+=($!)
  fi
  burn 45; pings 90
  rm -f "$WORK/run_cam" "$WORK/run_seg"
fi

echo cool > "$PHASE"; sleep 10

if [ $MODEM -eq 1 ]; then
  echo "wwan0 data used: rx $(( $(cat /sys/class/net/wwan0/statistics/rx_bytes) - rx0 )) B," \
       "tx $(( $(cat /sys/class/net/wwan0/statistics/tx_bytes) - tx0 )) B"
fi
echo "samples: $OUT"
python3 "$REPO/tools/power_test_report.py" "$OUT"
