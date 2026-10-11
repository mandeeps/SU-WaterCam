"""config/wittypi/gps_time.py: GPS time for syncTime.sh when there's no network time.

At boot gpsd is often cold: it opens the GPS when the first client connects, and the
first fix can take many seconds. The old reader used a socket file with a 5 s timeout,
which becomes unusable after one timeout, so a slow first report meant no time at all.
"""
import os
import socket
import subprocess
import sys
import threading
import time

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "config", "wittypi", "gps_time.py")


def fake_gpsd(port_holder, lines_after_delay, delay_s, ready):
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port_holder.append(srv.getsockname()[1]); ready.set()
    conn, _ = srv.accept()
    conn.recv(1024)                                               # ?WATCH
    conn.sendall(b'{"class":"VERSION"}\n{"class":"DEVICES"}\n')
    time.sleep(delay_s)                                           # GPS still starting
    for line in lines_after_delay:
        conn.sendall(line + b"\n")
        time.sleep(0.2)
    time.sleep(1); conn.close(); srv.close()


def run(lines, delay_s, wait="20"):
    port, ready = [], threading.Event()
    t = threading.Thread(target=fake_gpsd, args=(port, lines, delay_s, ready), daemon=True); t.start()
    ready.wait(5)
    src = open(SCRIPT).read().replace('("127.0.0.1", 2947)', f'("127.0.0.1", {port[0]})')
    r = subprocess.run([sys.executable, "-c", src, wait], capture_output=True, text=True, timeout=40)
    return r.returncode, r.stdout.strip()


def test_time_after_a_slow_first_report():
    rc, out = run([b'{"class":"TPV","mode":3,"time":"2026-10-11T05:00:00.000Z"}'], delay_s=8)
    assert rc == 0 and out == "2026-10-11T05:00:00.000Z"


def test_reports_without_a_fix_are_skipped():
    rc, out = run([b'{"class":"TPV","mode":1,"time":"2000-01-01T00:00:00.000Z"}',
                   b'{"class":"TPV","mode":2,"time":"2026-10-11T05:00:01.000Z"}'], delay_s=0)
    assert rc == 0 and out == "2026-10-11T05:00:01.000Z"


def test_no_fix_before_the_deadline_gives_nothing():
    rc, out = run([b'{"class":"TPV","mode":1}'], delay_s=0, wait="3")
    assert rc == 1 and out == ""
