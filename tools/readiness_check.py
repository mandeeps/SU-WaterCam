#!/home/pi/SU-WaterCam/venv/bin/python
"""Is this unit ready to deploy? One read-only report of what it actually runs.

Notes about a unit drift from the unit: one was recorded as having unflashed mDot
firmware when it ran a build that reset-loops, and its IMU had never been calibrated.
Each surprise found on deployment day costs a probe and a decision while the light
goes. This reads the facts from the unit itself and says PASS, WARN or FAIL for each,
with what to do, so a red item becomes a decision days ahead:

    tools/readiness_check.py                          # bench stage: before the unit leaves
    tools/readiness_check.py --stage deployed         # after field_setup's finish step
    tools/readiness_check.py --transport cellular     # the site has no LoRa gateway
    tools/readiness_check.py --json                   # for a script or an agent
    tools/readiness_check.py --accept mdot_firmware   # a FAIL decided and accepted

Checks: release and pickle, system files (tools/install_system.py, if present),
services, deployment switches (hold, ticktalk, recovery), runtime_config.json
(IP upload, emergency, WiFi policy), the transport the site will use, mDot firmware,
watchdog and silence restart (through lora_daemon, read-only), IMU calibration and
declination, camera calibration, segmentation models, the IR-cut filter's last
verdict, the Witty Pi schedule, power flags and voltage, cellular data used, disk
space and the clock against the server.

Nothing is changed: no AT command that sets anything, no ATZ, no service started or
stopped. The one network request is the server's /health (for the clock); --offline
skips it. Run it with sudo to include the system-file comparison of root-only files.

Exit codes: 0 no FAIL, 1 at least one FAIL (with --strict, a WARN too).
"""

import os as _os, sys as _sys  # noqa: E401
_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO not in _sys.path:
    _sys.path.insert(0, _REPO)

import argparse
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

REPO = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
#: Prefix for absolute node paths outside the repo; tests point it at a temporary directory.
ROOT = os.environ.get("WATERCAM_READINESS_ROOT", "/")
WITTYPI = "/home/pi/wittypi"
try:
    from tools.field_setup_steps import HOLD_FLAG          # one definition with field_setup
except Exception:  # noqa: BLE001 - an older release: the same path
    HOLD_FLAG = "/etc/watercam/hold-awake"
MODEL_RGB = "/home/pi/segformer_type_pool/type_pool_b0_dyn_meta.onnx"
#: mDot builds with the 20 s reset loop after any software reset (mDot-AT-firmware #30).
MDOT_BANNED = re.compile(r"-wc\d+-sw[3-7]\b")
NIR_MAX_AGE_DAYS = 14       # an older "ok" doesn't show the filter still moves
SIM_BYTES = 500e6           # 1NCE one-time SIM (docs/CELLULAR_DATA.md)
STAGES = ("bench", "deployed")
TRANSPORTS = ("auto", "lora", "cellular", "wifi")


@dataclass
class Check:
    name: str
    status: str          # pass | warn | fail | info
    detail: str
    fix: str = ""


class Runner:
    def __call__(self, argv: List[str], timeout: float = 30) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return subprocess.CompletedProcess(argv, 127, "", str(e))


def node_path(path: str) -> str:
    return os.path.join(ROOT, path.lstrip("/"))


