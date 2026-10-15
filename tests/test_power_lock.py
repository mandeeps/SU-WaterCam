"""tools/lock_utils.py power_exclusive / PowerHold: heavy steps and cellular take turns."""
import multiprocessing as mp
import time

from tools import lock_utils


def _hold(path, seconds, started):
    lock_utils.POWER_LOCK = path
    with lock_utils.power_exclusive("holder", 5):
        started.set()
        time.sleep(seconds)


def test_second_step_waits_for_the_first(tmp_path, monkeypatch):
    path = str(tmp_path / "power.lock")
    monkeypatch.setattr(lock_utils, "POWER_LOCK", path)
    started = mp.Event()
    p = mp.Process(target=_hold, args=(path, 1.5, started))
    p.start()
    assert started.wait(5)
    t0 = time.monotonic()
    with lock_utils.power_exclusive("segmentation", 10):
        waited = time.monotonic() - t0
    p.join(5)
    assert 1.0 <= waited < 5


def test_goes_ahead_after_the_timeout(tmp_path, monkeypatch, capsys):
    path = str(tmp_path / "power.lock")
    monkeypatch.setattr(lock_utils, "POWER_LOCK", path)
    started = mp.Event()
    p = mp.Process(target=_hold, args=(path, 4, started))
    p.start()
    assert started.wait(5)
    t0 = time.monotonic()
    ran = False
    with lock_utils.power_exclusive("segmentation", 1):
        ran = True
    assert ran and time.monotonic() - t0 < 3
    assert "going ahead" in capsys.readouterr().out
    p.join(6)


def test_power_hold_acquires_once_and_releases(tmp_path, monkeypatch):
    path = str(tmp_path / "power.lock")
    monkeypatch.setattr(lock_utils, "POWER_LOCK", path)
    hold = lock_utils.PowerHold()
    hold.acquire("cellular upload", 1)
    hold.acquire("cellular upload", 1)          # second call is a no-op, not a self-deadlock
    hold.release()
    hold.release()                              # idempotent
    with lock_utils.power_exclusive("segmentation", 1):
        pass                                    # free again
