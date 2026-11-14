"""The steps of a field installation, for tools/field_setup.py.

Each step is a function step_<name>(ctx) -> StepResult. It talks to the person
only through ctx.ui (say, ask, yes_no, number, pause), runs commands through
ctx.run, and makes every change to the unit through ctx.change, which records
it (with what was there before) and, in a dry run, only says what it would do.
So the same functions can sit behind the planned setup web page
(docs/SETUP_MODE_DESIGN.md section 8): only the UI object changes.

The order is by what the person is doing with the unit (phases):
  A. before mounting, unit in hand near the site
  B. mounting
  C. at the final position
Everything that needs the box in hand (the power wiggle test, the magnetometer)
comes before it is fixed in place.

Text is plain ASCII: it must read on a serial console as well as over SSH.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

REPO = os.environ.get("WATERCAM_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
#: Where this file is: field_setup.py and held_capture.sh sit next to it (a copy
#: being tried out may live outside the checkout, with WATERCAM_REPO set)
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(REPO, "venv", "bin", "python")
CONFIG = os.path.join(REPO, "runtime_config.json")
CALIBRATION = os.path.join(REPO, "config", "camera_calibration.json")
HOLD_FLAG = "/etc/watercam/hold-awake"
WITTYPI_DIR = os.path.expanduser("~/wittypi")
WITTYPI_LOG = os.path.join(WITTYPI_DIR, "wittyPi.log")
POWERLOG = os.path.expanduser("~/powerlog/powerlog.csv")

#: Witty Pi output voltage a healthy supply holds (docs/POWER_ANALYSIS.md, #228)
MIN_VOUT = 4.7
#: LoRa link margin the node calls healthy (tools/lora_health.py)
MIN_LORA_MARGIN_DB = 5.0
#: The capture workflow's frame size (tools/take_photo.py); the camera
#: calibration is for this size, so the reference photo is taken at it
PHOTO_W, PHOTO_H = 2592, 1944
#: Variance of the Laplacian of the grey photo at a quarter size: under this the
#: photo is probably out of focus or fogged. Measured on 006's reference photo:
#: about 4000 sharp, 190 with a 4 px blur, 45 through a fogged window, 19 badly
#: out of focus. It only warns: a very dim scene can score low too.
MIN_SHARPNESS = 100.0
WIGGLE_S = 60
#: The background Wi-Fi test, worst case: leave the setup network (20 s), join the
#: site's (60 s), reach the server (40 s), then up to 3 tries back (3 x 65 s).
#: systemd stops the job only well after that, so it never cuts the way back.
WIFI_TEST_MAX_S = 330
WIFI_TEST_KILL_S = 600


class Abort(Exception):
    """The person quit (end of input, or 'q' at a prompt)."""


@dataclass
class StepResult:
    status: str                       # pass | warn | fail | skip
    message: str
    values: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Step:
    id: str
    phase: str
    title: str
    fn: Callable[["Context"], StepResult]


# ── the person ──────────────────────────────────────────────────────────────

class TerminalUI:
    """Plain numbered prompts on stdin/stdout (SSH or a serial console)."""

    def __init__(self, inp=input, out=None):
        # Flushed every line: over SSH stdout is a pipe, and a command run in
        # between (the health check) writes straight to it
        self._input, self._print = inp, out or (lambda text: print(text, flush=True))

    def say(self, text: str = "") -> None:
        self._print(text)

    def heading(self, text: str) -> None:
        self._print("")
        self._print(text)
        self._print("-" * min(len(text), 72))

    def _ask(self, prompt: str) -> str:
        try:
            answer = self._input(prompt + " ")
        except EOFError:
            raise Abort() from None
        answer = (answer or "").strip()
        if answer.lower() in ("q", "quit"):
            raise Abort()
        return answer

    def ask(self, prompt: str, default: str = "") -> str:
        a = self._ask(f"{prompt}" + (f" [{default}]" if default else ""))
        return a or default

    def yes_no(self, prompt: str, default: Optional[bool] = None) -> bool:
        hint = {True: "[Y/n]", False: "[y/N]", None: "[y/n]"}[default]
        while True:
            a = self._ask(f"{prompt} {hint}").lower()
            if not a and default is not None:
                return default
            if a in ("y", "yes"):
                return True
            if a in ("n", "no"):
                return False
            self.say("  Please answer y or n (or q to stop here).")

    def number(self, prompt: str, lo: float, hi: float, allow_blank: bool = False) -> Optional[float]:
        while True:
            a = self._ask(f"{prompt}" + (" (Enter to skip)" if allow_blank else ""))
            if not a and allow_blank:
                return None
            try:
                v = float(a)
            except ValueError:
                self.say(f"  Please type a number between {lo:g} and {hi:g}.")
                continue
            if lo <= v <= hi:
                return v
            self.say(f"  Please type a number between {lo:g} and {hi:g}.")

    def pause(self, prompt: str = "Press Enter to continue.") -> None:
        self._ask(prompt)


class ScriptedUI(TerminalUI):
    """Answers given in advance, output collected (tests, and --answers for rehearsals).

    answers: a list, taken in order; or a dict from a piece of the prompt's text
    to the answer, where a prompt that matches nothing gets Enter (its default),
    which doesn't depend on how many warnings a unit happens to give."""

    def __init__(self, answers):
        self.answers = answers if isinstance(answers, dict) else list(answers)
        self.output: List[str] = []
        self._seen: Dict[str, int] = {}
        super().__init__(inp=self._next, out=self.output.append)

    def _next(self, prompt: str) -> str:
        self.output.append(prompt)
        if isinstance(self.answers, dict):
            self._seen[prompt] = self._seen.get(prompt, 0) + 1
            if self._seen[prompt] > 5:            # asked again and again: no usable answer
                raise EOFError
            return next((a for key, a in self.answers.items() if key in prompt), "")
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


# ── the session record ──────────────────────────────────────────────────────

SESSIONS_DIR = os.path.expanduser(os.environ.get("FIELD_SETUP_DIR", "~/field_setup"))


class Session:
    """~/field_setup/<YYYYmmdd-HHMMSS>/session.json: progress, recorded values and
    every change, so a dropped connection or a Ctrl-C loses nothing."""

    def __init__(self, path: str, data: Optional[Dict[str, Any]] = None):
        self.path = path
        self.data = data or {"started": _now(), "finished": None, "dry_run": False,
                             "steps": {}, "values": {}, "changes": []}

    @classmethod
    def new(cls, dry_run: bool = False) -> "Session":
        name = time.strftime("%Y%m%d-%H%M%S") + ("-dry-run" if dry_run else "")
        path = os.path.join(SESSIONS_DIR, name)
        os.makedirs(path, exist_ok=True)
        s = cls(path)
        s.data["dry_run"] = dry_run
        s.save()
        return s

    @classmethod
    def load(cls, path: str) -> "Session":
        with open(os.path.join(path, "session.json")) as f:
            return cls(path, json.load(f))

    @classmethod
    def latest_unfinished(cls) -> Optional["Session"]:
        for d in sorted(glob.glob(os.path.join(SESSIONS_DIR, "2*")), reverse=True):
            try:
                s = cls.load(d)
            except (OSError, ValueError):
                continue
            if not s.data.get("finished") and not s.data.get("dry_run"):
                return s
        return None

    def save(self) -> None:
        tmp = os.path.join(self.path, "session.json.tmp")
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, default=str)
        os.replace(tmp, os.path.join(self.path, "session.json"))

    @property
    def values(self) -> Dict[str, Any]:
        return self.data["values"]

    def record(self, step_id: str, result: StepResult) -> None:
        self.data["steps"][step_id] = {"status": result.status, "message": result.message,
                                       "at": _now(), "boot_id": boot_id()}
        self.values.update(result.values)
        self.save()

    def log(self, text: str) -> None:
        with open(os.path.join(self.path, "setup.log"), "a") as f:
            f.write(f"{_now()} {text}\n")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