def load_json(path: str) -> Optional[Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Context:
    def __init__(self, stage: str = "bench", transport: str = "auto", offline: bool = False,
                 run: Optional[Runner] = None, daemon: Optional[Callable[[str], Any]] = None,
                 repo: Optional[str] = None):
        self.stage, self.transport, self.offline = stage, transport, offline
        self.run = run or Runner()
        self.repo = repo or REPO
        self._daemon = daemon
        self._mdot: Optional[Dict[str, Any]] = None
        self.config = load_json(os.path.join(self.repo, "runtime_config.json")) or {}

    @property
    def deployed(self) -> bool:
        return self.stage == "deployed"

    def unit_enabled(self, unit: str) -> str:
        return (self.run(["systemctl", "is-enabled", unit]).stdout or "").strip() or "not-found"

    def unit_active(self, unit: str) -> str:
        return (self.run(["systemctl", "is-active", unit]).stdout or "").strip() or "unknown"

    def daemon(self, action: str) -> Any:
        """One read-only lora_daemon request; only mdot_info is used (it includes AT+NJS?)."""
        if self._daemon:
            return self._daemon(action)
        from tools.lora_handler_concurrent import LoRaHandlerClient
        if action != "mdot_info":
            raise ValueError(action)
        return LoRaHandlerClient().mdot_info()      # never raises: {"error": ...} on failure

    def mdot(self) -> Dict[str, Any]:
        """The mDot's answers, read once per report (the serial port is shared with the app)."""
        if self._mdot is None:
            try:
                info = self.daemon("mdot_info")
            except Exception as e:  # noqa: BLE001
                info = {"error": str(e)}
            self._mdot = info if isinstance(info, dict) else {"error": "no answer"}
        return self._mdot

    def ip(self, key: str, default=None):
        sec = self.config.get("ip_upload")
        return sec.get(key, default) if isinstance(sec, dict) else default


def must(ctx: Context) -> str:
    """FAIL at the deployed stage, WARN on the bench (it can still be fixed before it leaves)."""
    return "fail" if ctx.deployed else "warn"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_release(ctx: Context) -> List[Check]:
    out = []
    from tools import node_version
    v = node_version.software_version(use_cache=False)
    if not v:
        return [Check("release", "fail", "git can't say what this checkout is", "see docs/RELEASES.md")]
    rel = v.get("release")
    tag, flags = ("v%d.%d.%d" % tuple(rel)) if rel else None, []
    if v.get("dirty"):
        flags.append("local edits")
    if v.get("ahead"):
        flags.append(f"{v['ahead']} commits past the tag")
    if not tag:
        out.append(Check("release", "fail", f"not on a release (commit {v.get('commit')})",
                         "sudo tools/install_system.py --release <tag> --apply (over WiFi)"))
    else:
        out.append(Check("release", "warn" if flags else "pass", f"{tag}" + (f" ({', '.join(flags)})" if flags else ""),
                         "check out the release tag cleanly" if flags else ""))
    pickle = os.path.join(ctx.repo, "output", "ticktalk_main.pickle")
    try:
        have = hashlib.sha256(open(pickle, "rb").read()).hexdigest()
        want = open(pickle + ".sha256").read().split()[0]
        out.append(Check("pickle", "pass" if have == want else "fail",
                         "matches its .sha256" if have == want else "does NOT match output/ticktalk_main.pickle.sha256",
                         "" if have == want else "check out the release again; never run a mismatched pickle"))
    except (OSError, IndexError):
        out.append(Check("pickle", "fail", "output/ticktalk_main.pickle or its .sha256 missing"))
    return out


def check_system_files(ctx: Context) -> List[Check]:
    try:
        from tools import install_system
    except ImportError:
        return [Check("system files", "info", "tools/install_system.py not in this release: not compared")]
    inst = install_system.Installer(repo=ctx.repo, run=ctx.run)
    try:
        inst.plan()
    except Exception as e:  # noqa: BLE001
        return [Check("system files", "warn", f"could not compare: {e}", "run with sudo")]
    pending = [a.target for a in inst.actions if a.pending]
    unreadable = [a.target for a in inst.actions if "unreadable" in a.detail]
    warns = [Check("system files: " + os.path.basename(a.target.rstrip("/")) if "/" in a.target else
                   "system files: " + a.target, "warn", a.detail, "see tools/install_system.py")
             for a in inst.actions if a.kind == "warn"]
    if not pending:
        return [Check("system files", "pass", "all match the release")] + warns
    status = "warn" if unreadable and len(unreadable) == len(pending) else "fail"
    return [Check("system files", status, f"{len(pending)} differ: " + ", ".join(os.path.basename(p) for p in pending[:6])
                  + (" ..." if len(pending) > 6 else ""),
                  "tools/install_system.py (dry run), then sudo tools/install_system.py --apply")] + warns


def check_services(ctx: Context) -> List[Check]:
    out = []
    for unit in ("lora_daemon.service", "segformer_daemon.service"):
        state = ctx.unit_active(unit)
        out.append(Check(unit, "pass" if state == "active" else "fail", state, f"sudo systemctl start {unit}"))
    for unit in ("max-awake.timer", "watercam-firewall.service", "rtc-early-clock.service",
                 "wittypi-next-wake.service", "remote-debug.path"):
        state = ctx.unit_enabled(unit)
        ok = state in ("enabled", "static")
        out.append(Check(unit, "pass" if ok else "fail", state, "" if ok else "sudo tools/install_system.py --apply"))
    return out


def check_deployment_switches(ctx: Context) -> List[Check]:
    out = []
    held = ctx.config.get("hold_awake") is True or os.path.exists(node_path(HOLD_FLAG))
    tick = ctx.unit_enabled("ticktalk.service")
    rec = ctx.unit_enabled("wittypi-recovery.service")
    if ctx.deployed:
        out.append(Check("hold awake", "fail" if held else "pass", "set" if held else "off",
                         "field_setup.py --step finish" if held else ""))
        out.append(Check("ticktalk", "pass" if tick == "enabled" else "fail", tick, "field_setup.py --step finish"))
        out.append(Check("wittypi-recovery", "pass" if rec == "enabled" else "warn", rec,
                         "" if rec == "enabled" else "enable it unless the unit is on mains power"))
    else:
        out.append(Check("hold awake", "info", f"{'set' if held else 'off'}; ticktalk {tick}, wittypi-recovery {rec} "
                         "(field_setup.py's finish step sets these at the site)"))
        if not held and tick != "enabled":
            out.append(Check("bench hold", "warn", "ticktalk disabled without hold_awake: max-awake.timer powers the "
                             "unit off once it has been up max_awake_minutes", "set hold_awake while it is on the bench"))
    return out


def check_config(ctx: Context) -> List[Check]:
    c = ctx.config
    if not c:
        return [Check("runtime_config.json", "fail", "missing or unreadable")]
    out = []
    if ctx.ip("enabled"):
        missing = [k for k in ("server_url", "api_key", "device_id") if not ctx.ip(k)]
        url = str(ctx.ip("server_url", ""))
        if "localhost" in url:
            missing.append("server_url is localhost")
        out.append(Check("IP upload", "fail" if missing else "pass",
                         f"enabled, device {ctx.ip('device_id')}" + (f"; missing: {', '.join(missing)}" if missing else ""),
                         "tools/set_ip_token.py" if "api_key" in missing else ""))
    else:
        need = ctx.transport in ("cellular", "wifi")
        out.append(Check("IP upload", "fail" if need else "info", "disabled",
                         "set ip_upload.enabled true" if need else ""))
    if c.get("emergency_mode"):
        out.append(Check("emergency mode", must(ctx), "ON (10-minute wakes: battery and data)", "turn it off"))
    if c.get("wifi_when_lora_healthy"):
        out.append(Check("wifi_when_lora_healthy", must(ctx), "true (a bench setting)", "set it false before it leaves"))
    if c.get("max_awake_minutes", 60) == 0:
        out.append(Check("max_awake_minutes", must(ctx), "0: no awake backstop", "set 60"))
    rb = c.get("recovery_boot")
    if isinstance(rb, dict) and rb.get("enabled") is False:
        out.append(Check("recovery_boot", must(ctx), "disabled in runtime_config.json", "a bench setting: re-enable"))
    if c.get("debug_mode"):
        out.append(Check("debug_mode", "info", "on (more journal output)"))
    return out


def nm_get(ctx: Context, fields: str, conn: str) -> Optional[List[str]]:
    r = ctx.run(["nmcli", "-g", fields, "connection", "show", conn])
    return r.stdout.strip().splitlines() if r.returncode == 0 else None


def check_transport(ctx: Context) -> List[Check]:
    out = []
    t = ctx.transport
    conn = ctx.ip("cellular_connection", "Quectel")
    cell = nm_get(ctx, "connection.autoconnect,gsm.apn", conn)
    if cell is None:
        out.append(Check("cellular profile", "fail" if t == "cellular" else "info", f"no NetworkManager connection {conn!r}",
                         "create it (guide 1.5.1)" if t == "cellular" else ""))
    else:
        auto, apn = (cell + ["", ""])[:2]
        bad = []
        if auto != "no":
            bad.append(f"autoconnect {auto} (must be no: cellular is on demand)")
        if not apn:
            bad.append("no APN")
        out.append(Check("cellular profile", "fail" if bad else "pass", "; ".join(bad) or f"{conn}: on demand, APN {apn}",
                         f"sudo nmcli connection modify {conn} connection.autoconnect no" if auto != "no" else ""))
    if t == "cellular":
        out.append(Check("cellular timeout", "info", f"connect timeout {ctx.ip('cellular_connect_timeout_s', 90)} s; "
                         "a cold modem must register within the wake (test one cold wake on the bench)"))
    if t in ("cellular", "wifi") and ctx.ip("only_if_lora_unavailable", True):
        out.append(Check("IP when LoRa is up", "info", "IP uploads only when LoRa is unavailable (default): with no "
                         "gateway at the site, every wake uses IP"))
    usage = load_json(os.path.join(ctx.repo, "data", "cellular_usage.json")) or {}
    if "total_bytes" in usage:
        used = int(usage["total_bytes"])
        st = "fail" if used > 0.9 * SIM_BYTES else "warn" if used > 0.7 * SIM_BYTES else "pass"
        out.append(Check("SIM data", st, f"{used / 1e6:.1f} MB counted since {usage.get('since', '?')} "
                         f"(of a {SIM_BYTES / 1e6:.0f} MB SIM)", "check the SIM balance with the carrier" if st != "pass" else ""))
    r = ctx.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    wifi = [l.split(":")[0] for l in (r.stdout or "").splitlines() if l.endswith(":802-11-wireless")]
    if t == "wifi" and not wifi:
        out.append(Check("WiFi profiles", "fail", "none saved", "add the site's network (field_setup connectivity step)"))
    else:
        out.append(Check("WiFi profiles", "info", f"{len(wifi)} saved; a hotspot with the same SSID as a saved profile "
                         "but different security won't join"))
    if t == "lora" or t == "auto":
        info = ctx.mdot() if ctx.unit_active("lora_daemon.service") == "active" else {"error": "lora_daemon not running"}
        njs = (info.get("njs") or "").strip()
        if njs in ("0", "1"):
            joined = njs == "1"
            out.append(Check("LoRa session", "pass" if joined else ("fail" if t == "lora" else "info"),
                             "joined (saved session)" if joined else "not joined (no saved session)",
                             "join near a gateway before it leaves" if t == "lora" and not joined else ""))
        else:
            # can't tell: say why, rather than "not joined" (the fix is different)
            out.append(Check("LoRa session", "fail" if t == "lora" else "info",
                             f"unknown: {info.get('error') or 'no AT+NJS? answer'}",
                             "sudo systemctl restart lora_daemon, then re-run" if t == "lora" else ""))
    return out


def check_mdot(ctx: Context) -> List[Check]:
    if ctx.unit_active("lora_daemon.service") != "active":
        return [Check("mDot", "warn", "lora_daemon not running: mDot not read", "sudo systemctl start lora_daemon")]
    info = ctx.mdot()
    if all(info.get(k) is None for k in ("ati", "wdog")):
        err = info.get("error") or "no answer"
        # unread firmware could be a reset-loop build: not ready to leave
        return [Check("mDot", must(ctx), f"could not read the mDot: {err}",
                      "the daemon may predate mdot_info (restart it after updating)")]
    out = []
    ati = info.get("ati") or ""
    m = re.search(r"Firmware[:\s]+(\S+)", ati)
    fw = m.group(1) if m else (ati.splitlines()[0] if ati else "?")
    if MDOT_BANNED.search(fw):
        out.append(Check("mdot_firmware", "fail", f"{fw}: a build that reset-loops after a software reset "
                         "(never send ATZ to it)", "flash the current release (mDot-AT-firmware), or --accept mdot_firmware"))
    else:
        out.append(Check("mdot_firmware", "pass" if "-wc" in fw else "warn", fw,
                         "" if "-wc" in fw else "stock firmware: no Pi watchdog or remote start"))
    w = info.get("wdog")
    first = (w or "").split(",")[0].strip()
    if w is None:
        out.append(Check("mDot watchdog", must(ctx), "no answer to AT+WDOG? (timed out)", "re-run; check the serial link"))
    elif w == "ERROR":
        out.append(Check("mDot watchdog", "warn", "AT+WDOG not supported", "firmware without the Pi watchdog"))
    elif not first.isdigit():
        out.append(Check("mDot watchdog", must(ctx), f"unreadable reply {w!r}", "read AT+WDOG? by hand"))
    else:
        hours = int(first)
        if hours == 0:
            out.append(Check("mDot watchdog", must(ctx), f"off ({w})", "AT+WDOG=20 with lora_daemon stopped"))
        elif hours < 12:
            out.append(Check("mDot watchdog", "warn", f"{hours} h ({w}): a test value; it presses the button "
                             "within a normal overnight gap", "AT+WDOG=20"))
        else:
            out.append(Check("mDot watchdog", "pass", f"{hours} h (hours, hours without Pi power, presses: {w})"))
    s = info.get("silence")
    if s and s != "ERROR":
        out.append(Check("mDot silence restart", "pass" if not s.startswith("0") else "warn", s))
    if info.get("fsb") and info["fsb"] != "ERROR":
        out.append(Check("mDot sub-band", "pass" if info["fsb"].strip() == "1" else "warn", f"FSB {info['fsb']}",
                         "" if info["fsb"].strip() == "1" else "python3 tools/mdot_subband.py"))
    return out


def check_imu(ctx: Context) -> List[Check]:
    out = []
    configs = sorted(glob.glob(os.path.join(ctx.repo, "unit_config_*.json")))
    cal_path = os.path.join(ctx.repo, "bno055_calibration.json")
    if len(configs) != 1:
        out.append(Check("unit config", "fail", f"{len(configs)} unit_config_*.json files (need exactly 1)",
                         "create unit_config_<ID>.json (docs/IMU_CALIBRATION.md)"))
    else:
        uc = load_json(configs[0]) or {}
        name = uc.get("imu_calibration_file")
        if name:
            cal_path = name if os.path.isabs(name) else os.path.join(ctx.repo, name)
            cal_path = cal_path if not cal_path.startswith("/home/pi/SU-WaterCam/") else \
                os.path.join(ctx.repo, os.path.relpath(cal_path, "/home/pi/SU-WaterCam"))
        dec = uc.get("imu_magnetic_declination_deg")
        out.append(Check("magnetic declination", "pass" if dec not in (None, 0, 0.0) else "warn",
                         f"{dec} deg in {os.path.basename(configs[0])}",
                         "" if dec not in (None, 0, 0.0) else "set the site's declination (WMM) before calibrating"))
    cal = load_json(cal_path)
    if cal is None:
        out.append(Check("IMU calibration", must(ctx), f"{os.path.basename(cal_path)} missing",
                         "tools/bno055_calibration.py -u unit_config_<ID>.json (gyro+accel indoors, mag at the site)"))
        return out
    comps = cal.get("components")
    if not isinstance(comps, dict):
        out.append(Check("IMU calibration", "warn", f"old format (no components), saved {cal.get('timestamp', '?')}",
                         "recalibrate: tools/bno055_calibration.py"))
    else:
        missing = [p for p in ("gyro", "accel", "mag") if not comps.get(p)]
        st = "pass" if not missing else ("warn" if missing == ["mag"] and not ctx.deployed else must(ctx))
        out.append(Check("IMU calibration", st, "gyro, accel, mag saved" if not missing else
                         f"missing {', '.join(missing)}", "" if not missing else
                         "mag at the site before mounting (field_setup magnetometer step)" if missing == ["mag"]
                         else "gyro+accel on the bench (docs/IMU_CALIBRATION.md)"))
    if "PLACEHOLDER" in str(cal.get("node_id", "")).upper():
        out.append(Check("IMU calibration source", "warn", "a placeholder copied from another unit: headings unreliable",
                         "calibrate this unit"))
    return out


def check_camera_and_models(ctx: Context) -> List[Check]:
    out = []
    cam = os.path.join(ctx.repo, "config", "camera_calibration.json")
    out.append(Check("camera calibration", "pass" if os.path.exists(cam) else "fail",
                     "present" if os.path.exists(cam) else "config/camera_calibration.json missing"))
    from tools import node_version
    info = node_version.model_info()
    if info:
        out.append(Check("segmentation model", "pass", f"{info.get('name')} loaded (sha256 {info['sha256'][:8]}, "
                         f"{info.get('bands', '?')} bands)"))
    else:
        out.append(Check("segmentation model", "fail", "segformer_daemon reports no loaded model",
                         "check the model path in segformer_daemon.service and restart it"))
    ok = os.path.exists(node_path(MODEL_RGB))
    out.append(Check("RGB fallback model", "pass" if ok else "warn", os.path.basename(MODEL_RGB) if ok else
                     f"{MODEL_RGB} missing: a stuck IR-cut filter would leave no segmentation",
                     "" if ok else "copy type_pool_b0_dyn_meta.onnx from a release's model package"))
    return out


def latest_capture_status(repo: str) -> Optional[Dict[str, Any]]:
    files = glob.glob(os.path.join(repo, "images", "*", "capture_status.json"))
    files += glob.glob(os.path.join(repo, "images", "*", "*", "capture_status.json"))
    for f in sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)[:20]:
        data = load_json(f)
        if isinstance(data, dict) and data.get("nir_filter"):
            data["_when"] = datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
            data["_age_days"] = (time.time() - os.path.getmtime(f)) / 86400
            return data
    return None


