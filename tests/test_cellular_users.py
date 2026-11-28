"""tools/lock_utils.CellularUsers: the last upload out takes cellular down (#337)."""
from tools.lock_utils import CellularUsers


def _pair(tmp_path):
    kw = dict(lock_path=str(tmp_path / "users.lock"), owed_path=str(tmp_path / "owed"))
    return CellularUsers(**kw), CellularUsers(**kw), tmp_path / "owed"


def test_alone_the_starter_takes_it_down(tmp_path):
    a, _, _ = _pair(tmp_path)
    a.join()
    assert a.leave(started=True) is True


def test_alone_a_non_starter_leaves_it(tmp_path):
    a, _, _ = _pair(tmp_path)
    a.join()
    assert a.leave(started=False) is False


def test_starter_finishing_first_hands_the_teardown_to_the_last(tmp_path):
    a, b, owed = _pair(tmp_path)
    a.join(); b.join()
    assert a.leave(started=True) is False and owed.exists()
    assert b.leave(started=False) is True and not owed.exists()


def test_starter_finishing_last_takes_it_down(tmp_path):
    a, b, owed = _pair(tmp_path)
    a.join(); b.join()
    assert b.leave(started=False) is False and not owed.exists()
    assert a.leave(started=True) is True


def test_without_join_falls_back_to_started(tmp_path):
    a, _, _ = _pair(tmp_path)
    assert a.leave(started=True) is True
    assert a.leave(started=False) is False


def test_unusable_lock_path_falls_back(tmp_path):
    a = CellularUsers(lock_path=str(tmp_path / "missing" / "x.lock"), owed_path=str(tmp_path / "o"))
    a.join()
    assert a.leave(started=True) is True


def test_simultaneous_finish_still_takes_it_down(tmp_path):
    # Both let go; the non-starter wins the exclusive lock before the starter
    # tries it. The starter's marker is already there, so the winner tears down.
    import fcntl
    a, b, owed = _pair(tmp_path)
    a.join(); b.join()
    b._mark_owed()                       # what b.leave(started=True) does first
    fcntl.flock(b._fd, fcntl.LOCK_UN)
    assert a.leave(started=False) is True and not owed.exists()
    try:
        fcntl.flock(b._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        import os; os.close(b._fd); b._fd = None


def test_without_join_a_requested_teardown_is_still_honoured(tmp_path):
    a = CellularUsers(lock_path=str(tmp_path / "missing" / "x.lock"), owed_path=str(tmp_path / "o"))
    a.join()                              # can't open the lock: no coordination
    a.request_teardown()
    assert a.leave(started=False) is True
    assert not (tmp_path / "o").exists()


def test_teardown_runs_while_the_lock_is_held(tmp_path):
    import fcntl, os
    a, _, _ = _pair(tmp_path)
    a.join()
    seen = []

    def teardown():
        fd = os.open(a.lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            seen.append("joinable")
        except OSError:
            seen.append("held")
        finally:
            os.close(fd)
    assert a.leave(started=True, teardown=teardown) is True
    assert seen == ["held"]


def test_no_teardown_call_when_another_upload_runs(tmp_path):
    a, b, owed = _pair(tmp_path)
    a.join(); b.join()
    calls = []
    assert a.leave(started=True, teardown=lambda: calls.append("a")) is False
    assert calls == [] and owed.exists()
    assert b.leave(started=False, teardown=lambda: calls.append("b")) is True
    assert calls == ["b"]


def test_read_only_lock_file_still_coordinates(tmp_path):
    import os
    lock = tmp_path / "users.lock"
    lock.write_text("")
    os.chmod(lock, 0o444)
    a, b, owed = _pair(tmp_path)
    a.join(); b.join()
    assert a._fd is not None and b._fd is not None
    assert a.leave(started=True) is False and owed.exists()
    assert b.leave(started=False) is True
