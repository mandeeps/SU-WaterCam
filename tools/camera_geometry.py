"""
Shared camera geometry utilities
================================

Centralizes the conventions used across this project for:
  - Orientation (heading, pitch, roll)
  - World and camera coordinate frames
  - Rotation matrix construction

CONVENTIONS:
  World frame (ENU):
      X = East, Y = North, Z = Up
  Camera frame:
      X = right, Y = down, Z = forward (into the scene)

ANGLES:
  heading_deg : compass bearing of camera boresight (0 = North, 90 = East)
  pitch_deg   : tilt from horizontal
                  0°   = camera level (looking at horizon)
                -90°   = camera straight down
  roll_deg    : rotation around boresight
                  0°   = level, +ve = right side tilts down
"""

from __future__ import annotations

from ufonet_geo.geometry import build_rotation_matrix  # noqa: F401  (the implementation lives in the package)