def check_nir(ctx: Context) -> List[Check]:
    st = latest_capture_status(ctx.repo)
    if not st:
        return [Check("IR-cut filter", "warn", "no lit capture has judged it yet",
                      "take a daylight capture and look at capture_status.json")]
    ok = st["nir_filter"] == "ok"
    if not ok:
        return [Check("IR-cut filter", "fail", f"{st['nir_filter']} (capture {st['_when']})",
                      "filter not moving: NIR band empty, flood flags off (docs/KNOWN_ISSUES.md)")]
    if st["_age_days"] > NIR_MAX_AGE_DAYS:
        return [Check("IR-cut filter", "warn", f"ok, but the last judged capture is {st['_age_days']:.0f} days old "
                      f"({st['_when']})", "take a daylight capture and re-run")]
    return [Check("IR-cut filter", "pass", f"ok (capture {st['_when']})")]


def check_schedule(ctx: Context) -> List[Check]:
    sched = node_path(f"{WITTYPI}/schedule.wpi")
    deploy = node_path(f"{WITTYPI}/schedule.wpi.deploy")
    if os.path.exists(sched):
        return [Check("Witty Pi schedule", "pass", "schedule.wpi installed")]
    if ctx.deployed:
        return [Check("Witty Pi schedule", "fail", "no schedule.wpi: the unit won't wake again",
                      "field_setup.py --step finish (dashboard schedule)")]
    return [Check("Witty Pi schedule", "info" if os.path.exists(deploy) else "warn",
                  "none installed yet" + ("; schedule.wpi.deploy kept" if os.path.exists(deploy) else
                                          " and no schedule.wpi.deploy fallback"))]


