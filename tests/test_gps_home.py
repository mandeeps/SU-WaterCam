"""tools/gps_home.py: learn the mount position, flag a unit that moved away."""
import itertools

import pytest

from tools import get_gps, gps_home

HOME = {"gps_lat": 10.0, "gps_lon": 20.0, "gps_alt": 5.0}
FAR = {"gps_lat": 10.01, "gps_lon": 20.0, "gps_alt": 5.0}     # about 1.1 km north
NEAR = {"gps_lat": 10.0001, "gps_lon": 20.0, "gps_alt": 5.0}  # about 11 m


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(gps_home, "HOME_PATH", str(tmp_path / "gps_home.json"))
    monkeypatch.setattr(gps_home, "_alert_m", lambda: 50.0)
    clock = itertools.count(1_000_000, 60)          # each call one minute later
    monkeypatch.setattr(gps_home.time, "time", lambda: next(clock))
    return gps_home


def learn(h):
    for _ in range(3):
        h.check_movement(HOME, live=True, error_m=5)


def test_distance():
    assert gps_home.distance_m(10, 20, 10.01, 20) == pytest.approx(1112, rel=0.01)


def test_home_learned_from_three_agreeing_fixes(home):
    home.check_movement(HOME, live=True, error_m=5)
    home.check_movement(NEAR, live=True, error_m=5)
    assert not home.load().get("home")
    home.check_movement(HOME, live=True, error_m=5)
    assert home.load()["home"] == pytest.approx([10.0000333, 20.0], abs=1e-6)


def test_scattered_fixes_dont_become_home(home):
    for loc in (HOME, FAR, HOME):
        home.check_movement(loc, live=True, error_m=5)
    assert not home.load().get("home")


def test_two_far_fixes_set_moved_and_two_near_clear_it(home):
    learn(home)
    assert home.check_movement(FAR, live=True)["moved"] is False     # one could be a glitch
    r = home.check_movement(FAR, live=True)
    assert r["moved"] is True and r["distance_m"] == pytest.approx(1112, rel=0.01)
    assert home.check_movement(HOME, live=True)["moved"] is True
    assert home.check_movement(HOME, live=True)["moved"] is False


def test_reads_seconds_apart_count_once(home, monkeypatch):
    learn(home)
    monkeypatch.setattr(gps_home.time, "time", lambda: 5_000_000)   # same instant
    home.check_movement(FAR, live=True)
    assert home.check_movement(FAR, live=True)["moved"] is False


def test_cached_fix_keeps_the_state(home):
    learn(home)
    home.check_movement(FAR, live=True)
    home.check_movement(FAR, live=True)
    assert home.check_movement(HOME, live=False)["moved"] is True     # cached: no change
    assert home.check_movement({}, live=False)["moved"] is True       # no position at all


def test_large_fix_error_widens_the_limit(home):
    learn(home)
    for _ in range(2):
        r = home.check_movement(FAR, live=True, error_m=500)
    assert r["moved"] is False


def test_small_offset_is_not_movement(home):
    learn(home)
    for _ in range(3):
        assert home.check_movement(NEAR, live=True)["moved"] is False


def test_movement_fields(home, monkeypatch):
    learn(home)
    monkeypatch.setattr(get_gps, "last_fix", {"source": "live", "age_s": 0, "error_m": None})
    fields, _ = home.movement_fields(FAR)
    assert fields == {"movement_threshold": 0}
    fields, info = home.movement_fields(FAR)
    assert fields == {"movement_threshold": 1, "lat_lon_z": [10.01, 20.0, 5.0]}
    monkeypatch.setattr(get_gps, "last_fix", {"source": "cached", "age_s": 600, "error_m": None})
    fields, _ = home.movement_fields(FAR)                                # cached fix still alerts
    assert fields["movement_threshold"] == 1 and fields["lat_lon_z"][0] == 10.01


def test_reset(home, capsys):
    learn(home)
    assert home.main(["--reset"]) == 0
    assert home.load() == {}


@pytest.mark.parametrize("error_m", [None, 16, 200, -1, True])
def test_fixes_without_a_small_reported_error_are_not_candidates(home, error_m):
    for _ in range(5):
        home.check_movement(HOME, live=True, error_m=error_m)
    state = home.load()
    assert not state.get("home") and not state.get("candidates")


def test_a_fix_exactly_at_the_error_limit_counts(home):
    for _ in range(3):
        home.check_movement(HOME, live=True, error_m=home.HOME_MAX_ERROR_M)
    assert home.load().get("home")


def test_candidates_must_be_a_gap_apart(home, monkeypatch):
    t = [2_000_000]
    monkeypatch.setattr(home.time, "time", lambda: t[0])
    for _ in range(5):                  # five reads in the same wake
        t[0] += 2
        home.check_movement(HOME, live=True, error_m=5)
    assert len(home.load()["candidates"]) == 1 and not home.load().get("home")
    for _ in range(2):                  # later wakes
        t[0] += home.HOME_GAP_S
        home.check_movement(HOME, live=True, error_m=5)
    assert home.load().get("home")
