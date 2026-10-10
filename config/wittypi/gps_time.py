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
        sock.settimeout(max(1.0, min(5.0, wait)))
        sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
        stream = sock.makefile()
        while time.monotonic() < deadline:
            try:
                line = stream.readline()
            except OSError:          # socket timeout: gpsd quiet for a while
                continue
            if not line:
                return 1
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
