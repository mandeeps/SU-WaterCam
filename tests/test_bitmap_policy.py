"""tools/bitmap_policy.py: when the LoRa bitmap (08 18) is sent."""
import json

import pytest

from tools import bitmap_policy as bp

NOW = 1_760_000_000
H = 3600


def _last(water_pct=20, age_h=1.0, detected=0, growing=0):
    return {"water_pct": water_pct, "time": NOW - age_h * H, "capture": "20261015-110000",
            "camera_flood_detected": detected, "camera_flood_growing": growing}


def _decide(water_pct=20, flags=None, last="default", **kw):
    if last == "default":
        last = _last()
    return bp.decide(water_pct, flags or {}, last, NOW, **kw)


def test_unchanged_scene_is_skipped():
    send, reason = _decide()
    assert not send
    assert "20%" in reason and "1.0 h ago" in reason


def test_first_bitmap_is_sent():
    assert _decide(last=None)[0]


def test_unknown_water_pct_is_sent():
    assert _decide(water_pct=None)[0]


@pytest.mark.parametrize("pct,threshold,send", [
    (29, 10, False), (30, 10, True), (10, 10, True), (11, 10, False),
    (21, 0, True), (20, 0, False),     # threshold 0 still needs a 1-point change
    (21, 1, True),
])
def test_water_change_threshold(pct, threshold, send):
    assert _decide(water_pct=pct, area_threshold=threshold)[0] is send


@pytest.mark.parametrize("flags", [
    {"camera_flood_detected": 1}, {"camera_flood_growing": 1},
])
def test_flood_flag_set_is_sent(flags):
    assert _decide(flags=flags)[0]


@pytest.mark.parametrize("key", bp.FLAGS)
def test_flood_flag_cleared_is_sent(key):
    last = _last(**{key.replace("camera_flood_", ""): 1})
    send, reason = _decide(flags={key: 0}, last=last)
    assert send and "changed" in reason


def test_flags_absent_match_last_zero():
    """Detection off (no flags in the packet) against a last state with 0s."""
    assert not _decide(flags={})[0]


def test_emergency_is_sent():
    assert _decide(emergency=True)[0]


@pytest.mark.parametrize("age_h,send", [(23.99, False), (24.0, True), (30, True)])
def test_refresh_boundary(age_h, send):
    assert _decide(last=_last(age_h=age_h), refresh_hours=24)[0] is send


def test_refresh_zero_always_sends():
    send, reason = _decide(refresh_hours=0)
    assert send and "0" in reason


def test_future_last_time_is_sent():
    assert _decide(last=_last(age_h=-2))[0]


def test_bad_parameters_fall_back():
    assert not _decide(area_threshold="x", refresh_hours=None)[0]


@pytest.mark.parametrize("content", [
    "not json", "[]", "{}", json.dumps({"water_pct": "a", "time": NOW}),
    json.dumps({"water_pct": 5}), json.dumps({"water_pct": True, "time": NOW}),
])
def test_corrupt_state_means_send(tmp_path, content):
    path = tmp_path / "state.json"
    path.write_text(content)
    last = bp.load_state(str(path))
    assert last is None
    assert bp.decide(20, {}, last, NOW)[0]


def test_missing_state_means_send(tmp_path):
    assert bp.load_state(str(tmp_path / "none.json")) is None


def test_mark_sent_round_trip(tmp_path):
    path = str(tmp_path / "data" / "state.json")
    data = {"camera_water_pct": 42, "camera_flood_detected": 1}
    bp.mark_sent(data, "/images/20261015-120000/", now=NOW, path=path)
    state = bp.load_state(path)
    assert state == {"water_pct": 42, "time": NOW, "capture": "20261015-120000",
                     "camera_flood_detected": 1, "camera_flood_growing": 0}
    # The same scene a minute later, flood still on, is sent; with it off it's a change
    assert bp.decide(42, {"camera_flood_detected": 1}, state, NOW + 60)[0]
    assert bp.decide(42, {"camera_flood_detected": 0}, state, NOW + 60)[0]


def test_mark_sent_without_water_pct_writes_nothing(tmp_path):
    path = tmp_path / "state.json"
    bp.mark_sent({}, "/images/x", now=NOW, path=str(path))
    assert not path.exists()


def test_lora_decision_reads_parameters(tmp_path):
    path = str(tmp_path / "state.json")
    bp.mark_sent({"camera_water_pct": 20}, "/images/a", now=NOW - H, path=path)
    params = {"area_threshold": 10, "bitmap_refresh_hours": 24, "emergency_mode": False}
    get = lambda k, d=None: params.get(k, d)  # noqa: E731
    assert not bp.lora_decision({"camera_water_pct": 25}, NOW, get, state_path=path)[0]
    params["bitmap_refresh_hours"] = 0
    assert bp.lora_decision({"camera_water_pct": 25}, NOW, get, state_path=path)[0]
    params.update(bitmap_refresh_hours=24, emergency_mode=True)
    assert bp.lora_decision({"camera_water_pct": 25}, NOW, get, state_path=path)[0]


def test_refresh_hours_is_a_settable_parameter():
    from tools.lora_runtime_integration import LoRaRuntimeManager as M
    assert M._DEFAULT_PARAMS["bitmap_refresh_hours"] == 24
    assert M._PARAM_RANGES["bitmap_refresh_hours"] == (0, 168)
    assert "bitmap_refresh_hours" in M._SETTABLE_PARAMS
