#!/home/pi/SU-WaterCam/venv/bin/python
"""Water percentage of each capture's mask, and the node-side flood decision.

Every capture with a segmentation mask reports its water percentage (0-100,
camera_water_pct: 07 37 over LoRa and IP), whether or not flood detection is on.

Flood detection is off by default (runtime parameter
camera_flood_detection_enabled). A camera sees some water in normal conditions,
so the decision is made against a per-unit baseline, the water percentage at
installation (flood_baseline_water_pct in runtime_config.json). With detection
on and a baseline set, area_threshold (percentage points, default 10) sets:

    camera_flood_detected (07 17) = 1 if water % >= baseline + area_threshold
    camera_flood_growing  (07 27) = 1 if water % rose by area_threshold or more
                                    since the previous capture with a mask

A flag is raised only after CONFIRM_CAPTURES (2) captures in a row meet its
condition: one bad mask (a glare, a shadow, a failed segmentation) is not a
flood. The count restarts at the first capture that does not meet it.

Masks of frames whose mean brightness (0-255) is below MIN_BRIGHTNESS or above
MAX_BRIGHTNESS (night, dense fog, a covered or blown-out lens) are not trusted:
they still report camera_water_pct, but raise no flags and count for neither
the previous water % nor the streaks.

The previous water percentage persists across wakes in data/flood_state.json.

At installation, once a capture of the site in normal conditions has been
segmented (run from ~/SU-WaterCam):
    python tools/flood_baseline.py --set-from-latest   # or --set N
    python tools/flood_baseline.py --show
At a new site, or after the camera is re-aimed: --reset, then set it again.
"""
import argparse
import os
import sys
from typing import Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tools import config_io  # noqa: E402

_REPO_ROOT = os.environ.get("WATERCAM_REPO", "/home/pi/SU-WaterCam")
DEFAULT_STATE_PATH = os.path.join(_REPO_ROOT, "data", "flood_state.json")
DEFAULT_IMAGES_DIR = os.path.join(_REPO_ROOT, "images")
BASELINE_KEY = "flood_baseline_water_pct"
ENABLED_KEY = "camera_flood_detection_enabled"
DEFAULT_AREA_THRESHOLD = 10
#: Consecutive captures that must meet a flag's condition before it is raised
CONFIRM_CAPTURES = 2
#: Mean grey level (0-255) of the RGB frame outside which its mask is not used
#: for flood decisions. A first guess to tune from field captures.
MIN_BRIGHTNESS = 40
MAX_BRIGHTNESS = 225
#: Must match coreg_multiple.config.SEGMENTATION_PNG / LEGACY_SEGMENTATION_PNG.
#: Constants so this doesn't import OpenCV and rasterio just to find a file.
MASK_NAMES = ("color_preserved_5_band_segmentation.png", "final_5_band_segmentation.png")


def mask_path(dirname: str) -> Optional[str]:
    """The segmentation mask in a capture directory, or None if there is none."""
    for name in MASK_NAMES:
        p = os.path.join(dirname, name)
        if os.path.exists(p):
            return p
    return None


def mask_water_percent(dirname: str) -> Optional[int]:
    """Water percentage (0-100) of the capture's mask; None without a usable mask."""
    path = mask_path(dirname) if dirname else None
    if path is None:
        return None
    from PIL import Image
    from tools.compress_segmented import mask_format_error, water_percent
    try:
        with Image.open(path) as img:
            error = mask_format_error(img)
            if error:
                print(f"⚠️ Water %: {path}: {error}")
                return None
            return water_percent(img)
    except OSError as e:
        print(f"⚠️ Water %: couldn't read {path}: {e}")
        return None


