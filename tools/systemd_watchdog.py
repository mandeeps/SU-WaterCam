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
