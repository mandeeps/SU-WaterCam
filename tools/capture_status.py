"""What happened to one capture, written next to it.

A capture that loses a sensor still has to be reported as such: a missing
thermal frame used to surface only as an empty bitmap with no explanation.
The file is small JSON in the capture directory (capture_status.json), merged
field by field because the capture, co-registration and segmentation steps each
run in their own process.

    thermal        "missing" when the Lepton gave no frame this capture
    segmentation   "rgb_only"  segmented from the RGB bands alone (model declares
                               bands=red,green,blue, so it never needed thermal)
                   "skipped_no_thermal"  not segmented: no thermal frame and no
                               RGB-only model; camera_water_pct is absent
"""
import json
import os
from typing import Any, Dict

FILENAME = "capture_status.json"


def read(directory: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(directory, FILENAME)) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def mark(directory: str, **fields: Any) -> Dict[str, Any]:
    """Merge `fields` into the capture's status file; never raises."""
    data = read(directory)
    data.update(fields)
    try:
        path = os.path.join(directory, FILENAME)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        print(f"⚠️ Could not write {FILENAME} in {directory}: {e}")
    return data


def thermal_missing(directory: str) -> bool:
    return read(directory).get("thermal") == "missing"
