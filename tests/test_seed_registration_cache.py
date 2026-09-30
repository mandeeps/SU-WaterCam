"""seed_registration_cache must write the cache where coreg() will look for it.

save_transform_parameters() writes to the PARENT of the path it is given. With
a bare relative --scene the default target used to be "", which collapsed the
child path to "_seed" and sent the write to a directory that does not exist;
a missing --out failed the same way. Both then reported success.
"""
import os

import pytest

sitk = pytest.importorskip("SimpleITK")
pytest.importorskip("pandas")

from tools.coreg_multiple import config, save_transform_parameters  # noqa: E402
from tools.seed_registration_cache import resolve_out_dir  # noqa: E402


def test_bare_relative_scene_defaults_to_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_out_dir(None, "20260914-210100") == str(tmp_path)


def test_scene_with_trailing_slash_defaults_to_its_parent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_out_dir(None, "images/20260914-210100/") == str(tmp_path / "images")


def test_relative_out_is_made_absolute_and_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = resolve_out_dir("new/cache", "anything")
    assert out == str(tmp_path / "new" / "cache")
    assert os.path.isdir(out)


def test_seed_lands_in_the_target_not_in_a_seed_subdirectory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "SAVE_TRANSFORM_PARAMETERS", True)
    out = resolve_out_dir(None, "20260914-210100")

    assert save_transform_parameters(sitk.AffineTransform(2), os.path.join(out, "_seed"))

    assert (tmp_path / config.TRANSFORM_CACHE_FILENAME).is_file()
    assert not (tmp_path / "_seed").exists()
