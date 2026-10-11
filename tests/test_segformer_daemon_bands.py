"""The daemon reads only the bands a model declares, from a real five-band TIFF."""
import sys
from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
pytest.importorskip("cv2")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from segformer_daemon import load_and_preprocess  # noqa: E402
from segformer_preprocess import model_band_indices  # noqa: E402

DESCRIPTIONS = ("Red Channel (Optical)", "Green Channel (Optical)", "Blue Channel (Optical)",
                "Thermal Data (Normalized 0-255)", "NIR Band (NIR-ON minus NIR-OFF)")


class _Session:
    def __init__(self, meta):
        self._meta = meta

    def get_modelmeta(self):
        return type("M", (), {"custom_metadata_map": self._meta})()


@pytest.fixture
def tiff(tmp_path):
    path = tmp_path / "color_preserved_5_band.tiff"
    data = np.stack([np.full((4, 6), 10 * (i + 1), np.uint8) for i in range(5)])
    with rasterio.open(path, "w", driver="GTiff", height=4, width=6, count=5, dtype="uint8") as dst:
        dst.write(data)
        for i, d in enumerate(DESCRIPTIONS, start=1):
            dst.set_band_description(i, d)
        dst.update_tags(BAND_ORDER="red,green,blue,thermal,nir")
    return str(path)


def _bands(tiff, meta):
    with rasterio.open(tiff) as src:
        idx = model_band_indices(_Session(meta), src.descriptions, src.tags())
    return load_and_preprocess(tiff, 4, 6, spec={"mode": "embedded"}, bands=idx)


def test_rgb_model_reads_three_bands(tiff):
    arr = _bands(tiff, {"bands": "red,green,blue"})
    assert arr.shape == (1, 3, 4, 6)
    assert [float(arr[0, c, 0, 0]) for c in range(3)] == [10.0, 20.0, 30.0]


def test_five_band_model_reads_all(tiff):
    arr = _bands(tiff, {"bands": "red,green,blue,thermal,nir"})
    assert arr.shape == (1, 5, 4, 6)
    assert float(arr[0, 4, 0, 0]) == 50.0
