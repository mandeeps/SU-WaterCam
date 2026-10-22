"""Tell systemd the capture graph is still making progress (SU #208).

ticktalk.service sets WatchdogSec: if no heartbeat arrives for that long, systemd
kills ticktalk and Restart=on-failure starts it again. call_shutdown() runs once
per capture iteration and sends the heartbeat, so a graph that stops making
progress (a dead persistent worker such as lora_listener, a wedged read) is
restarted instead of sitting there while systemd sees a healthy process. The graph
runs in child processes, hence NotifyAccess=all in the unit.

No dependency on python-systemd: it is one datagram to $NOTIFY_SOCKET.
"""
import os
import socket
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Touched by every capture iteration (call_shutdown). The capture interval is
# 10-240 min in emergency mode and holds (tools/photo_interval_watcher.py), longer
# than WatchdogSec, so that watcher sends the heartbeats between iterations while
# the last one is recent enough for the current period.
PROGRESS_FILE = os.path.join(REPO, "data", "last_iteration")


def note_progress(path: str = PROGRESS_FILE) -> None:
    """Record that a capture iteration just ran."""
    try:
        with open(path, "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def last_progress(path: str = PROGRESS_FILE):
    """Epoch time of the last capture iteration, or None if none was recorded."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def heartbeat() -> bool:
    """Send WATCHDOG=1; False when not run by systemd or the send fails."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):            # abstract socket
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(b"WATCHDOG=1")
        return True
    except OSError:
        return False
