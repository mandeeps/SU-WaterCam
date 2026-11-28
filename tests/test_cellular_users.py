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
