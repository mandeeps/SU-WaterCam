"""The node reports its software release and loaded model (channels 01 09-01 0B)."""
import json
import subprocess

import pytest

from tools import node_version as nv


@pytest.mark.parametrize("text, release, ahead, dirty, code", [
    ("v2026.10.1", (2026, 10, 1), 0, False, 0x1A0A0100),
    ("v2026.10.1-3-gabc12345", (2026, 10, 1), 3, False, 0x1A0A0102),
    ("v2026.10.1-dirty", (2026, 10, 1), 0, True, 0x1A0A0101),
    ("v2026.10.1-3-gabc12345-dirty", (2026, 10, 1), 3, True, 0x1A0A0103),
    ("abc12345", None, 0, False, nv.FLAG_NO_TAG),
    ("abc12345-dirty", None, 0, True, nv.FLAG_NO_TAG | nv.FLAG_DIRTY),
])
def test_describe_is_parsed_and_packed(text, release, ahead, dirty, code):
    v = nv.parse_describe(text)
    assert (v["release"], v["ahead"], v["dirty"]) == (release, ahead, dirty)
    assert nv.encode_version_u32(v) == code


@pytest.mark.parametrize("text", ["", "junk", "v2026.10", "release-1"])
def test_unparseable_describe(text):
    assert nv.parse_describe(text) is None
    assert nv.encode_version_u32(None) is None


def test_out_of_range_release_is_sent_as_untagged():
    assert nv.encode_version_u32({"release": (2026, 10, 300), "ahead": 0, "dirty": False}) \
        == nv.FLAG_NO_TAG


def test_hex8():
    assert nv.hex8_u32("4ca62324ac11051e") == 0x4CA62324
    assert nv.hex8_u32("abc") is None
    assert nv.hex8_u32("zzzzzzzz") is None
    assert nv.hex8_u32(None) is None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (tmp_path / "a.txt").write_text("1")
    git("add", "a.txt")
    git("commit", "-qm", "one")
    monkeypatch.setattr(nv, "REPO", str(tmp_path))
    return tmp_path, git


def test_version_from_a_real_checkout(repo):
    path, git = repo
    assert nv.software_version(use_cache=False)["release"] is None   # no tag yet
    git("tag", "v2026.10.0")
    assert nv.software_version(use_cache=False)["describe"] == "v2026.10.0"
    (path / "a.txt").write_text("2")                                  # a local edit
    v = nv.software_version(use_cache=False)
    assert v["describe"] == "v2026.10.0-dirty" and v["dirty"]
    git("commit", "-qam", "two")
    v = nv.software_version(use_cache=False)
    assert v["ahead"] == 1 and not v["dirty"] and len(v["commit"]) == 8


def test_non_release_tags_are_ignored(repo):
    _, git = repo
    git("tag", "field-test")
    assert nv.software_version(use_cache=False)["release"] is None


def test_cache_is_per_boot(repo, tmp_path_factory, monkeypatch):
    path, git = repo
    cache = tmp_path_factory.mktemp("run") / "v.json"
    monkeypatch.setattr(nv, "CACHE_PATH", str(cache))
    git("tag", "v2026.10.0")
    assert nv.software_version()["describe"] == "v2026.10.0"
    (path / "a.txt").write_text("2")
    git("commit", "-qam", "two")
    git("tag", "v2026.10.1")
    assert nv.software_version()["describe"] == "v2026.10.0"          # same boot: cached
    monkeypatch.setattr(nv, "_boot_id", lambda: "another-boot")
    assert nv.software_version()["describe"] == "v2026.10.1"


def test_uplink_fields(repo, tmp_path_factory, monkeypatch):
    _, git = repo
    git("tag", "v2026.10.0")
    info = tmp_path_factory.mktemp("seg") / "model.json"
    info.write_text(json.dumps({"name": "m.onnx", "path": "/x/m.onnx", "sha256": "4ca62324" + "0" * 56}))
    monkeypatch.setattr(nv, "MODEL_INFO_PATH", str(info))
    monkeypatch.setattr(nv, "CACHE_PATH", str(tmp_path_factory.mktemp("run") / "v.json"))
    out = nv.uplink_fields()
    assert out["software_version"] == 0x1A0A0000
    assert out["model_id"] == 0x4CA62324
    assert set(out) == {"software_version", "software_commit", "model_id"}


def test_uplink_fields_without_git_or_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(nv, "REPO", str(tmp_path / "missing"))
    monkeypatch.setattr(nv, "MODEL_INFO_PATH", str(tmp_path / "none.json"))
    assert nv.uplink_fields() == {}


def test_lora_packet_ends_with_version_and_model():
    from tools.lora_handler_concurrent import _encode_compressed_packet
    packet = _encode_compressed_packet({"battery_percent": 44, "registration_status": 0,
                                        "software_version": 0x1A0A0100, "software_commit": 0xABC12345,
                                        "model_id": 0x4CA62324})
    assert packet.endswith(bytes([0x01, 0x08, 0x00, 0x01, 0x09, 0x1A, 0x0A, 0x01, 0x00,
                                  0x01, 0x0B, 0x4C, 0xA6, 0x23, 0x24]))
    assert bytes([0x01, 0x0A]) not in packet       # the commit is IP only


def test_tracker_sends_a_patch_release_as_a_change(tmp_path):
    from tools.sensor_changes import changed_fields, mark_sent
    state = str(tmp_path / "sent.json")
    mark_sent({"software_version": 0x1A0A0100, "model_id": 7}, state_path=state)
    out = changed_fields({"software_version": 0x1A0A0200, "model_id": 7}, state_path=state)
    assert set(out) == {"software_version"}


def test_status_reply_carries_the_version_after_em(monkeypatch):
    from tools import lora_debug_integration as ldi
    monkeypatch.setattr(ldi, "_software_version", lambda: "v2026.10.1-dirty")
    keys = [k for k, _ in ldi.collect_compact_status(emergency_mode=False)]
    assert keys[:3] == ["dbg", "em", "v"]


def test_daemon_records_the_loaded_model(tmp_path):
    pytest.importorskip("cv2")
    import hashlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import segformer_daemon
    model = tmp_path / "real.onnx"
    model.write_bytes(b"not really a model")
    link = tmp_path / "link.onnx"
    link.symlink_to(model)
    info = tmp_path / "model.json"
    segformer_daemon.record_model(str(link), str(info))
    got = json.loads(info.read_text())
    assert got["name"] == "real.onnx"
    assert got["sha256"] == hashlib.sha256(b"not really a model").hexdigest()
    segformer_daemon.record_model(str(tmp_path / "missing.onnx"), str(info))   # never raises
