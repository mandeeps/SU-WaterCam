#!/usr/bin/env python3
"""
DEPRECATED: this project uses a purchased 25x18-square, 30mm calib.io
checkerboard for calibration, not a printed one. This script's 9x6/24mm
pattern doesn't match it and shouldn't be printed/used. Kept for reference
only. See ../Georeferencing/camera_calibration.py, the canonical
calibration tool.

Generate a chessboard PNG for camera calibration.

This script generates a chessboard pattern sized to print on US Letter
paper (8.5 x 11 inches) at 300 DPI in **landscape** orientation.

Pattern details (matches camera_calibration.py defaults):
- Inner corners: 9 (columns) x 6 (rows)
  -> 10 x 7 squares total.
- Physical square size: ~24 mm per square edge (0.9449 inches).

At 300 DPI:
- Page size  : 11.0 x 8.5 in  -> 3300 x 2550 px
- Square size: ~0.945 in      -> 284 px (rounded)
- Board size : 10 x 7 squares -> 2840 x 1988 px

This leaves printable margins around the board. When printing, ensure:
- Page size: US Letter
- Orientation: Landscape
- Scaling: 100% (no "fit to page" / no scaling)

Usage:
    python3 tools/generate_calibration_chessboard.py
"""

from pathlib import Path

import cv2
import numpy as np


def generate_chessboard_png(
    output_path: Path,
    dpi: int = 300,
) -> None:
    # Page size (US Letter, landscape)
    width_in, height_in = 11.0, 8.5
    width_px = int(width_in * dpi)   # 3300
    height_px = int(height_in * dpi) # 2550

    # Chessboard parameters: 10 x 7 squares (9x6 inner corners)
    squares_x = 10
    squares_y = 7

    # Square size in inches (approx. 24 mm)
    square_size_in = 0.9449  # ~24 mm
    square_size_px = int(round(square_size_in * dpi))  # ~284 px

    board_w = squares_x * square_size_px
    board_h = squares_y * square_size_px

    # Center the board on the page
    margin_x = (width_px - board_w) // 2
    margin_y = (height_px - board_h) // 2

    # Start with white background
    img = np.full((height_px, width_px, 3), 255, dtype=np.uint8)

    # Draw chessboard: top-left square black
    for y in range(squares_y):
        for x in range(squares_x):
            if (x + y) % 2 == 0:
                x0 = margin_x + x * square_size_px
                y0 = margin_y + y * square_size_px
                x1 = x0 + square_size_px
                y1 = y0 + square_size_px
                cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), thickness=-1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output_path), img)
    print(f"Saved calibration chessboard PNG to: {output_path}")
    print(f"Image size: {width_px} x {height_px} px at {dpi} DPI (Letter, landscape)")
    print(f"Board size: {board_w} x {board_h} px ({squares_x} x {squares_y} squares)")
    print(f"Square size: {square_size_px} px (~{square_size_in:.3f} in)")


def main() -> None:
    output = Path("calibration_chessboard_letter_9x6_24mm.png")
    generate_chessboard_png(output_path=output, dpi=300)


if __name__ == "__main__":
    main()

