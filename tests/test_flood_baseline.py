"""tools/flood_baseline.py: water % of the mask, the baseline tool and the flood flags."""
import json
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from tools import flood_baseline as fb
from tools.lora_handler_concurrent import _encode_compressed_packet


def _capture(root, name, water_fraction):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    arr = np.zeros((100, 100), np.uint8)
    arr[: int(round(water_fraction * 100))] = 255
    Image.fromarray(arr, mode="L").save(str(d / fb.MASK_NAMES[0]))
    return str(d)


def _params(**over):
    p = {"camera_flood_detection_enabled": False, "area_threshold": 10}
    p.update(over)
    return lambda k, d=None: p.get(k, d)


def _fields(dirname, state, **params):
    with patch("tools.lora_runtime_integration.get_parameter", side_effect=_params(**params)):
        return fb.flood_fields(dirname, state_path=str(state))


def test_mask_names_match_coreg_multiple():
    cm = pytest.importorskip("tools.coreg_multiple")
    assert fb.MASK_NAMES == (cm.config.SEGMENTATION_PNG, cm.config.LEGACY_SEGMENTATION_PNG)


def test_water_pct_sent_with_detection_off(tmp_path):
    d = _capture(tmp_path, "20261015-100000", 0.3)
    assert _fields(d, tmp_path / "s.json") == {"camera_water_pct": 30}


def test_no_mask_no_fields(tmp_path):
    (tmp_path / "20261015-100000").mkdir()
    assert _fields(str(tmp_path / "20261015-100000"), tmp_path / "s.json") == {}
    assert _fields(None, tmp_path / "s.json") == {}


def test_colour_mask_gives_no_water_pct(tmp_path):
    d = tmp_path / "20261015-100000"
    d.mkdir()
    Image.fromarray(np.zeros((8, 8, 3), np.uint8), mode="RGB").save(str(d / fb.MASK_NAMES[0]))
    assert _fields(str(d), tmp_path / "s.json") == {}


def test_detection_on_without_baseline_sends_no_flags(tmp_path):
    d = _capture(tmp_path, "20261015-100000", 0.9)
    assert _fields(d, tmp_path / "s.json", camera_flood_detection_enabled=True) == {"camera_water_pct": 90}


def test_flood_detected_needs_two_captures_in_a_row(tmp_path):
    s = tmp_path / "s.json"
    on = dict(camera_flood_detection_enabled=True, flood_baseline_water_pct=20)
    out = _fields(_capture(tmp_path, "20261015-100000", 0.29), s, **on)
    assert out["camera_flood_detected"] == 0
    out = _fields(_capture(tmp_path, "20261015-110000", 0.30), s, **on)
    assert out["camera_flood_detected"] == 0              # over once: not yet
    out = _fields(_capture(tmp_path, "20261015-120000", 0.35), s, **on)
    assert out["camera_flood_detected"] == 1              # over twice in a row


def test_a_capture_back_under_restarts_the_count(tmp_path):
    s = tmp_path / "s.json"
    on = dict(camera_flood_detection_enabled=True, flood_baseline_water_pct=20)
    for name, frac, want in (("100000", 0.40, 0), ("110000", 0.25, 0), ("120000", 0.40, 0), ("130000", 0.40, 1)):
        out = _fields(_capture(tmp_path, f"20261015-{name}", frac), s, **on)
        assert out["camera_flood_detected"] == want, name


def test_growing_compares_with_previous_capture_and_is_confirmed(tmp_path):
    s = tmp_path / "s.json"
    on = dict(camera_flood_detection_enabled=True, flood_baseline_water_pct=90)
    assert _fields(_capture(tmp_path, "20261015-100000", 0.10), s, **on)["camera_flood_growing"] == 0
    assert _fields(_capture(tmp_path, "20261015-110000", 0.19), s, **on)["camera_flood_growing"] == 0
    out = _fields(_capture(tmp_path, "20261015-120000", 0.29), s, **on)       # first rise
    assert out == {"camera_water_pct": 29, "camera_flood_detected": 0, "camera_flood_growing": 0}
    out = _fields(_capture(tmp_path, "20261015-130000", 0.39), s, **on)       # second rise
    assert out == {"camera_water_pct": 39, "camera_flood_detected": 0, "camera_flood_growing": 1}


def test_lora_and_ip_see_the_same_previous_value_and_streak(tmp_path):
    s = tmp_path / "s.json"
    on = dict(camera_flood_detection_enabled=True, flood_baseline_water_pct=0)
    _fields(_capture(tmp_path, "20261015-100000", 0.10), s, **on)
    d = _capture(tmp_path, "20261015-110000", 0.40)
    first = _fields(d, s, **on)
    assert first == _fields(d, s, **on)               # the IP uplink's second look changes nothing
    assert first["camera_flood_detected"] == 1        # over on both captures
    assert first["camera_flood_growing"] == 0         # only one rise so far
    d2 = _capture(tmp_path, "20261015-120000", 0.60)
    assert _fields(d2, s, **on)["camera_flood_growing"] == 1


