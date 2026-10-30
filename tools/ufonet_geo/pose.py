"""
Camera pose resolution from a unit config and a photo's EXIF.

A unit config is a dict with the unit_config.py JSON schema (the API stores
the same keys as a device's pose_config): mount_height_m, camera_lat,
camera_lon, heading_deg, pitch_deg, roll_deg and the imu_* corrections.

Precedence (highest first): explicit override, unit config, EXIF.
unit_config.UnitConfig wraps these functions for the CLI tools.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Mapping, Optional, Tuple

log = logging.getLogger(__name__)

# EXIF yaw (BNO055 fused heading) is trusted only when the magnetometer
# calibration status is at its maximum (0-3 scale, 3 = fully calibrated).
MIN_CALIB_MAG_FOR_EXIF_YAW = 3


class HeadingUnavailableError(ValueError):
    """No trustworthy camera heading could be resolved."""


def _get(cfg: Mapping, key: str) -> Optional[float]:
    v = cfg.get(key)
    return float(v) if v is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# EXIF UserComment (SU-WaterCam add_metadata.py)
# ─────────────────────────────────────────────────────────────────────────────

_IMU_RE = re.compile(
    r"Roll\s+([-\d.]+)\s+Pitch\s+([-\d.]+)\s+Yaw\s+([-\d.]+)",
    re.IGNORECASE,
)
_CALIB_RE = re.compile(r"CalibMag\s+(\d+)", re.IGNORECASE)
_NUM = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"
_QUAT_RE = re.compile(r"Quaternion\s+" + ",".join([_NUM] * 4) , re.IGNORECASE)
_GRAV_RE = re.compile(r"Gravity\s+" + ",".join([_NUM] * 3), re.IGNORECASE)
_POSE_TIME_RE = re.compile(r"PoseTime\s+(\S+)", re.IGNORECASE)
_GPSFIX_RE = re.compile(r"GPSFix\s+(\w+)(?:\s+AgeS\s+" + _NUM + ")?", re.IGNORECASE)


def parse_user_comment(txt: str) -> dict:
    """
    Parse the SU-WaterCam UserComment string. Tolerates old photos (only
    "Roll R Pitch P Yaw Y") and the newer extra words:
      CalibMag n | Quaternion w,x,y,z | Gravity x,y,z | PoseTime <ISO> |
      GPSFix CACHED AgeS n
    Missing items are None. Returns keys: roll_deg, pitch_deg, yaw_deg,
    calib_mag, quaternion (w,x,y,z), gravity (x,y,z), pose_time,
    gps_fix_source, gps_fix_age_s.
    """
    out = {"roll_deg": None, "pitch_deg": None, "yaw_deg": None,
           "calib_mag": None, "quaternion": None, "gravity": None,
           "pose_time": None, "gps_fix_source": None, "gps_fix_age_s": None}
    m = _IMU_RE.search(txt)
    if m:
        out["roll_deg"], out["pitch_deg"], out["yaw_deg"] = (
            float(m.group(1)), float(m.group(2)), float(m.group(3)))
    m = _CALIB_RE.search(txt)
    if m:
        out["calib_mag"] = int(m.group(1))
    m = _QUAT_RE.search(txt)
    if m:
        out["quaternion"] = tuple(float(g) for g in m.groups())
    m = _GRAV_RE.search(txt)
    if m:
        out["gravity"] = tuple(float(g) for g in m.groups())
    m = _POSE_TIME_RE.search(txt)
    if m:
        out["pose_time"] = m.group(1)
    m = _GPSFIX_RE.search(txt)
    if m:
        out["gps_fix_source"] = m.group(1).upper()
        if m.group(2) is not None:
            out["gps_fix_age_s"] = float(m.group(2))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_position(cfg: Mapping,
                     override_lat: Optional[float] = None,
                     override_lon: Optional[float] = None,
                     exif_lat: Optional[float] = None,
                     exif_lon: Optional[float] = None,
                     ) -> Tuple[Optional[float], Optional[float], str]:
    """
    Return (lat, lon, source_label).
    Source: 'cli' > 'unit_config' > 'exif' > (None, None, 'none')

    A surveyed position in the unit config (RTK, ~cm) beats the unit's own
    GPS fix in EXIF (several metres of noise).
    """
    if override_lat is not None and override_lon is not None:
        return float(override_lat), float(override_lon), "cli"
    lat, lon = _get(cfg, "camera_lat"), _get(cfg, "camera_lon")
    if lat is not None and lon is not None:
        return lat, lon, "unit_config"
    if exif_lat is not None and exif_lon is not None:
        return float(exif_lat), float(exif_lon), "exif"
    return None, None, "none"


def resolve_heading(cfg: Mapping,
                    override: Optional[float],
                    exif_yaw: Optional[float],
                    calib_mag: Optional[int] = None) -> Tuple[float, str]:
    """
    Return (heading_deg, source_label).
    Source: 'cli' > 'unit_config' > 'exif_yaw_corrected' | 'exif_yaw_uncalibrated'

    There is deliberately NO further fallback. GPS track (course over
    ground) is meaningless for a fixed camera, and a silent 0 deg (north)
    gives a wrong georeference that looks plausible. With no configured
    heading and no trustworthy EXIF yaw this raises HeadingUnavailableError.

    calib_mag is the BNO055 magnetometer calibration status (0-3) from
    the EXIF UserComment ("CalibMag n"), or None for older photos that do
    not record it.
      * calib_mag == MIN_CALIB_MAG_FOR_EXIF_YAW (3): EXIF yaw is used.
      * calib_mag < 3: the magnetometer was not fully calibrated. A
        configured/surveyed heading is preferred; without one, EXIF yaw
        is still the best estimate and is used, with the source marked
        "exif_yaw_uncalibrated" and a loud warning, so the result is
        known to be unverified.
      * calib_mag is None: calibration unknown; EXIF yaw is used with a
        warning.

    When heading comes from EXIF, per-unit IMU corrections are applied:
    mount offset, magnetic declination, and post-calibration residual.
    Override and unit_config values are assumed to already be true headings.
    """
    if override is not None:
        return float(override), "cli"
    heading = _get(cfg, "heading_deg")
    if heading is not None:
        if (exif_yaw is not None and calib_mag is not None
                and calib_mag < MIN_CALIB_MAG_FOR_EXIF_YAW):
            log.warning(
                "EXIF yaw ignored: magnetometer CalibMag=%s (< %d). "
                "Using configured heading %.1f deg.",
                calib_mag, MIN_CALIB_MAG_FOR_EXIF_YAW, heading)
        return heading, "unit_config"
    if exif_yaw is None:
        raise HeadingUnavailableError(
            "No camera heading available: none given on the command line "
            "or in the unit config (heading_deg), and the photo has no "
            "EXIF yaw. Set a surveyed heading_deg in the unit config or "
            "pass --heading. (GPS track is not used: a fixed camera has "
            "no course over ground.)")
    source = "exif_yaw_corrected"
    if calib_mag is not None and calib_mag < MIN_CALIB_MAG_FOR_EXIF_YAW:
        log.warning(
            "HEADING UNCALIBRATED: magnetometer CalibMag=%s (need %d) and no "
            "heading_deg in the unit config / --heading; using EXIF yaw as the "
            "best estimate. Set a surveyed heading_deg for a reliable result.",
            calib_mag, MIN_CALIB_MAG_FOR_EXIF_YAW)
        source = "exif_yaw_uncalibrated"
    elif calib_mag is None:
        log.warning(
            "Magnetometer calibration status (CalibMag) not recorded in "
            "this photo; trusting EXIF yaw unverified. Prefer a surveyed "
            "heading_deg in the unit config.")
    imu_offset = ((_get(cfg, "imu_mount_offset_deg") or 0.0)
                  + (_get(cfg, "imu_magnetic_declination_deg") or 0.0)
                  + (_get(cfg, "imu_heading_correction_deg") or 0.0))
    return (float(exif_yaw) + imu_offset) % 360.0, source


def resolve_pitch_roll(cfg: Mapping,
                       override_pitch: Optional[float],
                       override_roll: Optional[float],
                       exif_pitch: Optional[float],
                       exif_roll: Optional[float],
                       ) -> Tuple[float, float, str]:
    """
    Return (pitch_deg, roll_deg, source_label).
    Source: 'cli' > 'unit_config' > 'exif_corrected' > 'exif' > 0.0

    Raw EXIF pitch comes straight from the BNO055 hardware Euler register
    (SU-WaterCam's bno055_imu.py / add_metadata.py write it unmodified,
    by design). Its native sign convention is the OPPOSITE of this
    module's (0deg=level, -90deg=straight down) — confirmed empirically
    2026-07-15 via RTK-validated GCP refinement on two independent units
    (imu_mount_offset_deg=0 and =180): the raw/passthrough pitch pointed
    the camera above the horizon in both cases.

    Root cause (confirmed 2026-07-16 against the Bosch datasheet,
    BST-BNO055-DS000-18 rev1.8, Table 3-13 "Rotation angle conventions",
    p.32, and the UNIT_SEL register default in the Page-0 register map,
    ~p.56): the BNO055 has two selectable Euler output formats, Android
    vs. Windows, and PITCH is defined with opposite sign between them
    ("turning clockwise decreases values" in Android vs. "increases
    values" in Windows). UNIT_SEL powers on to 0x80 (Android format,
    bit 7 set) and the vendored `adafruit_bno055` driver never writes
    UNIT_SEL, so the sensor stays in Android format — opposite of what
    this module assumes. Roll and Heading/Yaw are explicitly
    format-independent per the same table (identical convention in both
    formats), which is why only pitch needs this correction; empirically
    confirmed too (2026-07-16 roll-isolation test against RTK ground
    truth showed no sign inversion for roll).

    So raw EXIF pitch is negated first, before the mount-offset rotation
    below is applied. Roll is left as-is.

    When both values come from EXIF, the mount rotation is then applied:
        corrected_pitch = pitch * cos(θ) − roll * sin(θ)
        corrected_roll  = pitch * sin(θ) + roll * cos(θ)
    where θ = imu_mount_offset_deg and pitch is the sign-corrected value
    above. This un-does the apparent pitch/roll swap introduced by
    mounting the sensor at an arbitrary yaw angle relative to the camera
    body.

    Override and unit_config values are assumed to already be in camera frame.
    """
    if override_pitch is not None or override_roll is not None:
        p = float(override_pitch) if override_pitch is not None else 0.0
        r = float(override_roll) if override_roll is not None else 0.0
        return p, r, "cli"
    cfg_pitch, cfg_roll = _get(cfg, "pitch_deg"), _get(cfg, "roll_deg")
    if cfg_pitch is not None or cfg_roll is not None:
        return (cfg_pitch if cfg_pitch is not None else 0.0,
                cfg_roll if cfg_roll is not None else 0.0,
                "unit_config")
    if exif_pitch is not None and exif_roll is not None:
        theta = math.radians(_get(cfg, "imu_mount_offset_deg") or 0.0)
        p = -float(exif_pitch)
        r = float(exif_roll)
        corrected_pitch = p * math.cos(theta) - r * math.sin(theta)
        corrected_roll = p * math.sin(theta) + r * math.cos(theta)
        return corrected_pitch, corrected_roll, "exif_corrected"
    # Partial EXIF — only one axis available, pass through without rotation
    if exif_pitch is not None:
        return -float(exif_pitch), 0.0, "exif"
    if exif_roll is not None:
        return 0.0, float(exif_roll), "exif"
    return 0.0, 0.0, "default"


def resolve_mount_height(cfg: Mapping, override: Optional[float] = None) -> Tuple[Optional[float], str]:
    """Return (mount_height_m, source_label): 'cli' > 'unit_config' > (None, 'none')."""
    if override is not None:
        return float(override), "cli"
    height = _get(cfg, "mount_height_m")
    if height is not None:
        return height, "unit_config"
    return None, "none"
