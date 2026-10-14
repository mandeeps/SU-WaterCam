"""tools/data_retention.py: photos past the retention age, then the oldest when space is low."""
import os
from collections import namedtuple

import pytest

from tools import data_retention as dr

DAY = 86400
NOW = 2_000_000_000


@pytest.fixture
def images(tmp_path, monkeypatch):
    root = tmp_path / "images"
    root.mkdir()
    params = {"data_retention_days": 7, "storage_low_free_pct": 20, "storage_target_free_pct": 30}
    monkeypatch.setattr(dr, "_param", lambda name, default: params.get(name, default))
    return root, params


def capture(root, name, age_days, files=("RGB.jpg", "color_preserved_5_band.tiff", "mask.png", "meta.json")):
    d = root / name
    d.mkdir()
    for f in files:
        p = d / f
        p.write_bytes(b"x" * 1000)
        t = NOW - age_days * DAY
        os.utime(p, (t, t))
    return d


def test_photos_past_the_retention_age_go_others_stay(images):
    root, _ = images
    old = capture(root, "20260901-090000", 30)
    new = capture(root, "20261012-090000", 1)
    out = dr.run(root=str(root), now=NOW)
    assert out["aged"] == 2
    assert sorted(os.listdir(old)) == ["mask.png", "meta.json"]          # masks and metadata kept
    assert len(os.listdir(new)) == 4


def test_current_capture_never_touched(images):
    root, _ = images
    cur = capture(root, "20260901-090000", 30)
    dr.run(current_dir=str(cur), root=str(root), now=NOW)
    assert len(os.listdir(cur)) == 4


def test_low_space_removes_oldest_until_target(images, monkeypatch):
    root, params = images
    params["data_retention_days"] = 0          # age rule off; only space matters
    caps = [capture(root, f"2026100{i}-090000", 10 - i) for i in range(1, 6)]
    free = {"pct": 15.0}
    real_remove = os.remove

    def remove(p):
        real_remove(p)
        free["pct"] += 2.0                      # each photo frees 2 %
    monkeypatch.setattr(dr.os, "remove", remove)
    monkeypatch.setattr(dr, "free_pct", lambda path: free["pct"])
    out = dr.run(root=str(root), now=NOW)
    assert out["for_space"] == 8 and free["pct"] >= 30     # 15 % -> 31 %
    assert not any(f.endswith((".jpg", ".tiff")) for f in os.listdir(caps[0]))   # oldest went first
    assert len(os.listdir(caps[-1])) == 4                                           # newest kept


def test_enough_space_removes_nothing_for_space(images, monkeypatch):
    root, params = images
    params["data_retention_days"] = 0
    capture(root, "20261001-090000", 3)
    monkeypatch.setattr(dr, "free_pct", lambda path: 21.0)
    assert dr.run(root=str(root), now=NOW)["for_space"] == 0


def test_missing_images_dir_is_fine(tmp_path):
    assert dr.run(root=str(tmp_path / "nope"))["aged"] == 0


def test_default_keeps_everything_but_space_rule_still_works(images, monkeypatch):
    root, params = images
    del params["data_retention_days"]            # the default
    assert dr.DEFAULT_DAYS == 0
    old = capture(root, "20250101-090000", 400)
    assert dr.run(root=str(root), now=NOW)["aged"] == 0 and len(os.listdir(old)) == 4
    monkeypatch.setattr(dr, "free_pct", lambda path: 10.0)        # card nearly full
    assert dr.run(root=str(root), now=NOW)["for_space"] == 2


def test_audio_zero_retention_keeps_recordings(monkeypatch):
    from tools import audio_recorder
    called = []
    monkeypatch.setattr(audio_recorder.AudioRecorder, "_cleanup_old_segments", lambda self, d: called.append(d))
    monkeypatch.setattr(audio_recorder, "_find_usb_mic_device", lambda: None)
    audio_recorder.AudioRecorder(retention_days=0)
    audio_recorder.AudioRecorder(retention_days=30)
    assert called == [30]
