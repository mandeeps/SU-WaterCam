"""tools/debug_log.py: debug output only with the flag file or WATERCAM_DEBUG=1."""
from tools import debug_log


def test_off_without_flag_or_env(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "DEBUG_FLAG", str(tmp_path / "debug-logs"))
    monkeypatch.delenv("WATERCAM_DEBUG", raising=False)
    assert debug_log._enabled() is False


def test_on_with_flag_file(tmp_path, monkeypatch):
    flag = tmp_path / "debug-logs"
    flag.touch()
    monkeypatch.setattr(debug_log, "DEBUG_FLAG", str(flag))
    monkeypatch.delenv("WATERCAM_DEBUG", raising=False)
    assert debug_log._enabled() is True


def test_on_with_env(tmp_path, monkeypatch):
    monkeypatch.setattr(debug_log, "DEBUG_FLAG", str(tmp_path / "missing"))
    monkeypatch.setenv("WATERCAM_DEBUG", "1")
    assert debug_log._enabled() is True


def test_debug_prints_only_when_enabled(monkeypatch, capsys):
    monkeypatch.setattr(debug_log, "ENABLED", False)
    debug_log.debug("hidden")
    monkeypatch.setattr(debug_log, "ENABLED", True)
    debug_log.debug("shown")
    assert capsys.readouterr().out == "shown\n"
