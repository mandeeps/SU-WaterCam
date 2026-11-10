#!/bin/bash
# One real ticktalk wake with the shutdown intercepted, then everything restored.
#
#   sudo tools/held_capture.sh OUT_DIR
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
# Run as root (systemd drop-ins); ticktalk itself still runs as its service user.
set -u
OUT=${1:?usage: held_capture.sh OUT_DIR}
REPO=${WATERCAM_REPO:-/home/pi/SU-WaterCam}
CFG=$REPO/runtime_config.json
TEST=/run/ticktalk-test
DROPIN=/etc/systemd/system/ticktalk.service.d/zz-no-shutdown-test.conf
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
  log "restored"
  printf '{"status": "%s", "reason": "%s", "finished": "%s"}\n' "$STATUS" "$REASON" "$(date -Is)" > "$OUT/result.json.tmp"
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

mkdir -p "$TEST/bin"
cat > "$TEST/bin/doas" <<'EOF'
#!/bin/sh
echo "$(date +%T) TEST-DOAS intercepted: $*" >> /run/ticktalk-test/doas.log
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
sleep 5
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
