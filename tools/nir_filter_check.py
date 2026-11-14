#!/home/pi/SU-WaterCam/venv/bin/python
"""Check that the IR-cut filter moved between a capture's NIR-OFF and NIR-ON frames.

button_hold_camera.take_nir_pair() takes the NIR-OFF frame with the filter in and
the NIR-ON frame with it out, both at the same locked exposure and white balance.
With the filter working the two differ a lot in daylight: the ON frame takes in
the near-infrared the filter blocks, so it is brighter and pinker. If the filter
doesn't move, both frames are the same image, the NIR band (their difference) is
empty and the RGB frame carries infrared. Unit 006 ran like that from June 2026
until it was noticed in October, so this check reports it with every capture.

The comparison runs on small copies of the two frames (JPEG draft decoding, about
1/8 scale), so it costs little on the Pi:

- the OFF frame must be lit, its mean above LIT_MIN_MEAN (of 255): in the dark both
  frames are black whatever the filter does, so no verdict;
- the frames' mean red, green and blue are then compared: the largest of the
  three differences against SAME_MAX_DIFF. The filter changes the whole frame's
  brightness and colour, while things that move between the two frames (water,
  leaves) only move pixels about and leave the means alone, so a per-pixel
  difference would not do. On 006's captures working pairs differed by 13-145
  (April-June 2026); its stuck filter gave 0.1-1.2, a few up to 10 when the light
  changed between the two frames.

Sent to the server as nir_filter_status (channel 07 47, both transports):
0 the filter moved, 1 it did not (the frames are the same though lit). No value
when there is no verdict: no pair, a frame unreadable, or too dark.

Co-registration records the verdict in the capture's capture_status.json
(judge_capture) and, when the filter did not move, marks the TIFFs it writes:
INVALID_BANDS=nir and OPTICAL=unfiltered. A filter that is stuck stays stuck
after dark, so a capture too dark to judge takes the last lit capture's verdict
(kept in data/nir_filter_last.json) and names where it came from; the server
still gets a value only for captures judged on their own frames.

    python -m tools.nir_filter_check <capture dir>...    (from ~/SU-WaterCam)
"""
import glob
import json
import os
import sys
from typing import Any, Dict, Optional

try:
    from tools import capture_status
except ImportError:                      # run as a script from tools/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools import capture_status

LIT_MIN_MEAN = 20.0
SAME_MAX_DIFF = 5.0
_DRAFT_SIZE = (324, 243)

STATUS_OK = 0
STATUS_NOT_SWITCHING = 1
_VERDICT = {STATUS_OK: "ok", STATUS_NOT_SWITCHING: "not_switching"}

LAST_LIT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "nir_filter_last.json")


def _small(path: str):
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        im.draft("RGB", _DRAFT_SIZE)
        return np.asarray(im.convert("RGB").resize(_DRAFT_SIZE), dtype=np.float32)


def check_pair(off_path: str, on_path: str) -> Optional[Dict[str, Any]]:
    """{'status', 'off_mean', 'diff'} for one NIR pair, or None without a verdict."""
    try:
        off, on = _small(off_path), _small(on_path)
    except Exception as exc:
        print(f"NIR filter check: could not read the pair: {exc}")
        return None
    off_mean = float(off.mean())
    if off_mean < LIT_MIN_MEAN:
        return None
    diff = float(abs(off.reshape(-1, 3).mean(0) - on.reshape(-1, 3).mean(0)).max())
    status = STATUS_NOT_SWITCHING if diff < SAME_MAX_DIFF else STATUS_OK
    return {"status": status, "off_mean": round(off_mean, 1), "diff": round(diff, 1)}


def _pair_paths(dirname: str):
    off = sorted(glob.glob(os.path.join(dirname, "*-NIR-OFF.jpg")))
    on = sorted(glob.glob(os.path.join(dirname, "*-NIR-ON.jpg")))
    return (off[0], on[0]) if off and on else (None, None)


def _last_lit(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if data.get("nir_filter") in _VERDICT.values() else None
    except (OSError, ValueError, AttributeError):
        return None


def _save_last_lit(path: str, data: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"NIR filter check: could not save the last verdict: {exc}")


def judge_capture(dirname: str, last_lit_path: Optional[str] = None) -> Optional[str]:
    """Record the capture's verdict in its capture_status.json and return it.

    "ok", "not_switching", or None when there is neither a verdict of its own
    nor an earlier lit capture to take one from.
    """
    last_lit_path = last_lit_path or LAST_LIT_PATH
    off, on = _pair_paths(dirname)
    result = check_pair(off, on) if off else None
    if result is not None:
        verdict = _VERDICT[result["status"]]
        capture_status.mark(dirname, nir_filter=verdict, nir_filter_diff=result["diff"])
        _save_last_lit(last_lit_path, {"nir_filter": verdict,
                                       "capture": os.path.basename(os.path.normpath(dirname))})
        return verdict
    last = _last_lit(last_lit_path)
    if last is None:
        return None
    capture_status.mark(dirname, nir_filter=last["nir_filter"], nir_filter_from=last.get("capture"))
    return last["nir_filter"]


def status_code_for_capture(dirname: str) -> Optional[int]:
    """nir_filter_status for the capture in dirname, or None (see the module doc)."""
    if not dirname:
        return None
    status = capture_status.read(dirname)
    if status.get("nir_filter") in _VERDICT.values():
        if status.get("nir_filter_from"):
            return None                   # borrowed from an earlier capture: no verdict of its own
        return STATUS_NOT_SWITCHING if status["nir_filter"] == "not_switching" else STATUS_OK
    off, on = _pair_paths(dirname)
    if off is None:
        return None
    result = check_pair(off, on)
    if result is None:
        return None
    if result["status"] == STATUS_NOT_SWITCHING:
        print(f"⚠️ NIR filter not switching: NIR-OFF and NIR-ON are the same "
              f"(difference {result['diff']}, brightness {result['off_mean']})")
    else:
        print(f"NIR filter OK (difference {result['diff']})")
    return result["status"]


def main(argv) -> int:
    for dirname in argv:
        off, on = _pair_paths(dirname)
        result = check_pair(off, on) if off else None
        print(f"{dirname}: {result if result else 'no verdict'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
