"""tools/debug_log.py: debug output only with "debug_logs": true or WATERCAM_DEBUG=1."""
import json

from tools import debug_log


def _config(tmp_path, data):
    p = tmp_path / "runtime_config.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("WATERCAM_DEBUG", raising=False)
    assert debug_log._enabled(str(tmp_path / "missing.json")) is False
    assert debug_log._enabled(_config(tmp_path, {"emergency_mode": False})) is False


def test_on_with_runtime_config_key(tmp_path, monkeypatch):
    monkeypatch.delenv("WATERCAM_DEBUG", raising=False)
    assert debug_log._enabled(_config(tmp_path, {"debug_logs": True})) is True
    assert debug_log._enabled(_config(tmp_path, {"debug_logs": "yes"})) is False   # only true


def test_on_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WATERCAM_DEBUG", "1")
    assert debug_log._enabled(str(tmp_path / "missing.json")) is True


def test_damaged_config_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("WATERCAM_DEBUG", raising=False)
    p = tmp_path / "runtime_config.json"
    p.write_text("{not json")
    assert debug_log._enabled(str(p)) is False


def test_debug_prints_only_when_enabled(monkeypatch, capsys):
    monkeypatch.setattr(debug_log, "ENABLED", False)
    debug_log.debug("hidden")
    monkeypatch.setattr(debug_log, "ENABLED", True)
    debug_log.debug("shown")
    assert capsys.readouterr().out == "shown\n"
