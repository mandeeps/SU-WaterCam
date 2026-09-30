"""--normalization must stamp everything normalization_spec() needs to honour it.

A graph that declares external:meanstd without norm_mean/norm_std is unusable
by normalization_spec(), which then serves it min-max: a wrong mask, not an
error. The export must refuse such a declaration rather than stamp it.
"""
import sys
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import export_segformer_onnx as exp  # noqa: E402
from segformer_preprocess import normalization_spec  # noqa: E402

MEAN = "10,20,30,40,50"
STD = "1,2,3,4,5"
NONE = {"norm_mean": None, "norm_std": None, "norm_p_lo": None, "norm_p_hi": None}


def _stats(**kw):
    return {**NONE, **kw}


class _FakeSession:
    def __init__(self, meta):
        self._meta = meta

    def get_modelmeta(self):
        return types.SimpleNamespace(custom_metadata_map=self._meta)


def test_meanstd_stamps_statistics_normalization_spec_can_read():
    pairs = exp.normalization_metadata("external:meanstd", 5, _stats(norm_mean=MEAN, norm_std=STD))
    meta = {"normalization": "external:meanstd", **dict(pairs)}
    spec = normalization_spec(_FakeSession(meta))
    assert spec["mode"] == "meanstd"
    np.testing.assert_array_equal(spec["mean"], [10, 20, 30, 40, 50])
    np.testing.assert_array_equal(spec["std"], [1, 2, 3, 4, 5])


def test_percentile_stamps_statistics_normalization_spec_can_read():
    pairs = exp.normalization_metadata("external:percentile", 5,
                                       _stats(norm_p_lo="0,0,0,0,0", norm_p_hi="255,255,255,200,180"))
    meta = {"normalization": "external:percentile", **dict(pairs)}
    spec = normalization_spec(_FakeSession(meta))
    assert spec["mode"] == "percentile"
    np.testing.assert_array_equal(spec["hi"], [255, 255, 255, 200, 180])


@pytest.mark.parametrize("normalization, stats, match", [
    ("external:meanstd", _stats(), "requires --norm-mean and --norm-std"),
    ("external:meanstd", _stats(norm_mean=MEAN), "requires --norm-std"),
    ("external:percentile", _stats(norm_p_lo="0,0,0,0,0"), "requires --norm-p-hi"),
    ("external:meanstd", _stats(norm_mean="1,2,3", norm_std="1,2,3"), "one value per band"),
    ("external:meanstd", _stats(norm_mean="a,b,c,d,e", norm_std=STD), "comma-separated numbers"),
    ("external:percentile", _stats(norm_p_lo="5,5,5,5,5", norm_p_hi="5,9,9,9,9"), "greater than"),
    ("external:minmax", _stats(norm_mean=MEAN), "does not use --norm-mean"),
    ("embedded:meanstd", _stats(norm_std=STD), "does not use --norm-std"),
    ("external:mean-std", _stats(), "unknown normalization"),
    ("meanstd", _stats(), "unknown normalization"),
    ("embedded:", _stats(), "must name what the graph applies"),
])
def test_unusable_declarations_are_refused(normalization, stats, match):
    with pytest.raises(ValueError, match=match):
        exp.normalization_metadata(normalization, 5, stats)


@pytest.mark.parametrize("normalization", ["external:minmax", "embedded:meanstd"])
def test_modes_without_statistics_need_none(normalization):
    assert exp.normalization_metadata(normalization, 5, _stats()) == []


def test_stamp_writes_the_statistics(monkeypatch, tmp_path):
    class _Props(list):
        def add(self):
            entry = types.SimpleNamespace(key=None, value=None)
            self.append(entry)
            return entry

    model = types.SimpleNamespace(metadata_props=_Props())
    saved = []
    fake_onnx = types.SimpleNamespace(load=lambda path: model,
                                      save=lambda m, path: saved.append(path))
    monkeypatch.setitem(sys.modules, "onnx", fake_onnx)

    pairs = exp.normalization_metadata("external:meanstd", 5, _stats(norm_mean=MEAN, norm_std=STD))
    exp.stamp_normalization(str(tmp_path / "m.onnx"), "external:meanstd", stats=pairs)

    meta = {p.key: p.value for p in model.metadata_props}
    assert saved
    assert normalization_spec(_FakeSession(meta))["mode"] == "meanstd"


def test_cli_rejects_meanstd_without_statistics(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["export", "--normalization", "external:meanstd"])
    with pytest.raises(SystemExit):
        exp.parse_args()


def test_cli_accepts_meanstd_with_statistics(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["export", "--normalization", "external:meanstd",
                                      "--norm-mean", MEAN, "--norm-std", STD])
    args = exp.parse_args()
    assert dict(args.norm_stats)["norm_mean"] == "[10.0, 20.0, 30.0, 40.0, 50.0]"
