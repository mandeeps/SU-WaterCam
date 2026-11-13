#!/bin/bash
# One real ticktalk wake with the shutdown intercepted, then everything restored.
#
#   sudo tools/held_capture.sh [--ip] OUT_DIR
#
# The capture workflow ends a wake by powering the Pi off (call_shutdown runs
# `doas /usr/sbin/shutdown -h now`). For a test wake at the bench or at
# installation (tools/field_setup.py, step "First capture"), a stand-in doas on
# ticktalk's PATH logs the call and exits 0 instead, so the unit stays up. The
# drop-in, the stand-in and runtime_config.json are put back afterwards,
# whatever happens (trap). Results in OUT_DIR: runner.log, wake.journal,
# lora_daemon.journal, doas.log, captures.txt, lora_last_sent.before/.after,
# and result.json (written last: its presence means the run is over).
#
# Each run starts from iteration_count 0 (in the test's runtime_config.json),
# so the wake takes its usual shutdown_iteration_limit captures. The count only
# starts over at a new boot, so without this a second held wake in the same boot
# (a bench test, or field_setup run again) stopped after its first capture.
#
# --ip tests the IP path: lora_daemon is stopped for the wake (and its link
# health cache cleared), so LoRa is unavailable and the wake sends over IP, with
# ip_upload.enabled on in the test's config. lora_daemon is started again
# afterwards. The run counts as failed if no IP upload went through.
#
# Run as root (systemd drop-ins); ticktalk itself still runs as its service user.
set -u
MODE=lora
OUT=
for arg in "$@"; do
  case $arg in
    --ip) MODE=ip ;;
    -*) echo "usage: held_capture.sh [--ip] OUT_DIR" >&2; exit 2 ;;
    *) OUT=$arg ;;
  esac
done
[ -n "$OUT" ] || { echo "usage: held_capture.sh [--ip] OUT_DIR" >&2; exit 2; }
REPO=${WATERCAM_REPO:-/home/pi/SU-WaterCam}
CFG=$REPO/runtime_config.json
# Overridable for the tests only
TEST=${HELD_CAPTURE_RUN_DIR:-/run/ticktalk-test}
DROPIN=${HELD_CAPTURE_DROPIN:-/etc/systemd/system/ticktalk.service.d/zz-no-shutdown-test.conf}
LORA_HEALTH=${HELD_CAPTURE_LORA_HEALTH:-/tmp/watercam-lora-health.json}
LORA_STOPPED=0
# A wake ends by asking for the power-off (call_shutdown, after its captures:
# about 2 min in the field, longer with a bench schedule); this is only the cap
MAX_S=${HELD_CAPTURE_MAX_S:-900}

mkdir -p "$OUT"
exec >>"$OUT/runner.log" 2>&1
log() { echo "$(date +%T) $*"; }
STATUS=error
REASON="did not finish"

finish() {
  systemctl stop ticktalk 2>/dev/null
  [ -f "$OUT/runtime_config.json.orig" ] && { cp "$OUT/runtime_config.json.orig" "$CFG"; chown pi:pi "$CFG"; }
  rm -f "$DROPIN"
  systemctl daemon-reload
  systemctl reset-failed ticktalk 2>/dev/null
  [ -f "$TEST/doas.log" ] && cp "$TEST/doas.log" "$OUT/"
  rm -rf "$TEST"
  if [ "$LORA_STOPPED" = 1 ]; then
    rm -f "$LORA_HEALTH"        # "daemon not running" isn't cached, but be sure
    systemctl start lora_daemon && log "lora_daemon started again"
  fi
  log "restored"
  printf '{"status": "%s", "reason": "%s", "mode": "%s", "finished": "%s"}\n' \
    "$STATUS" "$REASON" "$MODE" "$(date -Is)" > "$OUT/result.json.tmp"
  mv "$OUT/result.json.tmp" "$OUT/result.json"
  chown -R pi:pi "$OUT"
}
trap finish EXIT

if systemctl is-active -q ticktalk; then
  REASON="ticktalk is already running"; log "$REASON"; exit 1