def _frame(capture_dir, grey):
    Image.fromarray(np.full((16, 16, 3), grey, np.uint8), mode="RGB").save(
        str(capture_dir + "/20261015-NIR-OFF.jpg"))


@pytest.mark.parametrize("grey", [5, 250])
def test_dark_or_blown_out_frames_raise_no_flags_and_leave_no_trace(tmp_path, grey):
    s = tmp_path / "s.json"
    on = dict(camera_flood_detection_enabled=True, flood_baseline_water_pct=0)
    good = _capture(tmp_path, "20261015-100000", 0.10)
    _frame(good, 128)
    _fields(good, s, **on)
    bad = _capture(tmp_path, "20261015-110000", 0.90)
    _frame(bad, grey)
    assert _fields(bad, s, **on) == {"camera_water_pct": 90}      # reported, no flags
    state = json.loads(s.read_text())
    assert state["capture"] == "20261015-100000" and state["water_pct"] == 10


def test_rgb_fallback_mask_raises_no_flags_and_leaves_no_trace(tmp_path):
    from tools import capture_status
    s = tmp_path / "s.json"
    on = dict(camera_flood_detection_enabled=True, flood_baseline_water_pct=0)
    _fields(_capture(tmp_path, "20261015-100000", 0.10), s, **on)
    fallback = _capture(tmp_path, "20261015-110000", 0.90)
    capture_status.mark(fallback, segmentation="rgb_fallback")
    assert _fields(fallback, s, **on) == {"camera_water_pct": 90}  # reported, no flags
    state = json.loads(s.read_text())
    assert state["capture"] == "20261015-100000" and state["water_pct"] == 10


def test_normal_brightness_frame_is_used(tmp_path):
    s = tmp_path / "s.json"
    d = _capture(tmp_path, "20261015-100000", 0.50)
    _frame(d, 128)
    out = _fields(d, s, camera_flood_detection_enabled=True, flood_baseline_water_pct=0)
    assert "camera_flood_detected" in out


def test_decide_threshold_zero_needs_a_rise():
    assert fb.decide(30, 30, 10, 0) == {"camera_flood_detected": 1, "camera_flood_growing": 0}


def test_water_pct_encoded_last_as_07_37():
    pkt = _encode_compressed_packet({"timestamp": 1791400000, "camera_flood_detected": 1,
                                     "camera_water_pct": 42, "registration_status": 0})
    assert pkt[-3:] == bytes([0x07, 0x37, 42])
    assert bytes([0x07, 0x17, 1]) in pkt


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli(tmp_path, *args):
    return fb.main(["--config", str(tmp_path / "runtime_config.json"), "--images",
                    str(tmp_path / "images"), "--state", str(tmp_path / "s.json"), *args])


def _cfg(tmp_path):
    return json.loads((tmp_path / "runtime_config.json").read_text())


def test_cli_set_from_latest_uses_newest_mask(tmp_path):
    (tmp_path / "runtime_config.json").write_text(json.dumps({"area_threshold": 10}))
    _capture(tmp_path / "images", "20261015-100000", 0.10)
    _capture(tmp_path / "images", "20261015-110000", 0.25)
    (tmp_path / "images" / "20261015-120000").mkdir()   # newest, but no mask
    assert _cli(tmp_path, "--set-from-latest") == 0
    assert _cfg(tmp_path) == {"area_threshold": 10, "flood_baseline_water_pct": 25}


def test_cli_set_show_reset(tmp_path, capsys):
    (tmp_path / "runtime_config.json").write_text("{}")
    assert _cli(tmp_path, "--set", "101") == 1
    assert _cli(tmp_path, "--set", "12") == 0
    assert _cfg(tmp_path)["flood_baseline_water_pct"] == 12
    assert _cli(tmp_path, "--show") == 0
    assert "12% water" in capsys.readouterr().out
    (tmp_path / "s.json").write_text(json.dumps({"water_pct": 5}))
    assert _cli(tmp_path, "--reset") == 0
    assert "flood_baseline_water_pct" not in _cfg(tmp_path)
    assert not (tmp_path / "s.json").exists()


def test_cli_set_from_latest_without_masks_changes_nothing(tmp_path):
    (tmp_path / "runtime_config.json").write_text("{}")
    assert _cli(tmp_path, "--set-from-latest") == 1
    assert _cfg(tmp_path) == {}
