#!/usr/bin/env python3
"""Remote debug session: keep the node up on cellular with Tailscale for an admin.

Started by the `18 98 NN` downlink (LoRa or IP) or by hand. Design and install
steps: docs/REMOTE_DEBUG_SESSION.md.

    remote_debug.py start [--minutes 60]   start, or extend the running session
    remote_debug.py stop [--shutdown]      restore normal operation now
    remote_debug.py status
    remote_debug.py tick                   remote-debug.timer, every 30 s

Starting: clear the Witty Pi shutdown alarm, stop ticktalk.service, bring the
modem's connection up and wait for Tailscale. Stopping: take cellular down again
if it is managed on demand, re-arm the Witty Pi from the installed schedule.wpi,
then start ticktalk (a normal cycle and its usual shutdown) or power off.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

DATA_DIR = os.path.join(REPO, "data")
STATE_FILE = os.path.join(DATA_DIR, "remote_debug.json")
REQUEST_FILE = os.path.join(DATA_DIR, "remote_debug_request.json")
LOCK_FILE = os.path.join(DATA_DIR, "remote_debug.lock")

MIN_MINUTES = 10
MAX_MINUTES = 240
DEFAULT_MINUTES = 60
TAILSCALE_WAIT_S = 120
SYSTEMCTL = "/usr/bin/systemctl"


def log(msg):
    print(f"[remote_debug] {msg}", flush=True)


# --- small helpers ------------------------------------------------------------------------

def _run(cmd, timeout=30):
    """Run cmd; return (returncode, stdout). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _remove(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return None


def clamp_minutes(minutes):
    return max(MIN_MINUTES, min(MAX_MINUTES, int(minutes)))


def minutes_from_code(value):
    """`18 98 NN`: NN (hex) x 10 minutes; 0 means end the session."""
    n = int(str(value).strip() or "0", 16)
    return 0 if n == 0 else clamp_minutes(n * 10)


def _cellular_connection():
    try:
        with open(os.path.join(REPO, "runtime_config.json")) as f:
            return json.load(f).get("ip_upload", {}).get("cellular_connection", "Quectel")
    except (OSError, ValueError, AttributeError):
        return "Quectel"


def _systemctl(action, unit="ticktalk.service"):
    """systemctl as root: doas first (config/doas.conf), then sudo -n."""
    args = [SYSTEMCTL, action, unit] + (["--no-block"] if action == "start" else [])
    for prefix in (["doas"], ["sudo", "-n"]):
        rc, out = _run(prefix + args, timeout=60)
        if rc == 0:
            return True
        log(f"{prefix[0]} systemctl {action} {unit} failed: {out or rc}")
    return False


# --- the pieces of a session --------------------------------------------------------------

def cellular_state(conn):
    """(active, managed_on_demand) for the modem's NetworkManager connection."""
    rc, out = _run(["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"])
    active = rc == 0 and conn in out.splitlines()
    rc, auto = _run(["nmcli", "-g", "connection.autoconnect", "connection", "show", conn])
    return active, (rc == 0 and auto == "no")


def cellular_up(conn):
    rc, out = _run(["nmcli", "--wait", "90", "connection", "up", conn], timeout=100)
    if rc != 0:
        log(f"cellular up failed: {out}")
    return rc == 0


def cellular_down(conn):
    _run(["nmcli", "connection", "down", conn], timeout=30)


def tailscale_online():
    rc, out = _run(["tailscale", "status", "--json"], timeout=15)
    if rc != 0:
        return False
    try:
        st = json.loads(out)
    except ValueError:
        return False
    return st.get("BackendState") == "Running" and bool((st.get("Self") or {}).get("Online"))


def wait_for_tailscale(deadline_s=TAILSCALE_WAIT_S):
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if tailscale_online():
            return True
        time.sleep(5)
    return False


WITTYPI_DIR = "/home/pi/wittypi"
ALARM_CLEARED = "00 00:00:00"


def _wittypi(func, timeout=20):
    """Run one utilities.sh function. Clearing an alarm can take longer than the 3 s
    tools/witty_pi_4.py allows, and i2cget/i2cset live in /usr/sbin."""
    env = dict(os.environ, PATH=os.environ.get("PATH", "/usr/bin:/bin") + ":/usr/sbin:/sbin")
    try:
        r = subprocess.run(["bash", "-c", f"cd {WITTYPI_DIR} && . ./utilities.sh && {func}"],
                           capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)


def suspend_shutdown_alarm(attempts=3):
    """Clear the Witty Pi shutdown alarm and read it back. If it stays set, the
    Witty Pi cuts power at the end of the ON slot, in the middle of the session."""
    for i in range(attempts):
        _wittypi("clear_shutdown_time")
        rc, alarm = _wittypi("get_shutdown_time")
        if rc == 0 and alarm.endswith(ALARM_CLEARED):
            return True
        log(f"Witty Pi shutdown alarm still {alarm!r} (attempt {i + 1})")
        time.sleep(2)
    log("could not clear the Witty Pi shutdown alarm: the node may power off at the end of its slot")
    return False


def rearm_schedule():
    """Re-arm the Witty Pi from the installed schedule.wpi (runScript.sh). Never writes it."""
    try:
        from tools.wittypi_control import installed_schedule_exists
        if not installed_schedule_exists():
            log("no schedule.wpi installed: nothing to re-arm")
            return True
        from tools.witty_pi_4 import WittyPi4
        nxt = WittyPi4().apply_schedule()
        log(f"Witty Pi schedule re-armed, next startup: {nxt}")
        return nxt not in (None, "-")
    except Exception as e:
        log(f"could not re-arm the Witty Pi schedule: {e}")
        return False


def _size_limit(handler):
    """The mDot's current payload limit: the real handler's, or the daemon's over RPC."""
    limit = getattr(handler, "current_size_limit", None)
    if not isinstance(limit, int):
        try:
            limit = int(handler.get_size_limit())
        except Exception:
            limit = 242
    return limit


def send_status(state, handler=None):
    """LoRa uplink {"dbg":1,"rd":<min left>,"tn":0/1,"cl":0/1,"ts":<epoch>}; best effort.

    Trimmed to the mDot's payload limit like the 5001 reply, dropping fields from
    the end. {"dbg":1,"rd":N} (about 17 B) is the least the server needs to confirm
    the request; below that (SF10, 11 B) nothing useful fits and nothing is sent.
    """
    left = 0
    if state and state.get("active"):
        left = max(0, int((state["until"] - time.time()) // 60))
    fields = [("dbg", 1), ("rd", left), ("tn", int(bool(state and state.get("tailscale")))),
              ("cl", int(bool(state and state.get("cellular")))), ("ts", int(time.time()))]
    try:
        if handler is None:
            from tools.lora_handler_concurrent import get_lora_handler
            handler = get_lora_handler()
        if handler is None:
            log("no LoRa handler: status not sent")
            return False
        from tools.lora_debug_integration import encode_for_limit
        limit = _size_limit(handler)
        payload, dropped = encode_for_limit(fields, limit)
        if "rd" in dropped:
            log(f"payload limit {limit} B is too small for a status uplink: not sent")
            return False
        ok = bool(handler.transmit(payload.hex()))
        log(f"status uplink {'sent' if ok else 'failed'}: {payload.decode()}")
        return ok
    except Exception as e:
        log(f"status uplink failed: {e}")
        return False


# --- session control ------------------------------------------------------------------------

class _Lock:
    """Serialise start/stop/tick across lora_daemon, the timer and the CLI."""

    def __init__(self, blocking=True):
        self.blocking = blocking
        self.fd = None

    def __enter__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o664)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | (0 if self.blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
        return self.fd is not None

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)


def load_state():
    return _read_json(STATE_FILE)


def start(minutes=DEFAULT_MINUTES, source="cli", handler=None):
    """Start the session, or extend a running one to `minutes` from now."""
    minutes = clamp_minutes(minutes)
    with _Lock():
        state = load_state() or {}
        conn = _cellular_connection()
        if not state.get("active"):
            was_up, _ = cellular_state(conn)
            state = {"active": True, "started": time.time(), "source": source,
                     "connection": conn, "cellular_was_up": was_up}
        extending = bool(state.get("tailscale")) and state.get("boot_id") == _boot_id()
        state["until"] = time.time() + minutes * 60
        log(f"session until {time.strftime('%H:%M', time.localtime(state['until']))} ({minutes} min, from {source})")
        _write_json(STATE_FILE, state)
        if extending:
            # already up: the API resends the command until it hears from the node
            send_status(state, handler)
            return state
        # answer at once, so the API stops resending; the full status follows
        send_status(dict(state, tailscale=False, cellular=cellular_state(state["connection"])[0]), handler)
        _establish(state)
    send_status(state, handler)
    return state


def _establish(state):
    """Hold the node up for the session; call with the lock held."""
    state["boot_id"] = _boot_id()
    _write_json(STATE_FILE, state)           # first, so a crash below still expires
    state["alarm_cleared"] = suspend_shutdown_alarm()
    _systemctl("stop")
    conn = state.get("connection") or _cellular_connection()
    state["cellular"] = cellular_state(conn)[0] or cellular_up(conn)
    state["tailscale"] = wait_for_tailscale()
    log(f"cellular {'up' if state['cellular'] else 'DOWN'}, tailscale {'online' if state['tailscale'] else 'NOT online'}")
    _write_json(STATE_FILE, state)


def stop(shutdown=False, handler=None, reason="requested"):
    """End the session and restore normal operation."""
    with _Lock():
        state = load_state()
        if not state or not state.get("active"):
            log("no session running")
            _remove(STATE_FILE)
            return False
        log(f"ending session ({reason})")
        conn = state.get("connection") or _cellular_connection()
        _, managed = cellular_state(conn)
        if managed and not state.get("cellular_was_up"):
            cellular_down(conn)
        rearm_schedule()
        _remove(STATE_FILE)
    send_status({"active": False}, handler)
    if shutdown:
        log("powering off; the Witty Pi wakes the Pi at its next slot")
        for cmd in (["doas", "/usr/sbin/shutdown", "-h", "now"], ["sudo", "-n", "/usr/sbin/shutdown", "-h", "now"]):
            if _run(cmd)[0] == 0:
                break
    else:
        _systemctl("start")
    return True


def request(minutes, source):
    """Ask the timer to start (minutes > 0) or stop (0) a session: for callers that
    can't run it themselves (ticktalk stopping itself would end the call)."""
    _write_json(REQUEST_FILE, {"minutes": int(minutes), "source": source, "at": time.time()})


def tick():
    """remote-debug.timer: apply a pending request; keep a session in force; expire it."""
    with _Lock(blocking=False) as got:
        if not got:
            return                            # a start/stop is in progress
        req = _read_json(REQUEST_FILE)
        _remove(REQUEST_FILE)
        state = load_state()
    if req:
        if int(req.get("minutes", 0)) > 0:
            start(req["minutes"], source=req.get("source", "request"))
        else:
            stop(reason=f"request from {req.get('source', '?')}")
        return
    if not state or not state.get("active"):
        return
    if time.time() >= state.get("until", 0):
        stop(reason="time is up")
        return
    if state.get("boot_id") != _boot_id():
        # rebooted mid-session: the boot re-armed the Witty Pi and started ticktalk
        log("rebooted during the session: suspending the schedule and ticktalk again")
        with _Lock():
            state = load_state()
            if state and state.get("active"):
                _establish(state)
        send_status(state)


def main(argv=None):
    p = argparse.ArgumentParser(description="Remote debug session (Tailscale over cellular)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="start, or extend the running session")
    s.add_argument("--minutes", type=int, default=DEFAULT_MINUTES)
    t = sub.add_parser("stop", help="restore normal operation")
    t.add_argument("--shutdown", action="store_true", help="power off instead of running a capture cycle")
    sub.add_parser("status")
    sub.add_parser("tick")
    args = p.parse_args(argv)
    if args.cmd == "start":
        start(args.minutes)
    elif args.cmd == "stop":
        stop(shutdown=args.shutdown)
    elif args.cmd == "tick":
        tick()
    else:
        st = load_state()
        if not st or not st.get("active"):
            print("No remote debug session.")
        else:
            left = max(0, int((st["until"] - time.time()) // 60))
            print(f"Session active: {left} min left (until {time.strftime('%H:%M', time.localtime(st['until']))}), "
                  f"started by {st.get('source')}, cellular {'up' if cellular_state(st.get('connection', 'Quectel'))[0] else 'down'}, "
                  f"tailscale {'online' if tailscale_online() else 'offline'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