fi
cp "$CFG" "$OUT/runtime_config.json.orig"
cp "$REPO/data/lora_last_sent.json" "$OUT/lora_last_sent.before" 2>/dev/null
ls -d "$REPO"/images/2* 2>/dev/null | sort > "$OUT/images.before"

# The test's settings, written in place (owner and mode kept); the original
# comes back in finish()
if ! python3 - "$CFG" "$MODE" <<'PY'
import json, sys
path, mode = sys.argv[1], sys.argv[2]
with open(path, "r+") as f:
    cfg = json.load(f)
    cfg["iteration_count"] = 0
    if mode == "ip":
        cfg.setdefault("ip_upload", {})["enabled"] = True
    f.seek(0)
    json.dump(cfg, f, indent=2)
    f.write("\n")
    f.truncate()
PY
then
  REASON="could not write the test settings to runtime_config.json"; log "$REASON"; exit 1
fi
if [ "$MODE" = ip ]; then
  log "test settings: iteration_count 0, ip_upload on"
else
  log "test settings: iteration_count 0"
fi

if [ "$MODE" = ip ]; then
  if systemctl is-active -q lora_daemon; then
    systemctl stop lora_daemon && LORA_STOPPED=1
  fi
  rm -f "$LORA_HEALTH"
  log "LoRa off for this wake (lora_daemon stopped)"
fi

mkdir -p "$TEST/bin"
cat > "$TEST/bin/doas" <<EOF
#!/bin/sh
echo "\$(date +%T) TEST-DOAS intercepted: \$*" >> "$TEST/doas.log"
exit 0
EOF
chmod 755 "$TEST/bin/doas"; chmod 777 "$TEST"
mkdir -p "$(dirname "$DROPIN")"
cat > "$DROPIN" <<EOF
[Service]
Environment=PATH=$TEST/bin:$REPO/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Restart=no
EOF
systemctl daemon-reload

# The stand-in must be what ticktalk's doas resolves to, or the wake powers off
systemd-run --wait -q -p User=pi -p "Environment=PATH=$TEST/bin:/usr/bin:/bin" \
  "$REPO/venv/bin/python" -c 'from subprocess import call; call(["doas", "/bin/true", "probe"])'
if ! grep -q probe "$TEST/doas.log" 2>/dev/null; then
  REASON="the shutdown stand-in did not work, so no wake was run"; log "$REASON"; exit 1
fi
log "doas probe ok"

t0=$(date '+%Y-%m-%d %H:%M:%S'); log "ticktalk start"
systemctl start --no-block ticktalk
sleep "${HELD_CAPTURE_SETTLE_S:-5}"
end=$(( $(date +%s) + MAX_S ))
ended() { grep -q "shutdown" "$TEST/doas.log" 2>/dev/null; }
while systemctl is-active -q ticktalk && ! ended && [ "$(date +%s)" -lt "$end" ]; do sleep 1; done
if ended; then
  # The wake asked for its power-off: its normal end. Let it finish writing.
  log "shutdown requested (intercepted)"
  for _ in $(seq 10); do systemctl is-active -q ticktalk || break; sleep 1; done
  systemctl stop ticktalk
  STATUS=done; REASON=""
elif systemctl is-active -q ticktalk; then
  log "still running after $MAX_S s, stopping"; systemctl stop ticktalk
  REASON="the wake was still running after $MAX_S s and was stopped"
else
  STATUS=done; REASON=""
fi
log "ticktalk ended"
journalctl -u ticktalk --since "$t0" --no-pager -o short-unix > "$OUT/wake.journal"
journalctl -u lora_daemon --since "$t0" --no-pager -o short-unix > "$OUT/lora_daemon.journal"
cp "$REPO/data/lora_last_sent.json" "$OUT/lora_last_sent.after" 2>/dev/null
ls -d "$REPO"/images/2* 2>/dev/null | sort > "$OUT/images.after"
comm -13 "$OUT/images.before" "$OUT/images.after" > "$OUT/captures.txt"
if [ "$MODE" = ip ] && [ "$STATUS" = done ]; then
  n=$(grep -c "IP uplink OK" "$OUT/wake.journal")
  log "IP uploads delivered: $n"
  if [ "$n" -eq 0 ]; then
    STATUS=error; REASON="no IP upload was delivered with LoRa off"
  fi
fi
