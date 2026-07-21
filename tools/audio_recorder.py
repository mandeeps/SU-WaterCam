#!/usr/bin/env python
"""USB microphone audio recording, segmented one WAV file per iteration.

Uses `arecord` (ALSA, already installed as part of alsa-utils) as a
subprocess rather than a Python audio library, matching how the rest of
this codebase shells out to lightweight external tools for capture
(tt_take_photos.py's flir()).

Recording is split into one segment per monitoring iteration (one file per
images/<timestamp>/ directory) rather than one long file for the whole wake
window. The WittyPi's hardware power-cut schedule is a hard, un-interceptable
power-rail cut with no OS warning, so segmenting bounds data loss to at most
the currently in-progress segment instead of the entire session. `stop()` is
also called explicitly from call_shutdown() before the graceful `shutdown -h
now` path, so that case loses nothing.
"""

import os
import re
import subprocess
import threading
import time
from typing import Optional

_SAMPLE_RATE = 16000
_CHANNELS = 1
_FORMAT = "S16_LE"
_SEGMENT_FILENAME = "audio.wav"


def _find_usb_mic_device() -> Optional[str]:
    """Return an ALSA plughw device string for the first USB audio capture card, or None.

    plughw (rather than hw) lets ALSA's plugin layer handle any sample-rate/
    format conversion the USB mic's native modes don't cover directly.
    """
    try:
        out = subprocess.run(
            ["arecord", "-l"], capture_output=True, text=True, timeout=5
        ).stdout
    except Exception as e:
        print(f"⚠️ Could not enumerate audio devices: {e}")
        return None

    for line in out.splitlines():
        m = re.match(r"card (\d+): .*usb.*, device (\d+):", line, re.IGNORECASE)
        if m:
            card, device = m.group(1), m.group(2)
            return f"plughw:{card},{device}"
    return None


class AudioRecorder:
    def __init__(self, retention_days: Optional[int] = None):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._current_path: Optional[str] = None
        self._device = _find_usb_mic_device()
        if self._device is None:
            print("⚠️ No USB microphone detected — audio recording disabled")
        else:
            print(f"🎙️ USB microphone found at {self._device}")
        if retention_days is not None:
            self._cleanup_old_segments(retention_days)

    def available(self) -> bool:
        return self._device is not None

    def start_segment(self, directory: str) -> bool:
        """Finalize any in-progress segment and start a new one in `directory`."""
        if self._device is None:
            return False
        with self._lock:
            self._stop_locked()
            path = os.path.join(directory, _SEGMENT_FILENAME)
            try:
                self._proc = subprocess.Popen(
                    [
                        "arecord",
                        "-D", self._device,
                        "-f", _FORMAT,
                        "-c", str(_CHANNELS),
                        "-r", str(_SAMPLE_RATE),
                        "-t", "wav",
                        path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._current_path = path
                print(f"🎙️ Recording audio segment: {path}")
                return True
            except Exception as e:
                print(f"⚠️ Failed to start audio recording: {e}")
                self._proc = None
                self._current_path = None
                return False

    def stop(self) -> None:
        """Gracefully finalize the current segment, if any."""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        proc = self._proc
        if proc is None:
            return
        path = self._current_path
        if proc.poll() is None:
            try:
                # SIGTERM — arecord traps this and closes the WAV file with a
                # correct RIFF header rather than leaving it truncated/invalid.
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                print("⚠️ arecord did not exit after SIGTERM; killing")
                proc.kill()
                proc.wait(timeout=5)
            except Exception as e:
                print(f"⚠️ Error stopping audio recording: {e}")
        print(f"🎙️ Audio segment finalized: {path}")
        self._proc = None
        self._current_path = None

    def _cleanup_old_segments(self, retention_days: int) -> None:
        """Delete audio.wav files older than retention_days from images/*/."""
        repo_root = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
        images_dir = os.path.join(repo_root, "images")
        if not os.path.isdir(images_dir):
            return
        cutoff = time.time() - retention_days * 86400
        removed = 0
        try:
            for entry in os.listdir(images_dir):
                audio_path = os.path.join(images_dir, entry, _SEGMENT_FILENAME)
                if os.path.isfile(audio_path) and os.path.getmtime(audio_path) < cutoff:
                    try:
                        os.remove(audio_path)
                        removed += 1
                    except OSError as e:
                        print(f"⚠️ Could not remove old audio segment {audio_path}: {e}")
        except Exception as e:
            print(f"⚠️ Audio retention cleanup failed: {e}")
        if removed:
            print(f"🗑️ Audio retention: removed {removed} segment(s) older than {retention_days} days")


_recorder: Optional[AudioRecorder] = None
_recorder_lock = threading.Lock()


def get_audio_recorder(retention_days: Optional[int] = None) -> "AudioRecorder":
    """Thread-safe lazy singleton, matching tools.lora_handler_concurrent.get_lora_handler().

    `retention_days` only has an effect on the call that actually constructs
    the singleton (the first call); later calls reuse the existing instance.
    """
    global _recorder
    if _recorder is not None:
        return _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = AudioRecorder(retention_days=retention_days)
    return _recorder


def stop_recording_if_active() -> None:
    """Stop the current segment if a recorder singleton already exists; no-op otherwise.

    Used at shutdown time so a device that never enabled/started recording
    doesn't pay the cost of lazily constructing a recorder (device
    enumeration) just to immediately do nothing.
    """
    if _recorder is not None:
        _recorder.stop()