# ── running things ──────────────────────────────────────────────────────────

class Context:
    def __init__(self, ui: TerminalUI, session: Session, dry_run: bool = False,
                 upload: bool = True, runner: Optional[Callable] = None):
        self.ui, self.session, self.dry_run, self.upload = ui, session, dry_run, upload
        self._run = runner or (lambda *a, **k: subprocess.run(*a, **k))

    def run(self, argv: List[str], timeout: Optional[float] = 60, interactive: bool = False,
            cwd: str = REPO) -> subprocess.CompletedProcess:
        """Run a command that changes nothing. interactive: it talks to the person
        directly (inherits the terminal), its output isn't captured."""
        self.session.log("run: " + " ".join(argv))
        try:
            if interactive:
                sys.stdout.flush()
                return self._run(argv, cwd=cwd, timeout=timeout)
            return self._run(argv, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            return subprocess.CompletedProcess(argv, 124, out, "timed out")
        except OSError as e:
            return subprocess.CompletedProcess(argv, 127, "", str(e))

    def tool(self, name: str, *args: str, **kw) -> subprocess.CompletedProcess:
        return self.run([VENV_PY, os.path.join(REPO, "tools", name), *args], **kw)

    def py(self, code: str, timeout: Optional[float] = 120) -> subprocess.CompletedProcess:
        return self.run([VENV_PY, "-c", code], timeout=timeout)

    def change(self, what: str, argv: Optional[List[str]] = None, fn: Optional[Callable[[], Any]] = None,
               before: Any = None, timeout: Optional[float] = 120, interactive: bool = False) -> bool:
        """Make one change to the unit, recorded in the session; True if it worked.
        In a dry run, only say what would be done."""
        entry = {"at": _now(), "what": what, "before": before, "dry_run": self.dry_run}
        if self.dry_run:
            self.ui.say(f"  (dry run) would {what}")
            entry["ok"] = None
        else:
            ok = True
            detail = ""
            if argv is not None:
                r = self.run(argv, timeout=timeout, interactive=interactive)
                ok = r.returncode == 0
                detail = ((r.stderr or "") + (r.stdout or "")).strip()[-300:] if not interactive else ""
            if fn is not None and ok:
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - recorded and reported
                    ok, detail = False, str(e)
            entry["ok"] = ok
            if detail:
                entry["detail"] = detail
            self.ui.say(f"  {'done' if ok else 'FAILED'}: {what}" + (f" ({detail})" if not ok and detail else ""))
        self.session.data["changes"].append(entry)
        self.session.save()
        return bool(entry["ok"]) or self.dry_run

    def set_parameter(self, name: str, value: Any, before: Any = None) -> bool:
        code = ("from tools.lora_runtime_integration import set_parameter; "
                f"import sys; sys.exit(0 if set_parameter({name!r}, {value!r}) is not False else 1)")
        return self.change(f"set {name} = {value}", [VENV_PY, "-c", code], before=before)


# ── small readers ───────────────────────────────────────────────────────────

def read_config() -> Dict[str, Any]:
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clock_offset_s() -> Optional[float]:
    """Server time minus this clock, from the server's Date header (seconds), or None.

    Units don't run NTP. At boot the Witty Pi daemon sets the system time from
    its real-time clock, then SU-WaterCam's config/wittypi/syncTime.sh (run from
    beforeScript.sh) applies network time, or GPS time, and writes it back to
    the RTC; after an IP uplink transmit_ip.correct_clock fixes a clock that is
    still off. So compare with the server instead of asking NTP."""
    try:
        from email.utils import parsedate_to_datetime

        import requests
        from tools import transmit_ip
        url = transmit_ip.IPTransmitter().server_url
        if not url:
            return None
        t0 = time.time()
        r = requests.get(url.rstrip("/") + "/health", timeout=10)
        t1 = time.time()
        return parsedate_to_datetime(r.headers["Date"]).timestamp() - (t0 + t1) / 2
    except Exception:  # noqa: BLE001 - no server, no comparison
        return None


def throttled(ctx: Context) -> Optional[int]:
    r = ctx.run(["vcgencmd", "get_throttled"], timeout=10)
    m = re.search(r"0x([0-9a-fA-F]+)", r.stdout or "")
    return int(m.group(1), 16) if m else None


THROTTLE_BITS = {0: "under-voltage now", 1: "CPU speed capped now", 2: "throttled now",
                 3: "soft temperature limit now", 16: "under-voltage since boot",
                 17: "CPU speed capped since boot", 18: "throttled since boot",
                 19: "soft temperature limit since boot"}


def describe_throttled(value: Optional[int]) -> str:
    if value is None:
        return "could not read the throttle flags"
    if value == 0:
        return "no power or temperature problems (0x0)"
    return f"0x{value:x}: " + ", ".join(t for b, t in THROTTLE_BITS.items() if value & (1 << b))


def witty_vout(ctx: Context) -> Optional[float]:
    r = ctx.py("from tools import powerlog as p; print(p.reading(p.REG_VOUT))", timeout=20)
    try:
        return float((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def restarts_logged() -> int:
    try:
        with open(WITTYPI_LOG, "rb") as f:
            return f.read().count(b"System starts up")
    except OSError:
        return 0


def last_powerlog_vout() -> Optional[float]:
    try:
        with open(POWERLOG) as f:
            lines = f.read().strip().splitlines()
        header = lines[0].split(",")
        return float(lines[-1].split(",")[header.index("vout")])
    except (OSError, ValueError, IndexError):
        return None


def unit_config_args() -> List[str]:
    """-u unit_config_*.json for bno055_calibration.py, if the unit has exactly one."""
    found = sorted(glob.glob(os.path.join(REPO, "unit_config_*.json")))
    return ["-u", found[0]] if len(found) == 1 else []


def lora_assess(ctx: Context) -> Dict[str, Any]:
    r = ctx.py("import json; from tools import lora_health as h; print(json.dumps(h.assess(force=True)))",
               timeout=120)
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"healthy": False, "reason": (r.stderr or r.stdout or "no answer").strip()[-200:]}


def nm(ctx: Context, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return ctx.run(["nmcli", *args], timeout=timeout)


def wifi_profiles(ctx: Context) -> List[str]:
    r = nm(ctx, "-t", "-f", "NAME,TYPE", "connection", "show")
    out = []
    for line in (r.stdout or "").splitlines():
        name, _, typ = line.rpartition(":")
        if typ == "802-11-wireless" and name:
            out.append(name.replace("\\:", ":"))
    return out


def active_wifi(ctx: Context) -> List[str]:
    r = nm(ctx, "-t", "-f", "NAME,TYPE", "connection", "show", "--active")
    return [line.rpartition(":")[0].replace("\\:", ":") for line in (r.stdout or "").splitlines()
            if line.endswith(":802-11-wireless")]


def ssid_of(ctx: Context, profile: str) -> str:
    return (nm(ctx, "-g", "802-11-wireless.ssid", "connection", "show", profile).stdout or "").strip()


def visible_networks(ctx: Context) -> Dict[str, int]:
    r = nm(ctx, "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes", timeout=40)
    seen: Dict[str, int] = {}
    for line in (r.stdout or "").splitlines():
        ssid, _, signal = line.rpartition(":")
        try:
            seen[ssid.replace("\\:", ":")] = max(seen.get(ssid, 0), int(signal))
        except ValueError:
            pass
    return seen


def site_profiles(ctx: Context) -> List[str]:
    setup = ctx.session.values.get("setup_connection")
    return [p for p in wifi_profiles(ctx) if p != setup]


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees from true north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def sharpness(path: str) -> Optional[float]:
    """Variance of the Laplacian of the photo, grey at a quarter size."""
    try:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            g = np.asarray(im.convert("L").resize((im.width // 4, im.height // 4)), dtype=np.float64)
    except Exception:  # noqa: BLE001
        return None
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


# ── A. before mounting ──────────────────────────────────────────────────────

def step_start(ctx: Context) -> StepResult:
    ui, values = ctx.ui, ctx.session.values
    cfg = read_config()
    device = (cfg.get("ip_upload") or {}).get("device_id") or "unknown"
    release = (ctx.tool("node_version.py", timeout=30).stdout or "").strip().splitlines()
    ui.say(f"  Unit: {device}   Software: {release[0] if release else 'unknown'}")
    offset = clock_offset_s()
    ui.say(f"  Clock: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}"
           + (f", {abs(offset):.0f} s {'behind' if offset > 0 else 'ahead of'} the server" if offset is not None
              else " (the server can't be reached to compare)"))

    if "initial" not in values:
        enabled = (ctx.run(["systemctl", "is-enabled", "ticktalk"], timeout=10).stdout or "").strip()
        values["initial"] = {"ticktalk_enabled": enabled == "enabled", "hold_awake": cfg.get("hold_awake"),
                             "hold_flag": os.path.exists(HOLD_FLAG)}
        ctx.session.save()

    ui.say("  Keeping the unit awake for setup (it would otherwise power off after its capture):")
    if cfg.get("hold_awake") is not True:
        ctx.set_parameter("hold_awake", True, before=cfg.get("hold_awake"))
    if (ctx.run(["systemctl", "is-active", "ticktalk"], timeout=10).stdout or "").strip() == "active":
        ctx.change("stop the capture service (ticktalk)", ["sudo", "systemctl", "stop", "ticktalk"])
    if (ctx.run(["systemctl", "is-enabled", "ticktalk"], timeout=10).stdout or "").strip() == "enabled":
        ctx.change("disable the capture service until setup is finished",
                   ["sudo", "systemctl", "disable", "ticktalk"], before="enabled")

    out = {"device_id": device, "release": release[0] if release else ""}
    active = active_wifi(ctx)
    if active:
        ui.say(f"  You are connected over the Wi-Fi network '{active[0]}'.")
        if ui.yes_no("  Is that your setup hotspot (your phone), not the site's own Wi-Fi?", default=True):
            out["setup_connection"] = active[0]
        else:
            out["setup_connection"] = None
    out["clock_offset_s"] = offset
    if offset is not None and abs(offset) > 120:
        ui.say(f"  The clock is {abs(offset):.0f} s off. The unit sets it from network time at boot when it is")
        ui.say("  online, so it probably had no internet then.")
        sync = os.path.join(WITTYPI_DIR, "syncTime.sh")
        if os.path.exists(sync) and ui.yes_no("  Set it from network time now (and the Witty Pi's clock)?", default=True):
            ctx.change("set the clock and the Witty Pi's RTC from network time (syncTime.sh)",
                       ["sudo", "bash", sync], timeout=120)
            if not ctx.dry_run:
                offset = clock_offset_s()
                out["clock_offset_s"] = offset
        if offset is not None and abs(offset) > 120:
            return StepResult("warn", f"The unit's clock is still {abs(offset):.0f} s off the server's: captures "
                                      "would carry the wrong time. It is fixed at the next boot with internet, or "
                                      "after the first IP upload.", out)
    return StepResult("pass", "The unit stays awake until setup is finished.", out)


def step_health(ctx: Context) -> StepResult:
    ctx.ui.say("  Checking battery, radio, GPS, IMU and camera. The GPS part can take up to 2 minutes.")
    r = ctx.tool("initial_health_check.py", "--no-alert", timeout=420, interactive=True)
    t = throttled(ctx)
    ctx.ui.say(f"  Power flags: {describe_throttled(t)}")
    vals = {"throttled_at_start": t}
    if r.returncode != 0:
        return StepResult("fail", "The health check found a problem (see above). Fix it before mounting.", vals)
    if t and t & 0x1:
        return StepResult("fail", "The supply is under-voltage right now: check the battery and the power cable.", vals)
    return StepResult("pass", "The unit is healthy.", vals)


def step_new_site(ctx: Context) -> StepResult:
    ui = ctx.ui
    ui.say("  A unit remembers where it is mounted: its GPS home, the camera orientation")
    ui.say("  reference and the normal water level (flood baseline).")
    if not ui.yes_no("  Is this a new site, or is the camera being re-aimed?", default=None):
        return StepResult("skip", "Same site and angle: kept what the unit learned there.", {"new_site": False})
    from tools import flood_baseline, gps_home  # paths only
    backup = os.path.join(ctx.session.path, "backup")
    files = [gps_home.HOME_PATH, flood_baseline.DEFAULT_STATE_PATH,
             os.path.join(REPO, "images", "registration_transform.json")]
    if not ctx.dry_run:
        os.makedirs(backup, exist_ok=True)
    for f in files:
        if os.path.exists(f):
            ctx.change(f"back up {os.path.relpath(f, REPO)}", fn=lambda f=f: shutil.copy2(f, backup))
    ok = all([
        ctx.change("forget the GPS home (it learns the new mount position)", [VENV_PY, "tools/gps_home.py", "--reset"]),
        ctx.change("forget the flood baseline (set again after the first capture)",
                   [VENV_PY, "tools/flood_baseline.py", "--reset"]),
        ctx.change("forget the camera orientation reference (adopted again at the first capture)",
                   [VENV_PY, "tools/registration_orientation.py", "--reset"], timeout=120),
    ])
    if not ok:
        return StepResult("fail", "Some of the old position's data could not be cleared (see above).", {"new_site": True})
    return StepResult("pass", "Cleared what the unit learned at its old position (backed up in this session).",
                      {"new_site": True})


def step_wiggle(ctx: Context) -> StepResult:
    ui = ctx.ui
    ui.say("  This checks the power wiring while you can still handle the box.")
    ui.say(f"  For {WIGGLE_S} seconds: gently tug the power cable at the Witty Pi, nudge the")
    ui.say("  mDot wiring, and tilt the box a few times. The unit must not restart or dip.")
    ui.pause("  Press Enter, then start.")
    start_restarts, start_t = restarts_logged(), throttled(ctx)
    ctx.session.values["wiggle_boot_id"] = boot_id()
    ctx.session.values["restarts_before_mount"] = start_restarts
    ctx.session.save()
    low, readings = None, 0
    end = time.monotonic() + WIGGLE_S
    while time.monotonic() < end:
        v = witty_vout(ctx)
        if v is not None:
            readings += 1
            low = v if low is None else min(low, v)
        t = throttled(ctx)
        if t is not None and start_t is not None and (t & 0x1 or (t & 0x10000 and not start_t & 0x10000)):
            return StepResult("fail", f"The supply dipped during the test ({describe_throttled(t)}): "
                                      "a loose or thin power connection. Fix it and repeat.", {})
        ui.say(f"  {int(end - time.monotonic()):3d} s left   output {v if v is not None else '?'} V")
        time.sleep(2)
    if restarts_logged() != start_restarts:
        return StepResult("fail", "The Witty Pi logged a restart during the test: check the power wiring.", {})
    vals = {"wiggle_min_vout": low}
    if low is not None and low < MIN_VOUT:
        return StepResult("fail", f"The output fell to {low:.2f} V (needs {MIN_VOUT} V or more): check the "
                                  "battery and the power cable.", vals)
    if not readings:
        return StepResult("warn", "No restart or dip seen, but the output voltage could not be read.", vals)
    return StepResult("pass", f"No restart or dip; the output stayed at {low:.2f} V or more.", vals)


def _imu_log_check(ctx: Context, seconds: int = 60) -> Optional[Dict[str, int]]:
    """Run the IMU log for a while; the last cal= reading as {sys, gyro, accel, mag}."""
    r = ctx.tool("bno055_calibration.py", "--mode", "log", *unit_config_args(), timeout=seconds)
    found = re.findall(r"cal=(\d)/(\d)/(\d)/(\d)", r.stdout or "")
    if not found:
        return None
    s, g, a, m = (int(x) for x in found[-1])
    return {"sys": s, "gyro": g, "accel": a, "mag": m}


def imu_calibration_file() -> str:
    """Where bno055_calibration.py keeps the IMU offsets (the unit config may say)."""
    args = unit_config_args()
    if args:
        try:
            with open(args[1]) as f:
                name = json.load(f).get("imu_calibration_file")
            if name:
                return name if os.path.isabs(name) else os.path.join(REPO, name)
        except (OSError, ValueError):
            pass
    return os.path.join(REPO, "bno055_calibration.json")


def step_magnetometer(ctx: Context) -> StepResult:
    ui = ctx.ui
    calib = imu_calibration_file()
    if not os.path.exists(calib):
        return StepResult("fail", "This unit's IMU has never been calibrated: the gyroscope and accelerometer "
                                  "are calibrated at the bench before the trip (FIELD_DEPLOYMENT_GUIDE.md 1.7, "
                                  f"tools/bno055_calibration.py), and {os.path.basename(calib)} is missing. "
                                  "Without it the compass calibration can't be kept.", {})
    ui.say("  Calibrate the compass (magnetometer) before mounting: metal nearby spoils it.")
    ui.say("  Stand at least 10 m from vehicles, the pole, fences and cables, with the box")
    ui.say("  closed as it will be deployed. The tool will ask you to move it in a slow")
    ui.say("  figure-eight; keep going for 30 s after it shows mag=3.")
    ui.pause("  Press Enter when you are at a clear spot.")
    if ctx.dry_run:
        ui.say("  (dry run) would run the compass calibration (it saves new compass offsets)")
    else:
        r = ctx.tool("bno055_calibration.py", "--mode", "calibrate", "--stages", "mag", *unit_config_args(),
                     timeout=900, interactive=True)
        ctx.session.data["changes"].append({"at": _now(), "what": "calibrate the magnetometer",
                                            "ok": r.returncode == 0, "dry_run": False})
        ctx.session.save()
        if r.returncode != 0:
            return StepResult("fail", "The compass calibration did not finish. Repeat it at a clear spot.", {})
    ui.say("  Checking it for a minute: keep the box still, then turn it slowly.")
    cal = _imu_log_check(ctx)
    if cal is None:
        return StepResult("warn", "Could not read the IMU calibration status to check it.", {})
    vals = {"imu_cal": cal}
    good = cal["mag"] == 3 and cal["sys"] == 3 and cal["gyro"] == 3 and cal["accel"] >= 2
    msg = f"IMU calibration sys={cal['sys']} gyro={cal['gyro']} accel={cal['accel']} mag={cal['mag']}"
    if good:
        return StepResult("pass", msg + " (good).", vals)
    return StepResult("warn", msg + ": should be 3/3/3/3 (accel 2 is OK). Repeat the figure-eight if mag < 3.", vals)


def step_coverage(ctx: Context) -> StepResult:
    ui = ctx.ui
    ui.say("  Hold the unit where it will be mounted, about as high, so you don't mount it")
    ui.say("  somewhere without a signal.")
    ui.pause("  Press Enter when you are there.")
    lora = lora_assess(ctx)
    ui.say(f"  LoRa: {'healthy' if lora.get('healthy') else 'not healthy'}"
           f"  margin {lora.get('margin_db', '?')} dB, gateways {lora.get('gateways', '?')}"
           + (f" ({lora.get('reason')})" if not lora.get("healthy") and lora.get("reason") else ""))
    seen = visible_networks(ctx)
    wifi = {}
    for p in site_profiles(ctx):
        ssid = ssid_of(ctx, p)
        wifi[p] = seen.get(ssid)
        ui.say(f"  Site Wi-Fi '{p}': " + (f"seen, signal {seen[ssid]}%" if ssid in seen else "not seen"))
    vals = {"coverage_lora": lora, "coverage_wifi": wifi}
    if lora.get("healthy"):
        return StepResult("pass", "LoRa reaches a gateway from here.", vals)
    if any(v for v in wifi.values()):
        return StepResult("warn", "LoRa is weak here; the unit will send over the site Wi-Fi instead. "
                                  "A higher or more open spot may help LoRa.", vals)
    return StepResult("warn", "Neither LoRa nor a known Wi-Fi network is good here. Try another spot, "
                              "or the unit will rely on cellular.", vals)


# ── B. mounting ─────────────────────────────────────────────────────────────

def step_mount(ctx: Context) -> StepResult:
    ui = ctx.ui
    ui.say("  Mount the unit rigidly: it must not sway or turn. Aim the camera at the area")
    ui.say("  to monitor. Don't seal the case yet.")
    ui.pause("  Press Enter when it is fixed in place.")
    site: Dict[str, Any] = {}
    site["mount_height_m"] = ui.number("  Mount height above the water or ground, in metres:", 0.1, 50)
    site["compass_heading_deg"] = ui.number("  Phone compass heading the camera faces, in degrees:", 0, 360,
                                            allow_blank=True)
    site["pole"] = ui.ask("  What is it mounted on (wood post, metal pole, wall...)?")
    site["magnetic_sources"] = ui.ask("  Any metal or cables close by? (Enter for none)")
    if ui.yes_no("  Do you have an RTK position for the camera?", default=False):
        site["camera_lat"] = ui.number("    Camera latitude (decimal degrees):", -90, 90)
        site["camera_lon"] = ui.number("    Camera longitude (decimal degrees):", -180, 180)
        site["camera_alt_m"] = ui.number("    Camera altitude (m):", -500, 9000, allow_blank=True)
    ui.say("  These stay on the unit, in this session's folder.")
    ok = ctx.change("start a new pose reference (the next 3 steady captures become it)",
                    [VENV_PY, "tools/pose_monitor.py", "--reset"])
    if not ok:
        return StepResult("fail", "Could not reset the pose reference (see above).", {"site": site})
    return StepResult("pass", "Mount recorded.", {"site": site})


# ── C. at the final position ────────────────────────────────────────────────

def step_connectivity(ctx: Context) -> StepResult:
    ui, cfg = ctx.ui, read_config()
    results: Dict[str, Any] = {}

    ui.say("  LoRa: the right sub-band and a fresh join from the final position.")
    if ui.yes_no("  Rejoin the LoRa network now? (recommended after a move)", default=True):
        ctx.change("stop the LoRa service for the rejoin", ["sudo", "systemctl", "stop", "lora_daemon"])
        ctx.change("check the mDot's sub-band and rejoin", [VENV_PY, "tools/mdot_subband.py", "--rejoin"],
                   timeout=240)
        ctx.change("start the LoRa service again", ["sudo", "systemctl", "start", "lora_daemon"])
        if not ctx.dry_run:
            time.sleep(10)
    checks = []
    for i in range(3):
        a = lora_assess(ctx)
        checks.append(a)
        ui.say(f"  LoRa check {i + 1}/3: {'healthy' if a.get('healthy') else 'not healthy'}, "
               f"margin {a.get('margin_db', '?')} dB, gateways {a.get('gateways', '?')}")
        if i < 2 and not ctx.dry_run:
            time.sleep(15)
    good = [a for a in checks if a.get("healthy") and (a.get("margin_db") or 0) >= MIN_LORA_MARGIN_DB]
    results["lora_healthy"] = len(good) >= 2
    results["lora_checks"] = checks

    results["wifi"] = _site_wifi_test(ctx)

    conn = (cfg.get("ip_upload") or {}).get("cellular_connection") or "Quectel"
    if ui.yes_no("  Test cellular? It uses a few hundred KB of the SIM's data.", default=True):
        if ctx.dry_run:
            ui.say(f"  (dry run) would bring up '{conn}', check the server, and take it down again")
            results["cellular"] = None
        else:
            r = ctx.py("import sys; from tools import transmit_ip as t\n"
                       f"up = t.cellular_up({conn!r}, 90)\n"
                       "ok = up and t.IPTransmitter().is_reachable()\n"
                       f"t.cellular_down({conn!r})\n"
                       "print('reachable' if ok else ('up' if up else 'down'))", timeout=200)
            state = ((r.stdout or "").strip().splitlines() or ["down"])[-1]
            results["cellular"] = state == "reachable"
            ui.say(f"  Cellular: {'reaches the server' if results['cellular'] else 'did not reach the server'} ({state})")

    paths = [n for n, ok in (("LoRa", results["lora_healthy"]), ("site Wi-Fi", (results["wifi"] or {}).get("ok")),
                             ("cellular", results.get("cellular"))) if ok]
    vals = {"connectivity": results}
    if not paths and not ctx.dry_run:
        return StepResult("fail", "No way to send data from here works. Check the antennas and the spot.", vals)
    if not results["lora_healthy"] and not ctx.dry_run:
        return StepResult("warn", f"LoRa is weak here; the unit will mostly use {', '.join(paths)}.", vals)
    return StepResult("pass", "Working: " + (", ".join(paths) or "(dry run)") + ".", vals)


def _site_wifi_test(ctx: Context) -> Optional[Dict[str, Any]]:
    ui = ctx.ui
    sites = site_profiles(ctx)
    setup = ctx.session.values.get("setup_connection")
    if not sites:
        ui.say("  Site Wi-Fi: none configured on this unit; skipped.")
        return None
    site = sites[0]
    if len(sites) > 1:
        for i, p in enumerate(sites, 1):
            ui.say(f"    {i}. {p}")
        site = sites[int(ui.number("  Which one is the site's Wi-Fi? Number:", 1, len(sites))) - 1]
    if not setup or site in active_wifi(ctx):
        ui.say(f"  Site Wi-Fi: already connected to '{site}', checking it in place.")
        from tools import transmit_ip
        ok = transmit_ip.IPTransmitter().is_reachable()
        ui.say(f"  Site Wi-Fi: {'reaches the server' if ok else 'does NOT reach the server'}.")
        return {"site": site, "ok": ok, "in_place": True}
    ui.say(f"  Site Wi-Fi: '{site}' is tested from a background job, because switching to it")
    ui.say(f"  cuts this connection: it leaves '{setup}', tries '{site}' and the server,")
    ui.say(f"  then comes back to '{setup}', usually within 2 minutes (at most about 6).")
    ui.say("  If this session drops, reconnect and run field_setup.py again: it shows the result.")
    if not ui.yes_no("  Start the test?", default=True):
        return None
    job = os.path.join(ctx.session.path, "wifi_test")
    if ctx.dry_run:
        ui.say("  (dry run) would start the background Wi-Fi test")
        return None
    os.makedirs(job, exist_ok=True)
    for f in ("result.json",):
        try:
            os.remove(os.path.join(job, f))
        except FileNotFoundError:
            pass
    with open(os.path.join(job, "job.json"), "w") as f:
        json.dump({"setup": setup, "site": site}, f)
    unit = f"field-setup-wifi-{time.strftime('%H%M%S')}"
    ctx.change(f"start the background Wi-Fi test ({unit})",
               ["sudo", "systemd-run", f"--unit={unit}", "-p", "User=pi", "-p", f"RuntimeMaxSec={WIFI_TEST_KILL_S}",
                "-p", f"Environment=WATERCAM_REPO={REPO}", "--collect", VENV_PY,
                os.path.join(TOOLS_DIR, "field_setup.py"), "--wifi-test-job", job])
    return wait_wifi_result(ctx, job)


def wait_wifi_result(ctx: Context, job: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(job, "result.json")
    end = time.monotonic() + WIFI_TEST_MAX_S + 60
    while time.monotonic() < end:
        if os.path.exists(path):
            with open(path) as f:
                res = json.load(f)
            ctx.ui.say(f"  Site Wi-Fi '{res.get('site')}': "
                       + ("reaches the server" if res.get("ok") else f"did NOT reach the server ({res.get('reason')})")
                       + (f", signal {res['signal']}%" if res.get("signal") is not None else "")
                       + ("" if res.get("setup_restored") else f"; WARNING: could not reconnect to '{res.get('setup')}'"))
            return res
        time.sleep(3)
    ctx.ui.say("  Site Wi-Fi: no result yet. Run field_setup.py again later to see it.")
    return None


def wifi_test_job(job: str) -> int:
    """The background job (field_setup.py --wifi-test-job DIR): leave the setup
    network, try the site's, then always go back to the setup network."""
    with open(os.path.join(job, "job.json")) as f:
        spec = json.load(f)
    setup, site = spec["setup"], spec["site"]
    res: Dict[str, Any] = {"site": site, "setup": setup, "ok": False, "started": _now()}

    def nmcli(*args, timeout=60):
        try:
            return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return subprocess.CompletedProcess(args, 1, "", str(e))
    try:
        time.sleep(3)                       # let the interactive session print its notice
        nmcli("connection", "down", setup, timeout=20)
        up = nmcli("--wait", "45", "connection", "up", site, timeout=60)
        if up.returncode != 0:
            res["reason"] = (up.stderr or up.stdout).strip()[-200:] or "could not connect"
        else:
            sig = nmcli("-t", "-f", "IN-USE,SIGNAL", "device", "wifi", timeout=20).stdout or ""
            for line in sig.splitlines():
                if line.startswith("*:"):
                    try:
                        res["signal"] = int(line.split(":")[1])
                    except ValueError:
                        pass
            from tools import transmit_ip
            tx = transmit_ip.IPTransmitter()
            res["ok"] = tx.wait_reachable(40)
            if not res["ok"]:
                res["reason"] = "connected, but the server could not be reached"
    except Exception as e:  # noqa: BLE001 - recorded, then the setup network comes back
        res["reason"] = f"test failed: {e}"
    finally:
        restored = False
        for _ in range(3):
            if nmcli("--wait", "45", "connection", "up", setup, timeout=60).returncode == 0:
                restored = True
                break
            time.sleep(5)
        res["setup_restored"] = restored
        res["finished"] = _now()
        tmp = os.path.join(job, "result.json.tmp")
        with open(tmp, "w") as f:
            json.dump(res, f, indent=2)
        os.replace(tmp, os.path.join(job, "result.json"))
    return 0 if res["ok"] else 1


def step_power_mount(ctx: Context) -> StepResult:
    t = throttled(ctx)
    v = witty_vout(ctx)
    before = ctx.session.values.get("restarts_before_mount")
    restarted = before is not None and restarts_logged() > before
    ctx.ui.say(f"  Power flags: {describe_throttled(t)}; output {v if v is not None else '?'} V")
    vals = {"vout_at_mount": v, "throttled_at_mount": t}
    if restarted or (ctx.session.values.get("wiggle_boot_id") not in (None, boot_id())):
        return StepResult("fail", "The unit restarted since the wiggle test: check the power wiring before "
                                  "sealing the case.", vals)
    if t and t & 0x1:
        return StepResult("fail", "The supply is under-voltage now: check the battery and the cable.", vals)
    if v is not None and v < MIN_VOUT:
        return StepResult("fail", f"The output is {v:.2f} V (needs {MIN_VOUT} V or more).", vals)
    return StepResult("pass", "Power is good at the mount. You can seal the case now.", vals)


def step_heading(ctx: Context) -> StepResult:
    ui = ctx.ui
    site = ctx.session.values.get("site") or {}
    if site.get("camera_lat") is None:
        return StepResult("skip", "No RTK camera position: the mount heading isn't calibrated, so the pose "
                                  "stays provisional until a visit with RTK.", {"pose": "provisional"})
    ui.say("  Pick a surveyed point near the centre of the camera's view.")
    lat = ui.number("  Its latitude (Enter to skip this step):", -90, 90, allow_blank=True)
    if lat is None:
        return StepResult("skip", "Skipped: the pose stays provisional.", {"pose": "provisional"})
    lon = ui.number("  Its longitude:", -180, 180)
    b = bearing_deg(site["camera_lat"], site["camera_lon"], lat, lon)
    ui.say(f"  True bearing from the camera to it: {b:.2f} degrees.")
    if ctx.dry_run:
        ui.say(f"  (dry run) would calibrate the mount heading to {b:.2f} degrees and save the correction")
        return StepResult("pass", "(dry run)", {"known_bearing_deg": b})
    r = ctx.tool("bno055_calibration.py", "--mode", "mount", "--known-bearing", f"{b:.2f}", "--save-correction",
                 *unit_config_args(), timeout=600, interactive=True)
    ctx.session.data["changes"].append({"at": _now(), "what": f"save the mount heading correction ({b:.2f} deg)",
                                        "ok": r.returncode == 0, "dry_run": False})
    ctx.session.save()
    if r.returncode != 0:
        return StepResult("fail", "The mount heading calibration did not finish.", {"known_bearing_deg": b})
    v = ctx.tool("bno055_calibration.py", "--mode", "validate", *unit_config_args(), timeout=300, interactive=True)
    if v.returncode != 0:
        return StepResult("warn", "Saved, but the check afterwards failed: repeat this step.", {"known_bearing_deg": b})
    return StepResult("pass", "Mount heading calibrated.", {"known_bearing_deg": b, "pose": "rtk"})


def step_reference_photo(ctx: Context) -> StepResult:
    ui = ctx.ui
    ui.say("  A full-size photo from the camera, as it is aimed now. The dashboard draws the")
    ui.say("  water maps on it through the camera's lens calibration.")
    if (ctx.run(["systemctl", "is-active", "ticktalk"], timeout=10).stdout or "").strip() == "active":
        return StepResult("fail", "The capture service is running and using the camera; run step 0 again.", {})
    path = os.path.join(ctx.session.path, "reference.jpg")
    r = ctx.run(["rpicam-still", "--nopreview", "--width", str(PHOTO_W), "--height", str(PHOTO_H), "-o", path],
                timeout=60)
    if r.returncode != 0 or not os.path.exists(path):
        return StepResult("fail", "The camera did not take the photo: " + ((r.stderr or "").strip()[-200:] or "no file"), {})
    vals: Dict[str, Any] = {"reference_photo": path}
    try:
        import numpy as np
        from PIL import Image
        with Image.open(path) as im:
            size = im.size
            mean = float(np.asarray(im.convert("L").resize((im.width // 8, im.height // 8))).mean())
    except Exception as e:  # noqa: BLE001
        return StepResult("fail", f"The photo can't be read: {e}", vals)
    sharp = sharpness(path)
    vals.update({"reference_photo_size": list(size), "reference_photo_brightness": round(mean, 1),
                 "reference_photo_sharpness": None if sharp is None else round(sharp, 1)})
    ui.say(f"  Photo: {size[0]}x{size[1]}, brightness {mean:.0f}/255, sharpness {sharp:.0f}" if sharp is not None
           else f"  Photo: {size[0]}x{size[1]}, brightness {mean:.0f}/255")
    problems = []
    if size != (PHOTO_W, PHOTO_H):
        problems.append(f"it is {size[0]}x{size[1]}, not {PHOTO_W}x{PHOTO_H}")
    if mean < 15:
        problems.append("it is nearly black (lens covered, or too dark)")
    if mean > 240:
        problems.append("it is nearly white (pointing at the sky or the sun)")
    if sharp is not None and sharp < MIN_SHARPNESS:
        problems.append("it looks blurred (fogged window, or out of focus)")
    host = os.uname().nodename
    ui.say(f"  Saved as {path}")
    ui.say(f"  To look at it on your laptop: scp {host}:{os.path.relpath(path, os.path.expanduser('~'))} .")
    if problems:
        return StepResult("warn", "Check the photo: " + "; ".join(problems) + ". Fix it and repeat this step.", vals)

    uploaded = upload_reference(ctx, path) if ctx.upload and not ctx.dry_run else None
    if ctx.dry_run or not ctx.upload:
        ui.say("  (not uploaded in this run)")
        return StepResult("pass", "Photo taken.", vals)
    vals["uploaded"] = uploaded
    if uploaded.get("photo") and uploaded.get("calibration"):
        return StepResult("pass", "Photo and camera calibration uploaded to the server.", vals)
    return StepResult("warn", "Photo taken but not all of it reached the server: " + uploaded.get("message", "")
                              + " Upload the photo in the dashboard (Management, Reference photo) and paste "
                                "config/camera_calibration.json into the Georeference page.", vals)


def _refusal(what: str, code: int) -> str:
    if code in (404, 405):
        return f"the server doesn't take {what}s from units yet"
    if code == 401:
        return (f"the server refused the unit's token for the {what}: check ip_upload.api_key "
                "(tools/set_ip_token.py)")
    if code == 429:
        return f"too many uploads for now ({what}); try again in an hour"
    return f"the server refused the {what} ({code})"


def upload_reference(ctx: Context, photo: str) -> Dict[str, Any]:
    """Send the photo and config/camera_calibration.json to the server with the
    unit's own API token (WaterCam-Team/API: POST /ip/devices/{id}/reference-photo,
    PUT /ip/devices/{id}/calibration)."""
    import requests
    from tools import transmit_ip
    tx = transmit_ip.IPTransmitter()
    out: Dict[str, Any] = {"photo": False, "calibration": False}
    headers = tx._auth_headers()
    if not tx.server_url or not tx.device_id:
        out["message"] = "the unit has no server or device id configured."
        return out
    if not headers:
        out["message"] = ("the unit's token is only sent to an https or tailnet server, or it has none "
                          "(tools/set_ip_token.py).")
        return out
    base = f"{tx.server_url.rstrip('/')}/ip/devices/{tx.device_id}"
    msgs = []
    try:
        with open(photo, "rb") as f:
            r = requests.post(f"{base}/reference-photo", headers=headers, timeout=60,
                              files={"file": ("reference.jpg", f, "image/jpeg")})
        out["photo"] = r.status_code == 200
        if not out["photo"]:
            msgs.append(_refusal("photo", r.status_code))
        with open(CALIBRATION) as f:
            cal = json.load(f)
        r = requests.put(f"{base}/calibration", headers=headers, timeout=30,
                         json={k: cal[k] for k in ("K", "D", "img_size")})
        out["calibration"] = r.status_code == 200
        if not out["calibration"]:
            msgs.append(_refusal("calibration", r.status_code))
    except (OSError, ValueError, KeyError, requests.RequestException) as e:
        msgs.append(f"upload failed: {e}")
    out["message"] = "; ".join(msgs) + ("." if msgs else "")
    ctx.ui.say(f"  Upload: photo {'sent' if out['photo'] else 'NOT sent'}, "
               f"calibration {'sent' if out['calibration'] else 'NOT sent'}")
    return out


def step_first_capture(ctx: Context) -> StepResult:
    ui = ctx.ui
    ui.say("  One normal capture cycle from the final position (usually 2-4 minutes). The")
    ui.say("  shutdown at its end is intercepted, so the unit stays up.")
    if ctx.dry_run:
        ui.say("  (dry run) would run one held capture cycle (tools/held_capture.sh)")
        return StepResult("pass", "(dry run)", {})
    for svc in ("lora_daemon", "segformer_daemon"):
        if (ctx.run(["systemctl", "is-active", svc], timeout=10).stdout or "").strip() != "active":
            ctx.change(f"start {svc}", ["sudo", "systemctl", "start", svc])
    out = os.path.join(ctx.session.path, f"capture-{time.strftime('%H%M%S')}")
    unit = f"field-setup-capture-{time.strftime('%H%M%S')}"
    ctx.change(f"run one held capture cycle ({unit})",
               ["sudo", "systemd-run", f"--unit={unit}", "--collect",
                "-p", f"Environment=WATERCAM_REPO={REPO}", os.path.join(TOOLS_DIR, "held_capture.sh"),
                "--keep-schedule", out])   # at installation the wake's schedule is the one to keep
    start = time.monotonic()
    shown = 0
    while time.monotonic() - start < 1000 and not os.path.exists(os.path.join(out, "result.json")):
        time.sleep(5)
        elapsed = int(time.monotonic() - start)
        if elapsed - shown >= 30:
            shown = elapsed
            ui.say(f"  ... still capturing ({elapsed} s)")
    res = check_capture(out)
    vals = {"first_capture": res}
    for line in res["lines"]:
        ui.say("  " + line)
    if not res["ok"]:
        return StepResult("fail", "The capture cycle didn't complete: " + res["problem"], vals)

    ui.say("")
    ui.say("  The flood baseline is the normal amount of water in view; a flood is judged")
    ui.say("  against it.")
    if ui.yes_no("  Is the water at its normal level right now (no flooding, no unusual rain)?", default=True):
        ctx.change("set the flood baseline from this capture", [VENV_PY, "tools/flood_baseline.py", "--set-from-latest"])
    else:
        n = ui.number("  Usual water in view, in percent (0-100):", 0, 100, allow_blank=True)
        if n is not None:
            ctx.change(f"set the flood baseline to {int(n)}%", [VENV_PY, "tools/flood_baseline.py", "--set", str(int(n))])
    shown = ctx.tool("flood_baseline.py", "--show", timeout=30)
    ui.say("  " + (shown.stdout or "").strip().replace("\n", "\n  "))
    return StepResult("pass", "First capture done. Check the dashboard: a new reading, the mask and water %.", vals)


def check_capture(out: str) -> Dict[str, Any]:
    """What a held capture's folder (tools/held_capture.sh) says about the wake."""
    def read(name):
        try:
            with open(os.path.join(out, name), errors="replace") as f:
                return f.read()
        except OSError:
            return ""
    res: Dict[str, Any] = {"ok": False, "problem": "", "lines": []}
    try:
        status = json.loads(read("result.json") or "{}")
    except ValueError:
        status = {}
    journal = read("wake.journal")
    captures = [c for c in read("captures.txt").split() if c]
    ip_ok = journal.count("IP uplink OK")
    lora_sent = read("lora_last_sent.before") != read("lora_last_sent.after") and bool(read("lora_last_sent.after"))
    tracebacks = journal.count("Traceback (most recent call last)")
    shutdown_reached = "shutdown" in read("doas.log")
    # The first capture of the wake is where a new orientation reference is adopted
    adopted = None
    for cap in captures:
        try:
            with open(os.path.join(cap, "registration_check.json")) as f:
                adopted = json.load(f).get("status")
            break
        except (OSError, ValueError):
            continue
    res.update(captures=len(captures), ip_uplinks=ip_ok, lora_sent=lora_sent, tracebacks=tracebacks,
               shutdown_reached=shutdown_reached, registration=adopted, status=status.get("status"))
    res["lines"] = [f"captures: {len(captures)}",
                    f"sent: {ip_ok} IP uplink(s)" + (", LoRa" if lora_sent else ", no LoRa send seen"),
                    f"camera registration: {adopted or 'unknown'}",
                    f"errors in the log: {tracebacks}",
                    f"reached its normal end (shutdown intercepted): {'yes' if shutdown_reached else 'no'}"]
    if status.get("status") != "done":
        res["problem"] = status.get("reason") or "it did not finish"
    elif not captures:
        res["problem"] = "no capture was saved"
    elif not (ip_ok or lora_sent):
        res["problem"] = "nothing was sent (no IP uplink, no LoRa send)"
    elif tracebacks:
        res["problem"] = f"{tracebacks} error(s) in the log ({os.path.join(out, 'wake.journal')})"
    elif not shutdown_reached:
        res["problem"] = "it stopped before its normal end"
    else:
        res["ok"] = True
    return res


def step_finish(ctx: Context) -> StepResult:
    ui, values = ctx.ui, ctx.session.values
    ui.say("  On the dashboard, set this unit's wake schedule now (device view, Schedule")
    ui.say("  panel), or check the one it has.")
    if ui.yes_no("  Have you saved a schedule on the dashboard just now?", default=True):
        # Collecting applies the waiting commands (the schedule among them): a change
        if ctx.dry_run:
            ui.say("  (dry run) would collect and apply the commands waiting on the server")
        else:
            r = ctx.py("import json; from tools import last_downlink_check as l; "
                       "print(json.dumps(l.check(), default=str))", timeout=120)
            got = ((r.stdout or "").strip().splitlines() or ["(nothing)"])[-1]
            ctx.session.data["changes"].append({"at": _now(), "what": "collect and apply the waiting commands",
                                                "ok": r.returncode == 0, "detail": got[-300:], "dry_run": False})
            ctx.session.save()
            ui.say("  Collected from the server: " + got[:300])

    ui.say("  Putting the unit back on its schedule:")
    cfg = read_config()
    ctx.set_parameter("hold_awake", False, before=cfg.get("hold_awake"))
    if os.path.exists(HOLD_FLAG):
        ctx.change("remove the old hold-awake flag file", ["sudo", "rm", "-f", HOLD_FLAG], before="present")
    ctx.change("enable the capture service", ["sudo", "systemctl", "enable", "ticktalk"])

    rec = (ctx.run(["systemctl", "is-enabled", "wittypi-recovery.service"], timeout=10).stdout or "").strip()
    ui.say(f"  Recovery boot is {rec or 'not installed'}. With it on, the unit restarts by itself after a")
    ui.say("  power cut once the battery has recovered (it then stays off for at least 2 hours).")
    if rec in ("enabled", "disabled"):
        want = ui.yes_no("  Keep recovery boot on?", default=rec == "enabled")
        if want != (rec == "enabled"):
            ctx.change(("enable" if want else "disable") + " recovery boot",
                       ["sudo", "systemctl", "enable" if want else "disable", "wittypi-recovery.service"], before=rec)

    setup = values.get("setup_connection")
    if setup:
        ui.say(f"  The unit should stop preferring your hotspot '{setup}' once you leave.")
        if ui.yes_no(f"  Stop it connecting to '{setup}' automatically?", default=True):
            ctx.change(f"turn off autoconnect for '{setup}'",
                       ["nmcli", "connection", "modify", setup, "connection.autoconnect", "no"], before="yes")

    checks = {}
    for unit in ("ticktalk", "wittypi-next-wake", "max-awake.timer", "remote-debug.timer"):
        checks[unit] = (ctx.run(["systemctl", "is-enabled", unit], timeout=10).stdout or "").strip() or "missing"
    alarms = ctx.run(["bash", "-c", "cd ~/wittypi && . ./utilities.sh >/dev/null 2>&1 && "
                                    "echo startup $(get_startup_time) && echo shutdown $(get_shutdown_time)"],
                     timeout=30, cwd=os.path.expanduser("~"))
    held = read_config().get("hold_awake") is True or os.path.exists(HOLD_FLAG)
    for unit, state in checks.items():
        ui.say(f"  {unit}: {state}")
    ui.say("  Witty Pi alarms: " + " | ".join((alarms.stdout or "").strip().splitlines()))
    ui.say(f"  Held awake: {'YES' if held else 'no'}")

    logs = os.path.join(ctx.session.path, "logs")
    if not ctx.dry_run:
        os.makedirs(logs, exist_ok=True)
        for f in (WITTYPI_LOG, POWERLOG):
            if os.path.exists(f):
                try:
                    shutil.copy2(f, logs)
                except OSError:
                    pass
    vals = {"final_services": checks}
    problems = [u for u in ("ticktalk", "wittypi-next-wake") if checks.get(u) != "enabled"]
    if (held or problems) and not ctx.dry_run:
        return StepResult("fail", "Not ready: " + ("still held awake; " if held else "")
                                  + (f"not enabled: {', '.join(problems)}" if problems else ""), vals)
    return StepResult("pass", "The unit is back on its schedule.", vals)


STEPS: List[Step] = [
    Step("start", "A", "Start: keep the unit awake for setup", step_start),
    Step("health", "A", "Check the node", step_health),
    Step("new_site", "A", "New site or new angle", step_new_site),
    Step("wiggle", "A", "Power wiggle test", step_wiggle),
    Step("magnetometer", "A", "Compass (magnetometer) calibration", step_magnetometer),
    Step("coverage", "A", "Signal at the chosen spot", step_coverage),
    Step("mount", "B", "Mount and record", step_mount),
    Step("connectivity", "C", "Connectivity from the final position", step_connectivity),
    Step("power_mount", "C", "Power at the mount", step_power_mount),
    Step("heading", "C", "Mount heading (with RTK)", step_heading),
    Step("reference_photo", "C", "Reference photo", step_reference_photo),
    Step("first_capture", "C", "First capture and flood baseline", step_first_capture),
    Step("finish", "C", "Finish: back on the schedule", step_finish),
]
STEP_IDS = [s.id for s in STEPS]

PHASES = {
    "A": "Phase A: before mounting. Keep the unit in hand, near the site.",
    "B": "Phase B: mount the unit.",
    "C": "Phase C: at the final position. Don't seal the case until step 'Power at the mount' passes.",
}


def restore_normal(ctx: Context) -> None:
    """Quitting before the end: put back what step 'start' changed, so the unit
    isn't left awake draining its battery."""
    initial = ctx.session.values.get("initial") or {}
    cfg = read_config()
    if cfg.get("hold_awake") is True and initial.get("hold_awake") is not True:
        ctx.set_parameter("hold_awake", initial.get("hold_awake") or False, before=True)
    if initial.get("ticktalk_enabled"):
        ctx.change("enable the capture service again", ["sudo", "systemctl", "enable", "ticktalk"])