def frame_brightness(dirname: str) -> Optional[float]:
    """Mean grey level (0-255) of the capture's RGB frame, or None if it has none."""
    try:
        names = sorted(n for n in os.listdir(dirname) if n.endswith("-NIR-OFF.jpg"))
    except OSError:
        return None
    if not names:
        return None
    try:
        from PIL import Image, ImageStat
        with Image.open(os.path.join(dirname, names[0])) as img:
            img.draft("L", (320, 240))      # decode JPEG at reduced size: this is a mean
            return float(ImageStat.Stat(img.convert("L")).mean[0])
    except Exception as e:                  # noqa: BLE001 - never fail a capture over this
        print(f"⚠️ Water %: couldn't measure brightness of {dirname}: {e}")
        return None


def latest_mask_dir(images_dir: str = DEFAULT_IMAGES_DIR) -> Optional[str]:
    """The newest capture directory (images/YYYYMMDD-HHMMSS) that has a mask."""
    try:
        names = sorted(os.listdir(images_dir), reverse=True)
    except OSError:
        return None
    for name in names:
        d = os.path.join(images_dir, name)
        if os.path.isdir(d) and mask_path(d):
            return d
    return None


def _load_state(path: str) -> dict:
    try:
        state = config_io.read_json(path)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def _record(dirname: str, pct: int, state_path: str) -> Tuple[Optional[int], dict]:
    """Store this capture's water % and return (the previous capture's, the flag
    streaks before this capture).

    Called by both the LoRa and the IP uplink for one capture: the second call
    sees the same capture and returns the same values."""
    capture = os.path.basename(os.path.normpath(dirname))
    state = _load_state(state_path)
    if state.get("capture") == capture:
        previous = state.get("previous_water_pct")
        before = state.get("streak_before")
        streak = state.get("streak")
    else:
        previous = state.get("water_pct")
        before = streak = state.get("streak")
    before = before if isinstance(before, dict) else {}
    new = {"capture": capture, "water_pct": pct, "previous_water_pct": previous,
           "streak_before": before, "streak": streak if isinstance(streak, dict) else before}
    try:
        if new != state:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
            config_io.write_json(state_path, new)
    except OSError as e:
        print(f"⚠️ Water %: couldn't save {state_path}: {e}")
    return (previous if isinstance(previous, int) else None), before


def _store_streak(state_path: str, streak: dict) -> None:
    state = _load_state(state_path)
    if state.get("streak") == streak:
        return
    state["streak"] = streak
    try:
        config_io.write_json(state_path, state)
    except OSError as e:
        print(f"⚠️ Water %: couldn't save {state_path}: {e}")


def decide(pct: int, previous: Optional[int], baseline: int, area_threshold: int) -> dict:
    """The two flood conditions for this water % (before confirmation); see the module docstring."""
    rise = max(1, area_threshold)   # with a threshold of 0, "growing" still needs a rise
    return {
        "camera_flood_detected": 1 if pct >= baseline + area_threshold else 0,
        "camera_flood_growing": 1 if previous is not None and pct - previous >= rise else 0,
    }


def confirm(raw: dict, before: dict) -> Tuple[dict, dict]:
    """(flags, streaks): a flag is 1 once its condition has held CONFIRM_CAPTURES captures running."""
    streak = {k: (int(before.get(k) or 0) + 1 if v else 0) for k, v in raw.items()}
    return {k: 1 if n >= CONFIRM_CAPTURES else 0 for k, n in streak.items()}, streak