def check_power(ctx: Context) -> List[Check]:
    out = []
    r = ctx.run(["vcgencmd", "get_throttled"])
    m = re.search(r"0x([0-9a-fA-F]+)", r.stdout or "")
    if m:
        v = int(m.group(1), 16)
        st = "fail" if v & 0x1 else "warn" if v & 0x10000 else "pass"
        out.append(Check("power flags", st, f"0x{v:x}" + (" under-voltage NOW" if v & 1 else
                                                         " under-voltage since boot" if v & 0x10000 else ""),
                         "check the battery and cable" if st != "pass" else ""))
    try:
        from tools import powerlog
        vout = float(powerlog.reading(powerlog.REG_VOUT) or "nan")
    except Exception:  # noqa: BLE001
        vout = float("nan")
    if vout == vout:
        out.append(Check("Witty Pi Vout", "pass" if vout >= 4.8 else "warn", f"{vout:.2f} V",
                         "" if vout >= 4.8 else "low supply: check the battery"))
    return out


def check_journal(ctx: Context) -> List[Check]:
    """The journal must be on the card, or a failed field wake can't be read back."""
    r = ctx.run(["systemd-analyze", "cat-config", "systemd/journald.conf"])
    storage = "auto"
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("Storage="):
            storage = line.split("=", 1)[1].strip()   # the last one read wins
    on_card = storage == "persistent" or (storage == "auto" and os.path.isdir("/var/log/journal"))
    if on_card:
        return [Check("journal", "pass", f"on the card (Storage={storage})")]
    return [Check("journal", "fail" if ctx.deployed else "warn",
                  f"in RAM (Storage={storage}): lost at each shutdown, so a failed wake can't be read back",
                  "sudo python3 tools/install_system.py --apply (installs journald-persistent.conf)")]


