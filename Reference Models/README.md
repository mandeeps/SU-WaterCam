# SU-WaterCam Bounding Box Models

All boxes are centered at XY origin, Z from 0 (PCB bottom face) upward.
Import individually into your 3D application and position/constrain manually.

| File | L (mm) | W (mm) | H (mm) | Notes |
|---|---|---|---|---|
| Raspberry_Pi_4B.step | 85.0 | 56.0 | 17.0 | Height to top of USB-A stack. Official mechanical PDF at datasheets.raspberrypi.com/rpi4/raspberry-pi-4-mechanical-drawing.pdf |
| WittyPi_4.step | 65.0 | 30.0 | 12.0 | Covers partial GPIO header area. STEP file (likely real): uugear.com/repo/WittyPi4/WittyPi4.step |
| FLIR_Lepton_3_5.step | 11.8 | 12.7 | 7.2 | Module only, seats in Breakout v2 ZIF socket. Per FLIR datasheet. |
| FLIR_Lepton_Breakout_v2.step | 38.0 | 38.0 | 8.5 | Community STEP on GrabCAD (note: socket rotated 90 deg from physical in that model). |
| Adafruit_BNO055_2472.step | 27.0 | 20.3 | 4.0 | Official CAD: github.com/adafruit/Adafruit_CAD_Parts/tree/main/2472%20BNO055%20Breakout |
| Adafruit_BNO085_4754.step | 20.3 | 17.8 | 3.5 | Community STEP: grabcad.com/library/adafruit-bno085-9-dof-imu-stemma-qt-1 |
| Adafruit_AHT20_4566.step | 17.8 | 12.7 | 3.5 | Check Adafruit_CAD_Parts repo for STEP; may not be published yet. |
| Quectel_EC25_miniPCIe.step | 51.0 | 30.0 | 4.9 | Per EC25 hardware design guide. Community STEP: grabcad.com/library/quectel-ec25-1 |
| Multitech_mDot_915.step | 33.0 | 25.0 | 3.0 | Per mDot datasheet dimensional drawing. Full STEP requires MultiTech support ticket. |

## Assembly notes

- The Pi 4B and WittyPi 4 stack vertically (WittyPi GPIO header adds ~8mm gap between boards).
- The FLIR Lepton module sits inside the Breakout v2 board; their Z heights overlap.
- The Quectel EC25 is connected via USB adapter, not soldered to the Pi.
- The mDot connects via 2mm-pitch jumper wires to Pi GPIO.

## Dimension sources

- Raspberry Pi 4B: official mechanical drawing PDF
- WittyPi 4: product page photos + KiCad files in github.com/uugear/Witty-Pi-4
- FLIR Lepton 3.5: FLIR Lepton 3.5 datasheet (Teledyne FLIR)
- FLIR Breakout v2: GrabCAD model + product page
- Adafruit boards: measured from Adafruit CAD Parts repo files
- Quectel EC25: EC25 Hardware Design guide (Quectel)
- Multitech mDot: mDot datasheet dimensional drawing
