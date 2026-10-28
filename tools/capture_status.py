"""What happened to one capture, written next to it.

A capture that loses a sensor still has to be reported as such: a missing
thermal frame used to surface only as an empty bitmap with no explanation.
The file is small JSON in the capture directory (capture_status.json), merged
field by field because the capture, co-registration and segmentation steps each
run in their own process.

    thermal        "missing" when the Lepton gave no frame this capture
    nir_filter     "ok" or "not_switching": did the IR-cut filter move between the
                   NIR-OFF and NIR-ON frames (tools/nir_filter_check.py). Not
                   switching, the NIR band is empty and the RGB bands carry
                   infrared. A capture too dark to judge takes the last lit
                   capture's verdict, named in nir_filter_from.
    segmentation   "rgb_only"  segmented from the RGB bands alone (model declares
                               bands=red,green,blue, so it never needed thermal
                               or NIR)
                   "skipped_no_thermal"  not segmented: no thermal frame and no
                               RGB-only model; camera_water_pct is absent
                   "skipped_no_nir"  the same for an empty NIR band
                   "rgb_fallback"  the loaded model reads a band this capture
                               lacks, so the daemon's RGB-only fallback made the
                               mask; segmentation_model_sha256 names that model

The bands a capture lacks are also written into its TIFFs as the INVALID_BANDS
tag (tools/coreg_multiple.py), so a copy of the TIFF says so on its own.
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


def nir_filter_stuck(directory: str) -> bool:
    return read(directory).get("nir_filter") == "not_switching"


def invalid_bands(directory: str) -> list:
    """Canonical names of the five-band TIFF's bands that hold no data this capture."""
    bands = []
    if thermal_missing(directory):
        bands.append("thermal")
    if nir_filter_stuck(directory):
        bands.append("nir")
    return bands


def tiff_tags(directory: str) -> Dict[str, str]:
    """TIFF tags naming the bands that hold no data this capture.

    INVALID_BANDS lists them by canonical name: "thermal" when the Lepton gave no
    frame (the band is zeros), "nir" when the IR-cut filter did not move (the band
    is the difference of two identical frames). OPTICAL=unfiltered says the RGB
    bands then carry infrared too: they are what the training code calls
    rgb_nofilt, not standard RGB. No tags when every band is good, so files from
    before these tags read as before.
    """
    tags = {}
    invalid = invalid_bands(directory)
    if invalid:
        tags["INVALID_BANDS"] = ",".join(invalid)
    if "nir" in invalid:
        tags["OPTICAL"] = "unfiltered"
    return tags
