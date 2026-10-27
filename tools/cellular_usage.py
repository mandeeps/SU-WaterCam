#!/home/pi/SU-WaterCam/venv/bin/python
"""Count the bytes this unit sends and receives over cellular, and report the total daily.

The SIMs' data allowances run out (1NCE: 500 MB once, not monthly), so the
server tracks each unit's use against its plan (documentation
CELLULAR_DATA_TRACKING_PLAN.md). The node knows nothing about plans: it keeps
one ever-growing total and reports it once a day. The server works out what that
means for the SIM's plan.

Counting. The kernel counts each interface's bytes in
/sys/class/net/<iface>/statistics, but only for as long as the interface
exists. The modem's interface (wwan*) is created when USB power comes on and
removed when it goes off, and every wake is a fresh boot, so those counters
start from zero again and again. sample() adds what is new since its last look
to a running total kept in data/cellular_usage.json:

- same boot, same interface (ifindex), counters not lower: add the difference;
- anything else is a new interface: add its counters in full.

Running it twice adds nothing the second time, and a reset is never subtracted,
so it can be called from anywhere at any time. It is called:

- every 5 minutes while the modem is up, and once more when it goes
  (watercam-cellular-usage.service, bound to wwan0 like the cellular filter;
  stopping it at shutdown samples too);
- by NetworkManager when the cellular connection goes up or down
  (config/networkmanager/dispatcher.d/90-watercam-cellular-usage);
- just before USB power goes off, which removes the interface
  (tools/usb_power.release);
- before the daily report.

Bytes used after the last sample before a power cut are lost: at most 5 minutes'
worth. The kernel doesn't count what the network adds, so the figure reads a
little low against the carrier's bill; the server measures the gap.

Reporting. daily_report_kib() gives the total in KiB for the uplink
(cellular_kib_total, channel 01 11, LoRa and IP) on the first wake of each day,
and None on the others. Every uplink of that one wake carries it. It never
causes a connection: it rides uplinks that are going out anyway, mostly LoRa. A
unit that has never had a modem interface reports nothing.

    python -m tools.cellular_usage show
    python -m tools.cellular_usage sample
    python -m tools.cellular_usage watch [--interval 300]      (the service)
"""
import argparse
import datetime
import glob
import os
import signal
import sys
import time
from typing import Dict, Optional, Tuple

try:
    from tools import config_io
except ImportError:                      # run as a script from tools/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools import config_io

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
STATE_PATH = os.path.join(_REPO_ROOT, "data", "cellular_usage.json")
SYS_NET = "/sys/class/net"
#: The modem's interfaces (qmi_wwan / cdc_mbim). WiFi and Tailscale are never counted.
IFACE_PATTERN = "wwan*"
WATCH_INTERVAL_S = 300


def read_counters(sys_net: str = SYS_NET) -> Dict[str, Tuple[int, int, int]]:
    """{iface: (ifindex, rx_bytes, tx_bytes)} for the modem interfaces that exist now."""
    out = {}
    for path in sorted(glob.glob(os.path.join(sys_net, IFACE_PATTERN))):
        try:
            values = []
            for name in ("ifindex", "statistics/rx_bytes", "statistics/tx_bytes"):
                with open(os.path.join(path, name)) as f:
                    values.append(int(f.read().strip()))
        except (OSError, ValueError):
            continue                     # the interface went away mid-read
        out[os.path.basename(path)] = tuple(values)
    return out


def _read(path: str) -> dict:
    try:
        data = config_io.read_json(path)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, config_io.ConfigUnreadable):
        return {}


def _add_new_bytes(state: dict, counters: Dict[str, Tuple[int, int, int]], boot_id: Optional[str]) -> int:
    """Add what the counters gained since the stored sessions; returns the bytes added."""
    sessions = state.setdefault("sessions", {})
    added = 0
    for iface, (ifindex, rx, tx) in counters.items():
        prev = sessions.get(iface) or {}
        same = (prev.get("boot_id") == boot_id and prev.get("ifindex") == ifindex
                and rx >= prev.get("rx", 0) and tx >= prev.get("tx", 0))
        if same:
            added += (rx - prev["rx"]) + (tx - prev["tx"])
        else:
            added += rx + tx             # a new interface: everything it counted is new
        sessions[iface] = {"boot_id": boot_id, "ifindex": ifindex, "rx": rx, "tx": tx}
    if counters and "since" not in state:
        state["since"] = datetime.date.today().isoformat()
    state["total_bytes"] = int(state.get("total_bytes", 0)) + added
    return added


def sample(state_path: Optional[str] = None, sys_net: str = SYS_NET,
           boot_id: Optional[str] = None) -> Optional[int]:
    """Add the new cellular bytes to the running total; returns the total, or None
    on a unit that has never had a modem interface. Never raises."""
    path = state_path or STATE_PATH
    boot_id = boot_id or config_io.current_boot_id()
    try:
        counters = read_counters(sys_net)
        with config_io.locked(path):
            state = _read(path)
            if not counters and "since" not in state:
                return None
            if _add_new_bytes(state, counters, boot_id) or counters:
                config_io.write_json(path, state, mode=0o644)
            return int(state["total_bytes"])
    except Exception as exc:             # noqa: BLE001 - counting must never break a caller
        print(f"⚠️ cellular usage sample failed: {exc}")
        return None


def daily_report_kib(state_path: Optional[str] = None, sys_net: str = SYS_NET,
                     boot_id: Optional[str] = None,
                     today: Optional[datetime.date] = None) -> Optional[int]:
    """cellular_kib_total for this wake's uplinks, or None.

    The first wake of each local day reports, and every uplink of that wake
    carries it (LoRa and IP alike). Later wakes that day report nothing.
    """
    path = state_path or STATE_PATH
    boot_id = boot_id or config_io.current_boot_id()
    day = (today or datetime.date.today()).isoformat()
    total = sample(path, sys_net, boot_id)
    if total is None:
        return None
    try:
        with config_io.locked(path):
            state = _read(path)
            reported = state.get("reported") or {}
            if reported.get("day") == day and reported.get("boot_id") != boot_id:
                return None              # an earlier wake today already reported
            if reported != {"day": day, "boot_id": boot_id}:
                state["reported"] = {"day": day, "boot_id": boot_id}
                config_io.write_json(path, state, mode=0o644)
    except Exception as exc:             # noqa: BLE001
        print(f"⚠️ cellular usage report state not saved: {exc}")
    return min(total // 1024, 0xFFFFFFFF)


def watch(interval_s: int = WATCH_INTERVAL_S, state_path: Optional[str] = None) -> int:
    """Sample every interval_s until stopped; sample once more on the way out."""
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))
    while not stop:
        sample(state_path)
        for _ in range(interval_s):
            if stop:
                break
            time.sleep(1)
    sample(state_path)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cellular data counter (see the module doc).")
    p.add_argument("command", choices=("show", "sample", "watch"))
    p.add_argument("--interval", type=int, default=WATCH_INTERVAL_S)
    args = p.parse_args(argv)
    if args.command == "watch":
        return watch(max(10, args.interval))
    if args.command == "sample":
        sample()
    state = _read(STATE_PATH)
    if "total_bytes" not in state:
        print("no cellular use counted (no modem interface seen)")
        return 0
    print(f"{state['total_bytes'] / 1e6:.2f} MB since {state.get('since', '?')}"
          f" (last reported {(state.get('reported') or {}).get('day', 'never')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
