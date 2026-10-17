"""tools/get_gps.py against a fake gpsd: per-request timeout and reconnecting."""
import json
import socket
import threading
import time

import pytest

from tools import get_gps

POLL_FIX = {"class": "POLL", "active": 1,
            "tpv": [{"mode": 3, "lat": 10.5, "lon": -20.25, "alt": 100.0, "time": "t"}],
            "sky": [{"satellites": [{"used": True}]}]}


class FakeGpsd:
    """Speaks enough of the gpsd protocol for py-gpsd2. hang=True: answers ?POLL never."""

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(4)
        self.port = self.srv.getsockname()[1]
        self.hang = False
        self.connections = 0
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def _client(self, conn):
        f = conn.makefile("rw")
        try:
            f.write(json.dumps({"class": "VERSION"}) + "\n"); f.flush()
            for line in f:
                if line.startswith("?WATCH"):
                    f.write(json.dumps({"class": "DEVICES", "devices": [{"path": "/dev/x", "bps": 1, "driver": "d"}]}) + "\n")
                    f.write(json.dumps({"class": "WATCH"}) + "\n"); f.flush()
                elif line.startswith("?POLL"):
                    if self.hang:
                        self.stop.wait(5)
                        return
                    f.write(json.dumps(POLL_FIX) + "\n"); f.flush()
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.srv.close()


@pytest.fixture
def fake(monkeypatch, tmp_path):
    g = FakeGpsd()
    monkeypatch.setattr(get_gps, "GPSD_PORT", g.port)
    monkeypatch.setattr(get_gps, "REQUEST_TIMEOUT_S", 0.5)
    monkeypatch.setattr(get_gps, "CACHE_PATH", str(tmp_path / "gps_last_fix.json"))
    monkeypatch.setattr(get_gps, "_cache_max_age_s", lambda: 48 * 3600)
    monkeypatch.setattr(get_gps.time, "sleep", lambda s: None)
    get_gps._disconnect()
    yield g
    get_gps._disconnect()
    g.close()


def test_live_fix_from_gpsd(fake):
    loc, packet = get_gps.get_location_with_retry()
    assert loc["gps_lat"] == 10.5 and packet is not None
    get_gps.get_location_with_retry()
    assert fake.connections == 1                 # the connection is kept


def test_hung_gpsd_times_out_then_reconnects_and_uses_the_cache(fake):
    get_gps.get_location_with_retry()            # a live fix, saved
    fake.hang = True
    start = time.monotonic()
    loc, packet = get_gps.get_location_with_retry(max_retries=2)
    assert time.monotonic() - start < 3          # two 0.5 s timeouts, not forever
    assert loc["gps_lat"] == 10.5 and packet is None and get_gps.last_fix["source"] == "cached"
    assert fake.connections >= 2                 # reconnected after the timeout
    fake.hang = False
    loc, packet = get_gps.get_location_with_retry()
    assert packet is not None and get_gps.last_fix["source"] == "live"


def test_no_gpsd_at_first_then_it_starts(monkeypatch, fake):
    real_port = fake.port
    free = socket.socket(); free.bind(("127.0.0.1", 0)); dead_port = free.getsockname()[1]; free.close()
    monkeypatch.setattr(get_gps, "GPSD_PORT", dead_port)
    assert get_gps.get_location_with_retry(use_cache=False) == ({}, None)
    assert get_gps.get_location() == []
    monkeypatch.setattr(get_gps, "GPSD_PORT", real_port)   # gpsd came up later
    loc, packet = get_gps.get_location_with_retry()
    assert loc["gps_lat"] == 10.5
