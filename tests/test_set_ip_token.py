"""tools/set_ip_token.py: stores the token, keeps other settings, makes the file 0600."""
import json
import os
import stat
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools import config_io  # noqa: E402
from tools import set_ip_token  # noqa: E402

TOKEN = "wct_" + "A" * 43


def _config(tmp_path, mode=0o644):
    p = tmp_path / "runtime_config.json"
    p.write_text(json.dumps({"area_threshold": 15, "ip_upload": {"device_id": "watercam-005", "api_key": ""}}))
    os.chmod(p, mode)
    return p


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_stores_token_and_keeps_other_settings(tmp_path, capsys):
    p = _config(tmp_path)
    assert set_ip_token.main(["--config", str(p)], prompt=lambda _: f"  {TOKEN}\n") == 0
    cfg = json.loads(p.read_text())
    assert cfg["ip_upload"] == {"device_id": "watercam-005", "api_key": TOKEN}
    assert cfg["area_threshold"] == 15
    out = capsys.readouterr().out
    assert "watercam-005" in out and TOKEN[-4:] in out and TOKEN not in out


def test_file_becomes_owner_only_and_stays_so(tmp_path):
    p = _config(tmp_path, 0o644)
    set_ip_token.main(["--config", str(p)], prompt=lambda _: TOKEN)
    assert _mode(p) == 0o600
    config_io.write_json(str(p), dict(json.loads(p.read_text()), area_threshold=20))   # a later config write
    assert _mode(p) == 0o600


def test_rejects_non_tokens_without_touching_the_file(tmp_path, capsys):
    p = _config(tmp_path)
    before = p.read_text()
    for bad in ("", "not-a-token", "wct_short", "wct_" + "A" * 20 + " " + "A" * 20):
        assert set_ip_token.main(["--config", str(p)], prompt=lambda _, b=bad: b) == 1
    assert p.read_text() == before and _mode(p) == 0o644
    assert "Nothing changed" in capsys.readouterr().err


def test_missing_config_is_refused(tmp_path):
    assert set_ip_token.main(["--config", str(tmp_path / "nope.json")], prompt=lambda _: TOKEN) == 1
    assert not (tmp_path / "nope.json").exists()
