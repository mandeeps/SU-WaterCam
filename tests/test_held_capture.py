"""tools/held_capture.sh with stand-ins for systemctl, systemd-run and journalctl:
each run starts from iteration_count 0, and --ip takes LoRa away for the wake."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "held_capture.sh"

# systemctl: services' states in $STATE/<name>; "start ticktalk" plays a wake
# (one capture, the config and lora_daemon's state as the wake saw them, the
# journal, and the shutdown through the stand-in doas).
SYSTEMCTL = r"""#!/bin/bash
echo "systemctl $*" >> "$STATE/calls.log"
case "$1" in
  is-active) svc=${@: -1}; [ "$(cat "$STATE/$svc" 2>/dev/null)" = active ]; exit $? ;;
  stop) echo inactive > "$STATE/${@: -1}" ;;
  start)
    svc=${@: -1}
    if [ "$svc" = ticktalk ]; then
      cp "$WATERCAM_REPO/runtime_config.json" "$STATE/wake_config.json"
      cat "$STATE/lora_daemon" > "$STATE/wake_lora_daemon" 2>/dev/null
      mkdir -p "$WATERCAM_REPO/images/20261112-213015"
      printf '%s\n' "$WAKE_JOURNAL" > "$STATE/journal"
      # A real wake writes its schedule and arms the next start-up, as on 005
      echo "BEGIN 2026-11-13 08:00:00 (written by the wake)" > "$HELD_CAPTURE_WITTYPI_DIR/schedule.wpi"
      echo "14 08:00:00" > "$HELD_CAPTURE_WITTYPI_DIR/.startup"
      PATH="$HELD_CAPTURE_RUN_DIR/bin:$PATH" doas /usr/sbin/shutdown -h now
    else
      echo active > "$STATE/$svc"
    fi ;;
