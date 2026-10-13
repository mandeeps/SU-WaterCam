"""tools/coreg_multiple.apply_thread_setting: single-threaded SimpleITK on bench units."""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
coreg_multiple = pytest.importorskip("tools.coreg_multiple")


@pytest.mark.parametrize("value", ["1", "true", "YES", " 1 "])
def test_set_means_one_thread(monkeypatch, value):
    monkeypatch.setenv("COREG_SINGLE_THREAD", value)
    with patch.object(coreg_multiple.sitk.ProcessObject, "SetGlobalDefaultNumberOfThreads") as set_threads:
        assert coreg_multiple.apply_thread_setting() == 1
    set_threads.assert_called_once_with(1)


@pytest.mark.parametrize("value", [None, "", "0", "false", "no"])
def test_unset_leaves_threads_alone(monkeypatch, value):
    # Deployed units: SimpleITK keeps its default thread count.
    if value is None:
        monkeypatch.delenv("COREG_SINGLE_THREAD", raising=False)
    else:
        monkeypatch.setenv("COREG_SINGLE_THREAD", value)
    with patch.object(coreg_multiple.sitk.ProcessObject, "SetGlobalDefaultNumberOfThreads") as set_threads:
        assert coreg_multiple.apply_thread_setting() is None
    set_threads.assert_not_called()
