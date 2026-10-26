"""Captures whose IR-cut filter did not move say so: capture_status.json and TIFF tags."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

from tools import capture_status
from tools import nir_filter_check as nfc

DESCRIPTIONS = ("Red Channel (Optical)", "Green Channel (Optical)", "Blue Channel (Optical)",
                "Thermal Data (Normalized 0-255)", "NIR Band (NIR-ON minus NIR-OFF)")


def _scene(seed=0, level=(60, 200)):
    rng = np.random.default_rng(seed)
    return rng.integers(*level, size=(486, 648, 3)).astype(np.float32)


def _capture(parent, name, stuck=True, dark=False, tiff=False):
    d = parent / name
    d.mkdir()
    off = _scene(level=(0, 10)) if dark else _scene()
    on = off.copy()
    if not stuck:
        on[..., 0] += 40
        on[..., 2] += 35
    for suffix, arr in (("NIR-OFF", off), ("NIR-ON", on)):
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(d / f"{name}-{suffix}.jpg", quality=90)
    if tiff:
        rasterio = pytest.importorskip("rasterio")
        with rasterio.open(d / "color_preserved_5_band.tiff", "w", driver="GTiff",
                           height=4, width=6, count=5, dtype="uint8") as dst:
            dst.write(np.zeros((5, 4, 6), np.uint8))
            for i, desc in enumerate(DESCRIPTIONS, 1):
                dst.set_band_description(i, desc)
            dst.update_tags(BAND_ORDER="red,green,blue,thermal,nir")
    return str(d)


def _tags(d):
    import rasterio
    with rasterio.open(os.path.join(d, "color_preserved_5_band.tiff")) as src:
        return src.tags()


# --- judging a capture ----------------------------------------------------------------------

def test_stuck_filter_is_recorded_and_marks_nir_invalid(tmp_path):
    last = str(tmp_path / "last.json")
    d = _capture(tmp_path, "20261026-115526", stuck=True)
    assert nfc.judge_capture(d, last) == "not_switching"
    assert capture_status.read(d)["nir_filter"] == "not_switching"
    assert capture_status.invalid_bands(d) == ["nir"]
    assert capture_status.tiff_tags(d) == {"INVALID_BANDS": "nir", "OPTICAL": "unfiltered"}
    assert nfc.status_code_for_capture(d) == nfc.STATUS_NOT_SWITCHING


def test_working_filter_leaves_every_band_valid(tmp_path):
    d = _capture(tmp_path, "20260615-110100", stuck=False)
    assert nfc.judge_capture(d, str(tmp_path / "last.json")) == "ok"
    assert capture_status.invalid_bands(d) == []
    assert capture_status.tiff_tags(d) == {}


def test_dark_capture_takes_the_last_lit_verdict_but_reports_none(tmp_path):
    last = str(tmp_path / "last.json")
    nfc.judge_capture(_capture(tmp_path, "20261026-175526", stuck=True), last)
    dark = _capture(tmp_path, "20261026-215526", dark=True)
    assert nfc.judge_capture(dark, last) == "not_switching"
    status = capture_status.read(dark)
    assert status["nir_filter_from"] == "20261026-175526"
    assert capture_status.invalid_bands(dark) == ["nir"]
    assert nfc.status_code_for_capture(dark) is None     # no verdict of its own goes to the server


def test_dark_capture_with_no_earlier_verdict_stays_unmarked(tmp_path):
    dark = _capture(tmp_path, "20261026-215526", dark=True)
    assert nfc.judge_capture(dark, str(tmp_path / "last.json")) is None
    assert "nir_filter" not in capture_status.read(dark)


def test_thermal_and_nir_both_listed(tmp_path):
    d = _capture(tmp_path, "20261026-115526", stuck=True)
    nfc.judge_capture(d, str(tmp_path / "last.json"))
    capture_status.mark(d, thermal="missing")
    assert capture_status.tiff_tags(d)["INVALID_BANDS"] == "thermal,nir"


# --- backfill -------------------------------------------------------------------------------

def test_backfill_dry_run_writes_nothing(tmp_path):
    from tools import mark_band_validity
    d = _capture(tmp_path, "20260705-110100", stuck=True, tiff=True)
    assert mark_band_validity.main([str(tmp_path)]) == 0
    assert capture_status.read(d) == {}
    assert "INVALID_BANDS" not in _tags(d)


def test_backfill_tags_stuck_and_carries_into_the_dark(tmp_path):
    from tools import mark_band_validity
    ok = _capture(tmp_path, "20260625-110100", stuck=False, tiff=True)
    stuck = _capture(tmp_path, "20260705-110100", stuck=True, tiff=True)
    dark = _capture(tmp_path, "20260705-230100", dark=True, tiff=True)
    mark_band_validity.main(["--apply", str(tmp_path)])
    assert "INVALID_BANDS" not in _tags(ok)
    assert _tags(stuck)["INVALID_BANDS"] == "nir" and _tags(stuck)["OPTICAL"] == "unfiltered"
    assert _tags(dark)["INVALID_BANDS"] == "nir"
    assert capture_status.read(dark)["nir_filter_from"] == "20260705-110100"
    assert _tags(stuck)["BAND_ORDER"] == "red,green,blue,thermal,nir"      # other tags kept


def test_backfill_clears_tags_when_the_bands_are_good_again(tmp_path):
    from tools import mark_band_validity
    d = _capture(tmp_path, "20260705-110100", stuck=False, tiff=True)
    capture_status.mark(d, nir_filter="not_switching")
    mark_band_validity.tag_tiffs(d)
    assert _tags(d)["INVALID_BANDS"] == "nir"
    mark_band_validity.main(["--apply", str(tmp_path)])
    assert not _tags(d).get("INVALID_BANDS") and _tags(d)["OPTICAL"] == "filtered"


# --- segmentation ---------------------------------------------------------------------------

def test_daemon_refuses_a_band_the_tiff_marks_invalid(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import segformer_daemon as daemon

    path = tmp_path / "t.tiff"
    with rasterio.open(path, "w", driver="GTiff", height=4, width=6, count=5, dtype="uint8") as dst:
        dst.write(np.zeros((5, 4, 6), np.uint8))
        for i, desc in enumerate(DESCRIPTIONS, 1):
            dst.set_band_description(i, desc)
        dst.update_tags(BAND_ORDER="red,green,blue,thermal,nir", INVALID_BANDS="nir")

    class Boom(Exception):
        pass

    class Session:
        def __init__(self, bands):
            self.meta = {"bands": bands}

        def get_inputs(self):
            return [type("I", (), {"name": "x", "shape": [1, 3, 4, 6]})()]

        def get_modelmeta(self):
            return type("M", (), {"custom_metadata_map": self.meta})()

        def run(self, *a, **k):
            raise Boom

    out = str(tmp_path / "o.png")
    with pytest.raises(ValueError, match="nir"):              # without rgb_only in the request
        daemon.run_inference(Session("red,green,blue,thermal,nir"), str(path), out)
    with pytest.raises(ValueError, match="nir"):
        daemon.run_inference(Session("red,green,nir"), str(path), out)
    with pytest.raises(Boom):                                  # thermal is fine here
        daemon.run_inference(Session("red,green,thermal"), str(path), out)
    with pytest.raises(Boom):
        daemon.run_inference(Session("red,green,blue"), str(path), out)


@pytest.fixture
def tm():
    import ticktalk_main
    return ticktalk_main


def _stuck_capture_with_tiff(tmp_path):
    from tools.coreg_multiple import config
    (tmp_path / config.MODEL_INPUT_TIFF).write_bytes(b"tiff")
    capture_status.mark(str(tmp_path), nir_filter="not_switching")
    return str(tmp_path)


def _socket_present(monkeypatch):
    monkeypatch.setattr("os.path.exists",
                        lambda p, _real=os.path.exists: True if p.endswith(".sock") else _real(p))


def test_stuck_filter_uses_the_rgb_only_model(tm, tmp_path, monkeypatch):
    d = _stuck_capture_with_tiff(tmp_path)
    _socket_present(monkeypatch)
    with patch("tools.segformer_client.segformer_via_daemon", return_value=True) as daemon, \
         patch("subprocess.run") as run:
        assert tm.segformer.__wrapped__(d, True)
    assert daemon.call_args.kwargs["rgb_only"] is True
    run.assert_not_called()
    assert capture_status.read(d)["segmentation"] == "rgb_only"


def test_stuck_filter_and_no_rgb_only_model_skips(tm, tmp_path, monkeypatch):
    d = _stuck_capture_with_tiff(tmp_path)
    _socket_present(monkeypatch)
    with patch("tools.segformer_client.segformer_via_daemon", return_value=False), \
         patch("subprocess.run") as run:
        assert tm.segformer.__wrapped__(d, True) is None
    run.assert_not_called()
    assert capture_status.read(d)["segmentation"] == "skipped_no_nir"


# --- the RGB fallback -----------------------------------------------------------------------

def _daemon():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import segformer_daemon
    return segformer_daemon


def _ask(daemon, session, fallback, tmp_path, rgb_only=False):
    import json
    import socket
    tiff_path = tmp_path / "t.tiff"
    tiff_path.write_bytes(b"x")
    a, b = socket.socketpair()
    req = {"tiff_path": str(tiff_path), "output_path": str(tmp_path / "o.png")}
    if rgb_only:
        req["rgb_only"] = True
    a.sendall((json.dumps(req) + "\n").encode())
    daemon.handle_connection(b, session, fallback)
    reply = json.loads(a.recv(4096).decode())
    a.close()
    return reply


def _fake_inference(daemon, monkeypatch):
    def run(session, tiff, out, rgb_only=False):
        if session == "five-band":
            raise daemon.BandUnavailable("this model reads the nir band, which this capture lacks")
        return 12.0
    monkeypatch.setattr(daemon, "run_inference", run)


def test_daemon_runs_the_rgb_fallback_when_a_band_is_missing(tmp_path, monkeypatch):
    daemon = _daemon()
    _fake_inference(daemon, monkeypatch)
    fallback = daemon.RgbFallback("/x/rgb.onnx")
    monkeypatch.setattr(fallback, "get", lambda: "rgb")
    fallback.sha256 = "ab" * 32
    reply = _ask(daemon, "five-band", fallback, tmp_path)
    assert reply == {"status": "ok", "model": "rgb_fallback", "model_sha256": "ab" * 32,
                     "inference_ms": 12}


def test_daemon_without_a_fallback_answers_with_the_error(tmp_path, monkeypatch):
    daemon = _daemon()
    _fake_inference(daemon, monkeypatch)
    reply = _ask(daemon, "five-band", daemon.RgbFallback(""), tmp_path)
    assert reply["status"] == "error" and "nir" in reply["message"]


def test_capture_with_all_bands_uses_the_loaded_model(tmp_path, monkeypatch):
    daemon = _daemon()
    _fake_inference(daemon, monkeypatch)
    fallback = daemon.RgbFallback("/x/rgb.onnx")
    monkeypatch.setattr(fallback, "get", lambda: pytest.fail("fallback loaded for a good capture"))
    assert _ask(daemon, "rgb", fallback, tmp_path) == {"status": "ok", "inference_ms": 12}


def test_missing_fallback_file_is_tried_once(tmp_path, monkeypatch):
    daemon = _daemon()
    loads = []
    monkeypatch.setattr(daemon, "load_session", lambda p: loads.append(p))
    fallback = daemon.RgbFallback(str(tmp_path / "absent.onnx"))
    assert fallback.get() is None and fallback.get() is None
    assert loads == []                                    # never got as far as loading


def test_fallback_that_reads_more_than_rgb_is_refused(tmp_path, monkeypatch):
    daemon = _daemon()
    path = tmp_path / "m.onnx"
    path.write_bytes(b"onnx")
    monkeypatch.setattr(daemon, "load_session", lambda p: "session")
    monkeypatch.setattr(daemon, "model_metadata", lambda s: {"bands": "red,green,blue,thermal,nir"})
    assert daemon.RgbFallback(str(path)).get() is None


def test_fallback_mask_is_recorded_with_its_model(tm, tmp_path, monkeypatch):
    d = _stuck_capture_with_tiff(tmp_path)
    _socket_present(monkeypatch)

    def daemon(*a, info=None, **k):
        info.update(status="ok", model="rgb_fallback", model_sha256="4ca62324" + "0" * 56)
        return True
    with patch("tools.segformer_client.segformer_via_daemon", side_effect=daemon):
        assert tm.segformer.__wrapped__(d, True)
    status = capture_status.read(d)
    assert status["segmentation"] == "rgb_fallback"
    assert status["segmentation_model_sha256"].startswith("4ca62324")


def test_uplink_model_id_names_the_model_that_made_the_mask(tmp_path, monkeypatch):
    import json
    from tools import node_version as nv
    info = tmp_path / "model.json"
    info.write_text(json.dumps({"name": "m.onnx", "sha256": "11223344" + "0" * 56}))
    monkeypatch.setattr(nv, "MODEL_INFO_PATH", str(info))
    monkeypatch.setattr(nv, "software_version", lambda: None)
    capture = tmp_path / "cap"
    capture.mkdir()
    assert nv.uplink_fields(str(capture))["model_id"] == 0x11223344
    capture_status.mark(str(capture), segmentation_model_sha256="4ca62324" + "0" * 56)
    assert nv.uplink_fields(str(capture))["model_id"] == 0x4CA62324
    assert nv.uplink_fields()["model_id"] == 0x11223344
