"""Preprocessing must match what the model was trained with.

The failure this guards against is silent: feed a model normalised differently
from how it was trained and it still returns a mask, just a wrong one. On a
mean/std-trained SegFormer fed min-max input, the observed result was 99.7%
of the frame called water.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from segformer_preprocess import (  # noqa: E402
    band_order_problem,
    canonical_bands,
    normalization_spec,
    preprocess_bands,
    tiff_band_order,
)

#: The band descriptions `tools/coreg_multiple.py` writes into the five-band TIFF.
TIFF_BANDS = ("Red Channel (Optical)", "Green Channel (Optical)",
              "Blue Channel (Optical)", "Thermal Data (Normalized 0-255)",
              "NIR Band (NIR-ON minus NIR-OFF)")
MODEL_BANDS = "red,green,blue,thermal,nir"


class _FakeMeta:
    def __init__(self, m):
        self.custom_metadata_map = m


class _FakeSession:
    """Stands in for an onnxruntime session's metadata surface."""

    def __init__(self, meta=None, raises=False):
        self._meta = meta
        self._raises = raises

    def get_modelmeta(self):
        if self._raises:
            raise RuntimeError("no metadata")
        return _FakeMeta(self._meta)


def _img(bands=5, h=8, w=8):
    rng = np.random.default_rng(0)
    return (rng.random((bands, h, w)) * 255).astype(np.uint8)


# --- what the model declares -------------------------------------------------

def test_embedded_means_do_not_normalise_here():
    s = _FakeSession({"normalization": "embedded:meanstd"})
    assert normalization_spec(s)["mode"] == "embedded"


def test_missing_metadata_falls_back_to_minmax():
    for s in (_FakeSession({}), _FakeSession(None), _FakeSession(raises=True)):
        assert normalization_spec(s)["mode"] == "minmax"


def test_unknown_scheme_falls_back_to_minmax_and_warns():
    spec = normalization_spec(_FakeSession({"normalization": "external:wavelet"}))
    assert spec["mode"] == "minmax"
    assert "WARNING" in spec["desc"]


def test_declared_meanstd_without_values_falls_back():
    spec = normalization_spec(_FakeSession({"normalization": "external:meanstd"}))
    assert spec["mode"] == "minmax"
    assert "WARNING" in spec["desc"]


def test_external_meanstd_is_read_from_metadata():
    spec = normalization_spec(_FakeSession({
        "normalization": "external:meanstd",
        "norm_mean": "[1,2,3,4,5]", "norm_std": "[2,2,2,2,2]"}))
    assert spec["mode"] == "meanstd"
    assert spec["mean"].tolist() == [1, 2, 3, 4, 5]


# --- what preprocess_bands then does -----------------------------------------

def test_embedded_keeps_raw_units_and_only_resizes():
    img = _img(h=16, w=16)
    out = preprocess_bands(img, 8, 8, spec={"mode": "embedded"})
    assert out.shape == (1, 5, 8, 8)
    assert out.max() > 1.0, "raw 0-255 units must survive for a self-normalising graph"


def test_minmax_is_the_default_and_lands_in_0_1():
    out = preprocess_bands(_img(), 8, 8)
    assert out.shape == (1, 5, 8, 8)
    assert 0.0 <= out.min() and out.max() <= 1.0


def test_constant_band_is_zeroed_not_nan():
    img = np.zeros((5, 8, 8), np.uint8)
    img[2] = 77
    out = preprocess_bands(img, 8, 8)
    assert np.isfinite(out).all() and out[0, 2].max() == 0.0


def test_meanstd_matches_the_arithmetic_it_claims():
    img = _img()
    mean = np.full(5, 10.0, np.float32)
    std = np.full(5, 4.0, np.float32)
    out = preprocess_bands(img, 8, 8, spec={"mode": "meanstd", "mean": mean, "std": std})
    np.testing.assert_allclose(out[0], (img.astype(np.float32) - 10.0) / 4.0, rtol=1e-6)