esac
exit 0
"""
SYSTEMD_RUN = r"""#!/bin/bash
# systemd-run --wait -q -p User=pi -p Environment=PATH=... CMD...: run CMD with that PATH
path=$PATH
while [ $# -gt 0 ]; do
  case "$1" in
    -p) case "$2" in Environment=PATH=*) path=${2#Environment=PATH=} ;; esac; shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
PATH="$path" "$@"
"""
# The Witty Pi's utilities.sh: the alarms kept in files beside it
UTILITIES = r"""
get_startup_time() { cat ./.startup 2>/dev/null || echo "00 00:00:00"; }
get_shutdown_time() { cat ./.shutdown 2>/dev/null || echo "00 00:00:00"; }
set_startup_time() { printf '%02d %02d:%02d:%02d\n' "$1" "$2" "$3" "$4" > ./.startup; }
set_shutdown_time() { printf '%02d %02d:%02d:%02d\n' "$1" "$2" "$3" "$4" > ./.shutdown; }
clear_startup_time() { echo "00 00:00:00" > ./.startup; }
clear_shutdown_time() { echo "00 00:00:00" > ./.shutdown; }
"""
JOURNALCTL = r"""#!/bin/bash
case "$*" in *"-u ticktalk"*) cat "$STATE/journal" 2>/dev/null ;; esac
exit 0
"""


@pytest.fixture
def held(tmp_path):
    repo = tmp_path / "repo"
    (repo / "venv" / "bin").mkdir(parents=True)
    (repo / "venv" / "bin" / "python").symlink_to(sys.executable)
    (repo / "data").mkdir()
    (repo / "images").mkdir()
    state = tmp_path / "state"
    state.mkdir()
    wittypi = tmp_path / "wittypi"
    wittypi.mkdir()
    (wittypi / "utilities.sh").write_text(UTILITIES)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, text in (("systemctl", SYSTEMCTL), ("systemd-run", SYSTEMD_RUN), ("journalctl", JOURNALCTL)):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    config = {"iteration_count": 3, "shutdown_iteration_limit": 2,
              "ip_upload": {"enabled": False, "server_url": "https://x"}}

    def run(*args, lora="active", journal="", config=config):
        cfg = repo / "runtime_config.json"
        cfg.write_text(json.dumps(config))
        cfg.chmod(0o600)
        (state / "lora_daemon").write_text(lora)
        (state / "ticktalk").write_text("inactive")
        out = tmp_path / "out"
        env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "STATE": str(state),
               "WATERCAM_REPO": str(repo), "WAKE_JOURNAL": journal,
               "HELD_CAPTURE_RUN_DIR": str(tmp_path / "run"),
               "HELD_CAPTURE_DROPIN": str(tmp_path / "dropin" / "zz-test.conf"),
               "HELD_CAPTURE_LORA_HEALTH": str(tmp_path / "lora-health.json"),
               "HELD_CAPTURE_SETTLE_S": "0", "HELD_CAPTURE_WITTYPI_DIR": str(wittypi)}
        r = subprocess.run(["bash", str(SCRIPT), *args, str(out)], env=env, capture_output=True,
                           text=True, timeout=60)

        def read(p):
            return p.read_text() if p.exists() else ""
        return {"rc": r.returncode, "result": json.loads(read(out / "result.json") or "{}"),
                "log": read(out / "runner.log"), "calls": read(state / "calls.log"),
                "wake_config": json.loads(read(state / "wake_config.json") or "{}"),
                "wake_lora": read(state / "wake_lora_daemon").strip(), "lora_after": read(state / "lora_daemon").strip(),
                "config_after": json.loads(cfg.read_text()), "mode_after": cfg.stat().st_mode & 0o777,
                "captures": read(out / "captures.txt").split(), "doas": read(out / "doas.log"),
                "run_dir_left": (tmp_path / "run").exists(), "dropin_left": (tmp_path / "dropin" / "zz-test.conf").exists(),
                "health_cache": tmp_path / "lora-health.json"}
    run.state = state
    run.wittypi = wittypi
    run.out = tmp_path / "out"
    return run


def test_each_run_starts_from_iteration_zero_and_restores_the_config(held):
    r = held()
    assert r["result"]["status"] == "done" and r["result"]["mode"] == "lora"
    assert r["wake_config"]["iteration_count"] == 0           # was 3: one capture only, before
    assert r["wake_config"]["ip_upload"]["enabled"] is False  # untouched without --ip
    assert r["config_after"]["iteration_count"] == 3          # the original is back
    assert r["mode_after"] == 0o600                           # written in place: mode kept
    assert "probe" in r["doas"] and "shutdown -h now" in r["doas"]
    assert len(r["captures"]) == 1
    assert not r["run_dir_left"] and not r["dropin_left"]


def test_a_lora_run_leaves_lora_daemon_alone(held):
    r = held()
    assert "lora_daemon" not in r["calls"] and r["wake_lora"] == "active"


def test_ip_takes_lora_away_for_the_wake_and_gives_it_back(held):
    r = held("--ip", journal="✅ IP uplink OK: 12 channels sent")
    assert r["result"]["status"] == "done" and r["result"]["mode"] == "ip"
    assert r["wake_lora"] == "inactive"                       # LoRa off while the wake ran
    assert r["wake_config"]["ip_upload"]["enabled"] is True
    assert r["wake_config"]["iteration_count"] == 0
    assert r["lora_after"] == "active"                        # started again
    assert r["config_after"]["ip_upload"]["enabled"] is False
    assert "IP uploads delivered: 1" in r["log"]
    assert not r["health_cache"].exists()


def test_ip_clears_a_cached_healthy_link(held, tmp_path):
    # A healthy assessment from earlier in the boot would keep the wake on LoRa
    (tmp_path / "lora-health.json").write_text('{"result": {"healthy": true}}')
    r = held("--ip", journal="✅ IP uplink OK")
    assert not r["health_cache"].exists() and r["result"]["status"] == "done"


def test_ip_without_an_ip_upload_fails(held):
    r = held("--ip", journal="📡 IP uplink disabled")
    assert r["result"]["status"] == "error"
    assert "no IP upload" in r["result"]["reason"]
    assert r["lora_after"] == "active"


def test_ip_does_not_start_a_lora_daemon_that_was_off(held):
    r = held("--ip", lora="inactive", journal="✅ IP uplink OK")
    assert r["lora_after"] == "inactive" and "start lora_daemon" not in r["calls"]


def test_bad_arguments_and_an_unwritable_config(held):
    assert held("--lora")["rc"] == 2
    r = held(config=None)          # "null": not a settings object
    assert r["result"]["status"] == "error" and "test settings" in r["result"]["reason"]
    assert "start --no-block ticktalk" not in r["calls"]


# ── the Witty Pi schedule and alarms the wake set are put back ───────────────

def _wp(held, name):
    p = held.wittypi / name
    return p.read_text().strip() if p.exists() else None


def test_no_schedule_before_none_after_and_the_alarm_cleared(held):
    r = held()
    assert r["result"]["status"] == "done"
    assert not (held.wittypi / "schedule.wpi").exists()            # there was none: removed
    assert "written by the wake" in (held.out / "schedule.wpi.from-wake").read_text()
    assert _wp(held, ".startup") == "00 00:00:00"                   # the wake's 14 08:00:00 cleared
    assert "alarms set back: startup 00 00:00:00, shutdown 00 00:00:00" in r["log"]


def test_an_existing_schedule_and_alarms_are_put_back(held):
    (held.wittypi / "schedule.wpi").write_text("BEGIN the bench's own\n")
    (held.wittypi / ".startup").write_text("09 07:05:00\n")
    (held.wittypi / ".shutdown").write_text("09 07:15:30\n")
    r = held()
    assert (held.wittypi / "schedule.wpi").read_text() == "BEGIN the bench's own\n"
    assert "written by the wake" in (held.out / "schedule.wpi.from-wake").read_text()
    assert _wp(held, ".startup") == "09 07:05:00" and _wp(held, ".shutdown") == "09 07:15:30"


def test_keep_schedule_leaves_what_the_wake_set(held):
    r = held("--keep-schedule")
    assert r["result"]["status"] == "done"
    assert "written by the wake" in (held.wittypi / "schedule.wpi").read_text()
    assert _wp(held, ".startup") == "14 08:00:00"
    assert not (held.out / "schedule.wpi.from-wake").exists()


def test_unreadable_alarms_are_left_alone_but_the_schedule_is_put_back(held):
    (held.wittypi / "utilities.sh").write_text("get_startup_time() { echo garbage; }\n")
    r = held()
    assert "could not read the Witty Pi alarms" in r["log"]
    assert not (held.wittypi / "schedule.wpi").exists()
    assert "alarms set back" not in r["log"]
