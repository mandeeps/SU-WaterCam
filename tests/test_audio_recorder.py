"""Tests for tools/audio_recorder.py — USB mic device discovery, per-iteration
segment lifecycle, the get_audio_recorder() singleton, and retention cleanup.
"""
import os
import time
from unittest.mock import patch, MagicMock

import pytest

import tools.audio_recorder as ar


ARECORD_LIST_WITH_USB = """\
**** List of CAPTURE Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 1: Device [USB PnP Sound Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

ARECORD_LIST_NO_USB = """\
**** List of CAPTURE Hardware Devices ****
card 0: Headphones [bcm2835 Headphones], device 0: bcm2835 Headphones [bcm2835 Headphones]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""


@pytest.fixture(autouse=True)
def _reset_singleton():
    ar._recorder = None
    yield
    ar._recorder = None


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------

class TestFindUsbMicDevice:
    def test_finds_usb_card(self):
        with patch.object(ar.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ARECORD_LIST_WITH_USB)
            assert ar._find_usb_mic_device() == "plughw:1,0"

    def test_returns_none_when_no_usb_card(self):
        with patch.object(ar.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(stdout=ARECORD_LIST_NO_USB)
            assert ar._find_usb_mic_device() is None

    def test_returns_none_when_arecord_missing(self):
        with patch.object(ar.subprocess, "run", side_effect=FileNotFoundError):
            assert ar._find_usb_mic_device() is None


# ---------------------------------------------------------------------------
# Segment lifecycle
# ---------------------------------------------------------------------------

class TestAudioRecorderSegments:
    def _make_recorder(self, device="plughw:1,0"):
        with patch.object(ar, "_find_usb_mic_device", return_value=device):
            return ar.AudioRecorder()

    def test_unavailable_when_no_mic_found(self):
        recorder = self._make_recorder(device=None)
        assert recorder.available() is False
        assert recorder.start_segment("/tmp/whatever") is False

    def test_start_segment_launches_arecord_with_expected_args(self, tmp_path):
        recorder = self._make_recorder()
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        with patch.object(ar.subprocess, "Popen", return_value=fake_proc) as mock_popen:
            ok = recorder.start_segment(str(tmp_path))

        assert ok is True
        args = mock_popen.call_args[0][0]
        assert args[0] == "arecord"
        assert "-D" in args and args[args.index("-D") + 1] == "plughw:1,0"
        assert "-r" in args and args[args.index("-r") + 1] == str(ar._SAMPLE_RATE)
        assert "-c" in args and args[args.index("-c") + 1] == str(ar._CHANNELS)
        assert args[-1] == os.path.join(str(tmp_path), "audio.wav")

    def test_starting_new_segment_finalizes_previous_one(self, tmp_path):
        recorder = self._make_recorder()
        first_proc = MagicMock()
        first_proc.poll.return_value = None
        second_proc = MagicMock()
        second_proc.poll.return_value = None

        with patch.object(ar.subprocess, "Popen", side_effect=[first_proc, second_proc]):
            recorder.start_segment(str(tmp_path / "iter1"))
            recorder.start_segment(str(tmp_path / "iter2"))

        first_proc.terminate.assert_called_once()
        first_proc.wait.assert_called_once()
        second_proc.terminate.assert_not_called()

    def test_stop_kills_process_if_terminate_times_out(self, tmp_path):
        recorder = self._make_recorder()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [ar.subprocess.TimeoutExpired(cmd="arecord", timeout=5), None]

        with patch.object(ar.subprocess, "Popen", return_value=proc):
            recorder.start_segment(str(tmp_path))
            recorder.stop()

        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    def test_stop_is_a_noop_when_nothing_recording(self):
        recorder = self._make_recorder()
        recorder.stop()  # must not raise


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestGetAudioRecorderSingleton:
    def test_second_call_returns_same_instance(self):
        with patch.object(ar, "_find_usb_mic_device", return_value=None):
            r1 = ar.get_audio_recorder()
            r2 = ar.get_audio_recorder()
        assert r1 is r2

    def test_stop_recording_if_active_is_noop_when_never_created(self):
        assert ar._recorder is None
        ar.stop_recording_if_active()  # must not construct a recorder or raise
        assert ar._recorder is None

    def test_stop_recording_if_active_stops_existing_recorder(self):
        with patch.object(ar, "_find_usb_mic_device", return_value="plughw:1,0"):
            recorder = ar.get_audio_recorder()
        with patch.object(recorder, "stop") as mock_stop:
            with patch.object(ar, "_recorder", recorder):
                ar.stop_recording_if_active()
        mock_stop.assert_called_once()


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------

class TestCleanupOldSegments:
    def test_removes_only_old_audio_segments(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WATERCAM_REPO", str(tmp_path))
        images_dir = tmp_path / "images"

        old_dir = images_dir / "20200101-000000"
        old_dir.mkdir(parents=True)
        old_audio = old_dir / "audio.wav"
        old_audio.write_bytes(b"old")
        old_photo = old_dir / "photo.jpg"
        old_photo.write_bytes(b"old-photo")

        new_dir = images_dir / "20990101-000000"
        new_dir.mkdir(parents=True)
        new_audio = new_dir / "audio.wav"
        new_audio.write_bytes(b"new")

        old_time = time.time() - 30 * 86400
        os.utime(old_audio, (old_time, old_time))
        os.utime(old_photo, (old_time, old_time))

        with patch.object(ar, "_find_usb_mic_device", return_value=None):
            ar.AudioRecorder(retention_days=7)

        assert not old_audio.exists(), "old audio segment should have been deleted"
        assert old_photo.exists(), "retention must only touch audio.wav, not other files"
        assert new_audio.exists(), "recent audio segment should be kept"

    def test_no_images_dir_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WATERCAM_REPO", str(tmp_path))
        with patch.object(ar, "_find_usb_mic_device", return_value=None):
            ar.AudioRecorder(retention_days=7)  # must not raise