def test_percentile_clips_to_0_1():
    img = _img()
    spec = {"mode": "percentile", "lo": np.full(5, 50.0, np.float32),
            "hi": np.full(5, 200.0, np.float32)}
    out = preprocess_bands(img, 8, 8, spec=spec)
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_zero_std_does_not_divide_by_zero():
    out = preprocess_bands(_img(), 8, 8, spec={
        "mode": "meanstd", "mean": np.zeros(5, np.float32), "std": np.zeros(5, np.float32)})
    assert np.isfinite(out).all()


def test_caller_buffer_is_never_modified():
    img = _img()
    before = img.copy()
    preprocess_bands(img, 8, 8)
    np.testing.assert_array_equal(img, before)


@pytest.mark.parametrize("mode", ["embedded", "minmax"])
def test_output_is_float32_batched(mode):
    out = preprocess_bands(_img(), 8, 8, spec={"mode": mode})
    assert out.dtype == np.float32 and out.ndim == 4 and out.shape[0] == 1


# --- band order --------------------------------------------------------------

def test_tiff_descriptions_read_as_the_bands_they_name():
    assert canonical_bands(TIFF_BANDS) == ["red", "green", "blue", "thermal", "nir"]


def test_nir_is_not_read_as_red():
    """"NIR" contains "r"... and the naive keyword order would call it red."""
    assert canonical_bands(("NIR Band (NIR-ON minus NIR-OFF)",)) == ["nir"]


def test_matching_order_is_silent():
    assert band_order_problem(_FakeSession({"bands": MODEL_BANDS}), TIFF_BANDS) == ""


def test_bgr_tiff_against_an_rgb_model_is_reported():
    """The exact mismatch that final_5_band.tiff had: R and B transposed."""
    bgr = (TIFF_BANDS[2], TIFF_BANDS[1], TIFF_BANDS[0]) + TIFF_BANDS[3:]
    problem = band_order_problem(_FakeSession({"bands": MODEL_BANDS}), bgr)
    assert "blue,green,red" in problem


def test_band_count_disagreement_is_reported():
    problem = band_order_problem(_FakeSession({"bands": "red,green,blue"}), TIFF_BANDS)
    assert "3 bands" in problem and "has 5" in problem


@pytest.mark.parametrize("session", [
    _FakeSession({}),                      # exported before the metadata existed
    _FakeSession({"bands": ""}),
    _FakeSession(None),
    _FakeSession(raises=True),
])
def test_a_model_that_declares_nothing_is_not_accused(session):
    assert band_order_problem(session, TIFF_BANDS) == ""


@pytest.mark.parametrize("descriptions", [None, (), (None,) * 5, ("",) * 5])
def test_a_tiff_that_names_nothing_is_not_accused(descriptions):
    assert band_order_problem(_FakeSession({"bands": MODEL_BANDS}), descriptions) == ""


# --- the BAND_ORDER tag ------------------------------------------------------

def test_band_order_tag_is_preferred_over_prose():
    """A tag needs no interpretation, so it wins over keyword-matched prose."""
    assert tiff_band_order(TIFF_BANDS, {"BAND_ORDER": MODEL_BANDS}) == \
        ["red", "green", "blue", "thermal", "nir"]


def test_prose_is_used_when_there_is_no_tag():
    """Captures written before the tag existed still have to be read."""
    for tags in (None, {}, {"BAND_ORDER": ""}, {"BAND_ORDER": "   "}):
        assert tiff_band_order(TIFF_BANDS, tags) == \
            ["red", "green", "blue", "thermal", "nir"]


def test_tag_whitespace_and_case_are_tolerated():
    assert tiff_band_order((), {"BAND_ORDER": " Red , GREEN ,blue, Thermal ,NIR "}) == \
        ["red", "green", "blue", "thermal", "nir"]


def test_a_legacy_bgr_tag_is_caught_against_an_rgb_model():
    """final_5_band.tiff now tags itself BGR — the check must act on that."""
    problem = band_order_problem(_FakeSession({"bands": MODEL_BANDS}), (),
                                 {"BAND_ORDER": "blue,green,red,thermal,nir"})
    assert "blue,green,red" in problem


def test_tag_agreeing_with_the_model_is_silent():
    assert band_order_problem(_FakeSession({"bands": MODEL_BANDS}), (),
                              {"BAND_ORDER": MODEL_BANDS}) == ""
