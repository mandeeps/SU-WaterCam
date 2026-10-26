#!/home/pi/SU-WaterCam/venv/bin/python
"""Mark captures taken before co-registration tagged band validity.

Co-registration now records whether the IR-cut filter moved (capture_status.json)
and tags the TIFFs it writes with the bands a capture lacks (INVALID_BANDS,
OPTICAL; see tools/capture_status.py). Captures made before that carry neither,
so a five-band TIFF from a unit whose filter was stuck still claims a good NIR
band and filtered RGB. This applies the same check and the same tags to them.

Captures are judged in name order, so give it one unit's captures per run. A
capture too dark to judge takes the verdict of the last lit capture before it
in the same run, as co-registration does; dark captures before any lit one stay
unmarked. A missing thermal frame is taken from capture_status.json only (older
captures have none recorded).

    python -m tools.mark_band_validity ~/SU-WaterCam/images            (dry run)
    python -m tools.mark_band_validity --apply ~/SU-WaterCam/images
"""
import argparse
import glob
import os
import sys

try:
    from tools import capture_status
    from tools.nir_filter_check import _VERDICT, _pair_paths, check_pair
except ImportError:                      # run as a script from tools/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools import capture_status
    from tools.nir_filter_check import _VERDICT, _pair_paths, check_pair

TIFFS = ("color_preserved_5_band.tiff", "final_5_band.tiff")


def capture_dirs(roots):
    """Capture directories (those holding a five-band TIFF) under roots, by name."""
    found = set()
    for root in roots:
        for name in TIFFS:
            found.update(os.path.dirname(p) for p in glob.glob(os.path.join(root, "**", name), recursive=True))
    return sorted(found, key=lambda d: (os.path.basename(d), d))


def tag_tiffs(directory: str) -> int:
    """Write the capture's validity tags into its TIFFs; returns how many it changed."""
    import rasterio
    tags = capture_status.tiff_tags(directory)
    changed = 0
    for name in TIFFS:
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            continue
        with rasterio.open(path, "r+") as dst:
            have = dst.tags()
            # Bands good again: an empty INVALID_BANDS removes the tag (GDAL drops empty items).
            want = dict(tags)
            if "INVALID_BANDS" in have and "INVALID_BANDS" not in want:
                want["INVALID_BANDS"] = ""
            if "OPTICAL" in have and "OPTICAL" not in want:
                want["OPTICAL"] = "filtered"
            if any(have.get(k) != v for k, v in want.items()):
                dst.update_tags(**want)
                changed += 1
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("roots", nargs="+", help="capture directories, or directories holding them")
    parser.add_argument("--apply", action="store_true", help="write capture_status.json and the TIFF tags")
    args = parser.parse_args(argv)

    counts = {"ok": 0, "not_switching": 0, "ok (carried)": 0, "not_switching (carried)": 0, "unjudged": 0}
    tiffs_changed = 0
    last = None
    for d in capture_dirs(args.roots):
        off, on = _pair_paths(d)
        result = check_pair(off, on) if off else None
        if result is not None:
            verdict = _VERDICT[result["status"]]
            fields = {"nir_filter": verdict, "nir_filter_diff": result["diff"]}
            last = (verdict, os.path.basename(d))
            counts[verdict] += 1
        elif last is not None:
            verdict = last[0]
            fields = {"nir_filter": verdict, "nir_filter_from": last[1]}
            counts[f"{verdict} (carried)"] += 1
        else:
            counts["unjudged"] += 1
            continue
        if args.apply:
            capture_status.mark(d, **fields)
            tiffs_changed += tag_tiffs(d)

    print(", ".join(f"{k}: {v}" for k, v in counts.items()))
    print(f"TIFFs tagged: {tiffs_changed}" if args.apply else "dry run: nothing written (--apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
