"""The daemon refuses a model it cannot feed correctly, and still runs the old one."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import segformer_daemon as daemon  # noqa: E402
from segformer_preprocess import (ModelContractError, check_model_contract,  # noqa: E402
                                  normalization_spec, resize_interpolation)

BANDS = "red,green,blue,thermal,nir"


def meta(**kw):
    m = {"schema": "1", "bands": BANDS, "in_channels": "5", "classes": "background,water",
         "normalization": "minmax", "input_layout": "NCHW", "input_range": "normalised",
         "resize_interpolation": "bilinear"}
    m.update(kw)
    return {k: v for k, v in m.items() if v is not None}


class Session:
    def __init__(self, m, channels=5):
        self._m, self._c = m, channels

    def get_modelmeta(self):
        return type("M", (), {"custom_metadata_map": self._m})()

    def get_inputs(self):
        return [type("I", (), {"name": "input", "shape": [1, self._c, "h", "w"]})()]


def test_model_with_no_metadata_passes_and_is_minmax_area():
    """The deployed segformer_5band_dyn.onnx declares nothing and keeps working."""
    assert check_model_contract({}, 5) == []
    s = Session({})
    assert normalization_spec(s)["mode"] == "minmax"
    assert resize_interpolation(s) == "area"


def test_complete_valid_metadata_passes():
    assert check_model_contract(meta(), 5) == []
    ms = meta(normalization="meanstd", norm_mean="[1,2,3,4,5]", norm_std="[1,1,1,1,1]")
    assert check_model_contract(ms, 5) == []
    assert normalization_spec(Session(ms))["mode"] == "meanstd"
    assert check_model_contract(meta(normalization="embedded:meanstd",
                                     input_range="raw_0_255"), 5) == []


def test_rgb_subset_is_fine():
    m = meta(bands="red,green,blue", in_channels="3")
    assert check_model_contract(m, 3) == []


@pytest.mark.parametrize("bad, expect", [
    (dict(in_channels="4"), "in_channels 4 but 5 bands"),
    (dict(bands="red,green,blue,thermal,swir"), "unknown band"),
    (dict(normalization="zscore"), "not one the daemon can apply"),
    (dict(normalization="meanstd"), "norm_mean"),
    (dict(normalization="meanstd", norm_mean="[1,2]", norm_std="[1,2]"), "2 values for 5"),
    (dict(normalization="embedded:meanstd", input_range="normalised"), "input_range"),
    (dict(schema="2"), "newer than this daemon"),
    (dict(input_layout="NHWC"), "NCHW"),
    (dict(resize_interpolation="cubic"), "resize_interpolation"),
])
def test_unhonourable_metadata_is_refused(bad, expect):
    problems = check_model_contract(meta(**bad), 5)
    assert any(expect in p for p in problems), problems


def test_graph_channel_mismatch_is_refused():
    assert any("graph takes 3" in p for p in check_model_contract(meta(), 3))


def test_band_order_is_not_a_contract_error():
    """Order mismatches stay warnings (band_order_problem), never refusals."""
    assert check_model_contract(meta(bands="nir,thermal,blue,green,red"), 5) == []


def test_load_session_refuses_before_serving(tmp_path, monkeypatch):
    pytest.importorskip("onnxruntime")
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    fake = Session(meta(normalization="zscore"))
    import onnxruntime
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **k: fake)
    with pytest.raises(ModelContractError, match="refused"):
        daemon.load_session(str(model))


def test_refused_primary_falls_back_to_the_next_model(tmp_path, monkeypatch):
    a, b = tmp_path / "int8.onnx", tmp_path / "fp32.onnx"
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    def load(path):
        if path == str(a):
            raise ModelContractError("refused")
        return "session-b"

    monkeypatch.setattr(daemon, "load_session", load)
    assert daemon.load_first_usable([str(a), str(b)]) == (str(b), "session-b")
    with pytest.raises(SystemExit):
        daemon.load_first_usable([str(a), str(tmp_path / "missing.onnx")])


def test_record_model_adds_name_and_version(tmp_path):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"abc")
    info = tmp_path / "model.json"
    daemon.record_model(str(model), str(info),
                        {"model_name": "tiny", "model_version": "2026-10-15"})
    rec = json.loads(info.read_text())
    assert rec["model_name"] == "tiny" and rec["model_version"] == "2026-10-15"
    assert rec["name"] == "m.onnx" and len(rec["sha256"]) == 64
    daemon.record_model(str(model), str(info))               # old model: unchanged shape
    assert set(json.loads(info.read_text())) == {"name", "path", "sha256"}


def test_model_hash_is_recorded_after_the_socket_is_serving(tmp_path, monkeypatch):
    """Hashing the model reads the whole file; it must not delay the socket."""
    events = []
    monkeypatch.setattr(daemon, "parse_args", lambda: type("A", (), {
        "model": "m.onnx", "fallback_fp32": None, "socket": str(tmp_path / "s.sock")})())
    monkeypatch.setattr(daemon, "load_first_usable", lambda c: ("m.onnx", object()))
    monkeypatch.setattr(daemon, "model_metadata", lambda s: {})
    done = daemon.threading.Event()

    def record(*a, **k):
        events.append("hash")
        done.set()

    def serve(session, sock, on_listening=None):
        events.append("listening")
        on_listening()
        done.wait(5)

    monkeypatch.setattr(daemon, "record_model", record)
    monkeypatch.setattr(daemon, "serve", serve)
    daemon.main()
    assert events == ["listening", "hash"]
