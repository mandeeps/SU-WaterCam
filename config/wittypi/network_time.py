#!/usr/bin/env python3
"""Print network time (Unix seconds) for syncTime.sh, or nothing if there's none to trust.

    network_time.py [--config runtime_config.json]

The stock Witty Pi script took the Date header of plain-HTTP google.com, with no
timeout and no check. On site WiFi that can be a captive portal's or an access
point's own clock, which syncTime.sh then wrote to the RTC and which, as "network
time", kept the server's, LoRa's and GPS's corrections out for the boot. Wifi
that is up but has no internet made each curl wait for the TCP timeout (about
2 min), with the next wake still unarmed when the RTC was bad (beforeScript.sh).

Now, in order, the first that answers:
1. our API server (ip_upload.server_url): only over https or the tailnet
   (an http *.ts.net name must resolve into the tailnet, as transmit_ip.trusted_server);
2. public https servers, certificate checked, for when the API server is down.
   A portal or access point can't answer for them.
Plain http outside the tailnet is never used. Each request has REQUEST_TIMEOUT_S,
the whole run TOTAL_TIMEOUT_S (name lookups included).

The answer is refused if it is before 2026 or after 2100, or more than
MAX_STEP_S (a day) from the clock while the RTC looks valid, the rule
tools/transmit_ip.py correct_clock and tools/lora_time.py apply. The RTC counts
as not valid when /run/watercam-rtc-untrusted exists (tools/rtc_early_clock.py,
or syncTime.sh after the Witty Pi daemon logged "RTC has bad time") or the clock
is before 2026. Exit status 0 with "UNIX_TIME SOURCE" on stdout, else 1 with the
reason on stderr.

Standard library only: it runs as root from the Witty Pi daemon, outside the venv.
"""
import ipaddress
import json
import os
import signal
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

CONFIG = "/home/pi/SU-WaterCam/runtime_config.json"
RTC_UNTRUSTED = "/run/watercam-rtc-untrusted"
PUBLIC = ("https://www.google.com", "https://www.cloudflare.com")
REQUEST_TIMEOUT_S = 8
TOTAL_TIMEOUT_S = 30
CLOCK_FLOOR = 1767225600       # 2026-01-01, as watercam-set-clock
CLOCK_CEILING = 4102444800     # 2100-01-01
MAX_STEP_S = 24 * 3600
TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def server_url(config_path):
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        url = (cfg.get("ip_upload") or {}).get("server_url") or ""
    except (OSError, ValueError, AttributeError):
        return ""
    return url.rstrip("/") if isinstance(url, str) else ""


def _in_tailnet(host):
    try:
        addrs = {i[4][0].split("%")[0] for i in socket.getaddrinfo(host, None)}
        return bool(addrs) and all(any(ipaddress.ip_address(a) in n for n in TAILNET) for a in addrs)
    except (OSError, ValueError):
        return False


def trusted(url):
    """https (the certificate is checked), or http that stays inside the tailnet."""
    p = urlparse(url or "")
    if p.scheme == "https":
        return True
    if p.scheme != "http" or not p.hostname:
        return False
    try:
        return any(ipaddress.ip_address(p.hostname) in n for n in TAILNET)
    except ValueError:
        return p.hostname.lower().endswith(".ts.net") and _in_tailnet(p.hostname)


def fetch_date(url, opener=urllib.request.urlopen, now=time.time):
    """The server's time from its Date header (any HTTP status), or None."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "watercam-clock"})
    t0 = now()
    try:
        resp = opener(req, timeout=REQUEST_TIMEOUT_S, context=ssl.create_default_context())
        headers = resp.headers
        resp.close()
    except urllib.error.HTTPError as e:      # a 404 or 405 still carries the server's Date
        headers = e.headers
    except (OSError, ValueError, ssl.SSLError):
        return None
    rtt = now() - t0
    try:
        # The header is the second the reply was made, floored: +0.5 s on average,
        # plus half the round trip for the reply's way back
        return parsedate_to_datetime(headers["Date"]).timestamp() + 0.5 + rtt / 2
    except (TypeError, ValueError, KeyError):
        return None


def check(ts, clock, rtc_untrusted):
    """None if `ts` may be applied, else why not."""
    if not CLOCK_FLOOR <= ts < CLOCK_CEILING:
        return f"network time {ts:.0f} is out of range"
    if abs(ts - clock) > MAX_STEP_S and not rtc_untrusted and clock >= CLOCK_FLOOR:
        return (f"network time is {abs(ts - clock) / 3600:.1f} h off the clock: more than a day, "
                "not applied (the RTC looks valid)")
    return None


def main(argv=None, fetch=fetch_date, is_trusted=trusted, now=time.time):
    argv = sys.argv[1:] if argv is None else argv
    config = argv[argv.index("--config") + 1] if "--config" in argv else CONFIG
    api = server_url(config)
    sources = ([api + "/health"] if api and is_trusted(api) else []) + list(PUBLIC)
    for url in sources:
        ts = fetch(url)
        if ts is None:
            continue
        why = check(ts, now(), os.path.exists(RTC_UNTRUSTED))
        if why:
            print(f"{why} ({urlparse(url).hostname})", file=sys.stderr)
            return 1
        name = "api" if api and url.startswith(api) else urlparse(url).hostname
        print(f"{ts:.3f} {name}")
        return 0
    print("no network time (API server and public https servers unreachable)", file=sys.stderr)
    return 1


def _give_up(signum, frame):
    print(f"no network time within {TOTAL_TIMEOUT_S} s", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, _give_up)
    signal.alarm(TOTAL_TIMEOUT_S)        # name lookups ignore the request timeouts
    sys.exit(main())
