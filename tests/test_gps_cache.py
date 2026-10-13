"""tools/get_gps.py: the last live fix is saved and used when there's no live one."""
import json
import time

import pytest

from tools import get_gps

FIX = {"gps_lat": 10.5, "gps_lon": -20.25, "gps_alt": 100.0}


@pytest.fixture
def gps(tmp_path, monkeypatch):
    monkeypatch.setattr(get_gps, "CACHE_PATH", str(tmp_path / "gps_last_fix.json"))
    monkeypatch.setattr(get_gps, "_gps_available", True)
    monkeypatch.setattr(get_gps.time, "sleep", lambda s: None)
    monkeypatch.setattr(get_gps, "_cache_max_age_s", lambda: 48 * 3600)
    return get_gps


def test_live_fix_is_returned_and_saved(gps, monkeypatch):
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: (dict(FIX), "packet"))
    assert gps.get_location_with_retry() == (FIX, "packet")
    saved = json.load(open(gps.CACHE_PATH))
    assert saved["gps_lat"] == 10.5 and saved["gps_lon"] == -20.25 and saved["fix_time"] > 0


def test_no_live_fix_uses_the_cache(gps, monkeypatch):
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: (dict(FIX), "packet"))
    gps.get_location_with_retry()
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: ({}, None))
    assert gps.get_location_with_retry() == (FIX, None)
    assert gps.get_location_with_retry(use_cache=False) == ({}, None)


def test_gps_not_active_is_caught(gps, monkeypatch):
    def not_active():
        raise UserWarning("GPS not active")
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", not_active)
    assert gps.get_location_with_retry() == ({}, None)          # no cache yet, no exception
    json.dump({**FIX, "fix_time": time.time()}, open(gps.CACHE_PATH, "w"))
    assert gps.get_location_with_retry() == (FIX, None)


def test_old_cache_is_ignored(gps, monkeypatch):
    json.dump({**FIX, "fix_time": time.time() - 49 * 3600}, open(gps.CACHE_PATH, "w"))
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: ({}, None))
    assert gps.get_location_with_retry() == ({}, None)


def test_zero_max_age_turns_the_cache_off(gps, monkeypatch):
    json.dump({**FIX, "fix_time": time.time()}, open(gps.CACHE_PATH, "w"))
    monkeypatch.setattr(gps, "_cache_max_age_s", lambda: 0)
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: ({}, None))
    assert gps.get_location_with_retry() == ({}, None)


def test_cache_rewritten_only_when_moved_or_stale(gps, monkeypatch):
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: (dict(FIX), "p"))
    gps.get_location_with_retry()
    first = json.load(open(gps.CACHE_PATH))["fix_time"]
    gps.get_location_with_retry()                                    # same place, fresh: kept
    assert json.load(open(gps.CACHE_PATH))["fix_time"] == first
    moved = dict(FIX, gps_lat=10.6)
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: (moved, "p"))
    gps.get_location_with_retry()
    assert json.load(open(gps.CACHE_PATH))["gps_lat"] == 10.6


def test_corrupt_cache_is_ignored(gps, monkeypatch):
    open(gps.CACHE_PATH, "w").write("{not json")
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: ({}, None))
    assert gps.get_location_with_retry() == ({}, None)


def test_last_fix_records_the_source(gps, monkeypatch):
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: (dict(FIX), "packet"))
    gps.get_location_with_retry()
    assert gps.last_fix["source"] == "live" and gps.last_fix["age_s"] == 0
    monkeypatch.setattr(gps, "_get_lat_lon_alt_with_packet", lambda: ({}, None))
    gps.get_location_with_retry()
    assert gps.last_fix["source"] == "cached" and gps.last_fix["age_s"] >= 0
    assert gps.get_location_with_retry(use_cache=False) == ({}, None)
    assert gps.last_fix["source"] == "none"


def test_default_cache_is_two_days():
    assert get_gps.DEFAULT_CACHE_MAX_AGE_H == 48