def check_disk_and_clock(ctx: Context) -> List[Check]:
    out = []
    du = shutil.disk_usage(ctx.repo)
    free = 100 * du.free / du.total
    out.append(Check("disk free", "pass" if free > 30 else "warn" if free > 20 else "fail", f"{free:.0f}%"))
    if not ctx.offline:
        try:
            from tools.field_setup_steps import clock_offset_s
            off = clock_offset_s()
        except Exception:  # noqa: BLE001
            off = None
        if off is None:
            out.append(Check("clock", "info", "server not reachable: not compared"))
        else:
            out.append(Check("clock", "pass" if abs(off) < 5 else "warn", f"{off:+.1f} s against the server"))
    return out


CHECKS = [check_release, check_system_files, check_services, check_deployment_switches, check_config,
          check_transport, check_mdot, check_imu, check_camera_and_models, check_nir, check_schedule,
          check_power, check_journal, check_disk_and_clock]


def run_checks(ctx: Context, accept=()) -> List[Check]:
    out: List[Check] = []
    for fn in CHECKS:
        try:
            out.extend(fn(ctx))
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the rest
            # a check that didn't run hasn't shown the unit is ready
            out.append(Check(fn.__name__.replace("check_", ""), must(ctx), f"check failed to run: {e}",
                             "report it; check this by hand"))
    for c in out:
        if c.status == "pass":
            c.fix = ""
        if c.name in accept and c.status in ("fail", "warn"):
            c.status, c.detail, c.fix = "info", f"ACCEPTED: {c.detail}", ""
    return out


