#!/usr/bin/env python3
"""Print the current UTC time from gpsd (ISO 8601), or nothing if there's no fix.

syncTime.sh falls back to this when there's no network time: with the cellular
modem kept off, a unit without WiFi would otherwise never correct its clock.
The GPS comes from the modem's GNSS, which works with its data connection down.

    gps_time.py [seconds to wait, default 20]

Standard library only: it runs as root from the Witty Pi daemon, outside the venv.
"""
import json
import socket
import sys
import time


def main():
    wait = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    deadline = time.monotonic() + wait
    try:
        sock = socket.create_connection(("127.0.0.1", 2947), timeout=5)
    except OSError:
        return 1
    with sock:
        # Read the socket directly. A timed-out read on a socket *file* (makefile())
        # leaves it unusable, so when gpsd was cold and the GPS took more than the
        # timeout to send its first report (normal right after boot), the old loop
        # could never read again and returned no time.
        sock.settimeout(1.0)
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue                 # gpsd quiet: keep waiting until the deadline
            except OSError:
                return 1
            if not chunk:
                return 1                 # gpsd closed the connection
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                # mode 2/3 = 2D/3D fix; earlier reports can carry a guessed time
                if msg.get("class") == "TPV" and msg.get("mode", 0) >= 2 and msg.get("time"):
                    print(msg["time"])
                    return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
