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

import numpy as np


def build_rotation_matrix(heading_deg: float,
                          pitch_deg: float,
                          roll_deg: float) -> np.ndarray:
    """
    Build rotation matrix R (world ENU → camera frame) from orientation.

    APPROACH — construct camera axes directly in ENU space:
        1. Compute camera boresight (Z axis) in ENU from heading + pitch
        2. Compute camera right (X axis) in ENU from heading
        3. Apply roll about boresight
        4. Compute camera down (Y axis) = Z × X
        5. Stack as columns → R_cam_to_world (camera→world ENU)
        6. Transpose → R (world→camera)
    """
    H = np.radians(heading_deg)
    P = np.radians(pitch_deg)
    r = np.radians(roll_deg)

    # Step 1: boresight (forward, Z) in ENU
    forward = np.array([
        np.sin(H) * np.cos(P),  # East component
        np.cos(H) * np.cos(P),  # North component
        np.sin(P),              # Up (negative when tilting down)
    ])

    # Step 2: camera right (X) in ENU — perpendicular to heading, in horizontal plane
    right = np.array([
        np.cos(H),  # East
        -np.sin(H), # North
        0.0,        # Up
    ])

    # Step 3: roll about boresight
    if abs(roll_deg) > 1e-6:
        cos_r = np.cos(r)
        sin_r = np.sin(r)
        right = cos_r * right + sin_r * np.cross(forward, right)

    # Step 4: camera down (Y) = Z × X
    down = np.cross(forward, right)

    # Normalize axes
    right = right / np.linalg.norm(right)
    down = down / np.linalg.norm(down)
    forward = forward / np.linalg.norm(forward)

    # Step 5–6: camera→world then transpose
    R_cam_to_world = np.column_stack([right, down, forward])
    R_world_to_cam = R_cam_to_world.T
    return R_world_to_cam