def report(checks: List[Check], ctx: Context) -> str:
    mark = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "info": "info"}
    host = os.uname().nodename
    lines = [f"Readiness of {host}: stage {ctx.stage}, transport {ctx.transport}, "
             f"{time.strftime('%Y-%m-%d %H:%M %Z')}"]
    for c in checks:
        lines.append(f"[{mark[c.status]}] {c.name}: {c.detail}" + (f"\n         -> {c.fix}" if c.fix and c.status != "pass" else ""))
    n = {s: sum(c.status == s for c in checks) for s in mark}
    lines.append(f"{n['fail']} FAIL, {n['warn']} WARN, {n['pass']} PASS.")
    return "\n".join(lines)


def main(argv=None, ctx: Optional[Context] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--stage", choices=STAGES, default="bench")
    p.add_argument("--transport", choices=TRANSPORTS, default="auto", help="what the site will use")
    p.add_argument("--offline", action="store_true", help="no request to the server")
    p.add_argument("--accept", action="append", default=[], metavar="CHECK",
                   help="record a FAIL/WARN as decided (repeatable), e.g. mdot_firmware")
    p.add_argument("--strict", action="store_true", help="a WARN fails too")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    ctx = ctx or Context(stage=a.stage, transport=a.transport, offline=a.offline)
    checks = run_checks(ctx, accept=set(a.accept))
    unknown = sorted(set(a.accept) - {c.name for c in checks})
    if unknown:
        print(f"--accept: no check named {', '.join(unknown)}. Names: "
              + ", ".join(sorted({c.name for c in checks})), file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({"stage": a.stage, "transport": a.transport, "host": os.uname().nodename,
                          "checks": [asdict(c) for c in checks]}, indent=1))
    else:
        print(report(checks, ctx))
    bad = {"fail", "warn"} if a.strict else {"fail"}
    return 1 if any(c.status in bad for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