def flood_fields(dirname: str, state_path: Optional[str] = None) -> dict:
    """Uplink fields for this capture: camera_water_pct when it has a mask, and
    the two flood flags when detection is on and a baseline is set."""
    pct = mask_water_percent(dirname)
    if pct is None:
        return {}
    fields = {"camera_water_pct": pct}
    brightness = frame_brightness(dirname)
    if brightness is not None and not MIN_BRIGHTNESS <= brightness <= MAX_BRIGHTNESS:
        print(f"🌙 Water {pct}%: frame brightness {brightness:.0f} is outside "
              f"{MIN_BRIGHTNESS}-{MAX_BRIGHTNESS}, mask not used for flood flags")
        return fields
    previous, before = _record(dirname, pct, state_path or DEFAULT_STATE_PATH)
    from tools.lora_runtime_integration import get_parameter
    if not get_parameter(ENABLED_KEY, False):
        return fields
    baseline = get_parameter(BASELINE_KEY, None)
    if not isinstance(baseline, int) or isinstance(baseline, bool):
        print("⚠️ Flood detection is on but no baseline is set (tools/flood_baseline.py --set-from-latest)")
        return fields
    try:
        area_threshold = int(get_parameter("area_threshold", DEFAULT_AREA_THRESHOLD))
    except (TypeError, ValueError):
        area_threshold = DEFAULT_AREA_THRESHOLD
    raw = decide(pct, previous, baseline, area_threshold)
    flags, streak = confirm(raw, before)
    _store_streak(state_path or DEFAULT_STATE_PATH, streak)
    fields.update(flags)
    print(f"🌊 Water {pct}% (baseline {baseline}%, previous {previous}%): "
          f"flood={flags['camera_flood_detected']} growing={flags['camera_flood_growing']} "
          f"(conditions met: {raw['camera_flood_detected']}/{raw['camera_flood_growing']}, "
          f"{CONFIRM_CAPTURES} in a row needed)")
    return fields


def _set_baseline(config: str, value: Optional[int]) -> None:
    with config_io.locked(config):
        cfg = config_io.read_json(config)
        if value is None:
            cfg.pop(BASELINE_KEY, None)
        else:
            cfg[BASELINE_KEY] = value
        config_io.write_json(config, cfg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default=os.path.join(REPO, "runtime_config.json"))
    ap.add_argument("--images", default=DEFAULT_IMAGES_DIR,
                    help="capture directories (default: the repo's images/)")
    ap.add_argument("--state", default=DEFAULT_STATE_PATH)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--set-from-latest", action="store_true",
                   help="baseline = water %% of the newest segmented capture")
    g.add_argument("--set", type=int, metavar="N", help="baseline = N %% (0-100)")
    g.add_argument("--show", action="store_true", help="print the baseline (the default)")
    g.add_argument("--reset", action="store_true",
                   help="clear the baseline and the previous water %%")
    a = ap.parse_args(argv)

    if not os.path.exists(a.config):
        print(f"{a.config} not found. Nothing changed.", file=sys.stderr)
        return 1
    if a.reset:
        _set_baseline(a.config, None)
        for p in (a.state, a.state + ".bak"):   # config_io restores from the .bak
            try:
                os.remove(p)
            except FileNotFoundError:
                pass
        print("Flood baseline cleared: set it again for this site (--set-from-latest or --set N)")
        return 0
    if a.set_from_latest or a.set is not None:
        if a.set is not None:
            if not 0 <= a.set <= 100:
                print("The baseline is a percentage, 0-100. Nothing changed.", file=sys.stderr)
                return 1
            value, source = a.set, "given"
        else:
            d = latest_mask_dir(a.images)
            value = mask_water_percent(d) if d else None
            if value is None:
                print(f"No usable segmentation mask under {a.images}. Nothing changed.", file=sys.stderr)
                return 1
            source = f"from {os.path.basename(d)}"
        _set_baseline(a.config, value)
        print(f"Flood baseline set to {value}% water ({source})")
        return 0

    cfg = config_io.read_json(a.config)
    baseline = cfg.get(BASELINE_KEY)
    state = _load_state(a.state)
    print(f"Flood detection: {'on' if cfg.get(ENABLED_KEY) else 'off'} ({ENABLED_KEY})")
    print(f"Baseline: {f'{baseline}% water' if baseline is not None else 'not set'}; "
          f"area_threshold: {cfg.get('area_threshold', DEFAULT_AREA_THRESHOLD)} points above it")
    if state.get("water_pct") is not None:
        print(f"Last capture {state.get('capture')}: {state['water_pct']}% water")
    return 0


if __name__ == "__main__":
    sys.exit(main())
