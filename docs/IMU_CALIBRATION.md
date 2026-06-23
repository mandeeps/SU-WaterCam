# WaterCam IMU Calibration Procedure

**Applies to:** All WaterCam units equipped with a BNO055 IMU  
**Script:** `tools/bno055_calibration.py`  
**Time required:** ~20 minutes pre-mount + ~10 minutes post-mount

---

## Background

The BNO055 IMU provides heading (compass bearing), pitch, and roll for the georeferencing pipeline. The heading is the most accuracy-critical value: a 2.5° heading error at 13 m slant range causes ~57 cm lateral displacement in the georeferenced flood extent.

The BNO055 has no internal non-volatile memory. Calibration offsets must be saved to disk and reloaded on every boot. Without saved offsets, the magnetometer starts uncalibrated (`mag=0`) and heading output is unreliable.

**Calibration is split into two stages because of the pole installation constraint:**

- **Stage 1 (pre-mount)** captures the magnetic signature of the camera box's own hardware — PCB traces, connectors, screws, the Raspberry Pi. This requires free rotation of the box and cannot be done once it is fixed to a pole.
- **Stage 2 (post-mount)** measures the residual heading error introduced by the mounting pole and local environment. This must be done after installation because the pole itself distorts the local magnetic field.

---

## What you need

**For Stage 1 (pre-mount):**
- Assembled WaterCam unit (fully populated PCB, all components installed)
- Raspberry Pi powered and running
- Outdoor location away from metal furniture, power supplies, and electrical panels
- Laptop or SSH session to run the calibration script

**For Stage 2 (post-mount):**
- Unit installed on its pole in the field
- viDoc RTK rover + iPhone with Pix4DCatch (or any RTK equipment)
- Two surveyed reference points visible from the camera with a known true bearing between them
- SSH session to the Pi

---

## Stage 1 — Pre-mount calibration

Run this **before** permanently mounting the unit. If the unit is already mounted, dismount it for this stage.

### 1.1 Go to the deployment site (or a nearby outdoor area)

Do not calibrate at a workbench or indoors near computers and power supplies. The hard-iron calibration captures the magnetic environment at the time of calibration — you want that environment to match where the unit will be deployed. Within 50 m of the deployment site is ideal.

### 1.2 Connect and run the calibration script

```bash
cd /home/pi/SU-WaterCam
python tools/bno055_calibration.py \
  --unit-config unit_config_UFO006.json \
  --mode calibrate
```

Replace `unit_config_UFO006.json` with the config file for the unit you are calibrating.

### 1.3 Complete the three calibration stages

The script guides you through each stage interactively.

**Stage 1/3 — Gyroscope (~5 seconds)**

Place the box on a flat, stable surface and hold it completely still. Press Enter when ready. The gyroscope reaches `status=3` in about 5 seconds.

**Stage 2/3 — Accelerometer (~2 minutes)**

You will be guided through 6 positions. Hold the box still in each position for 5 seconds:

1. Base down — box sitting normally
2. Lid down — box flipped upside-down
3. Tilt forward ~45° — front edge resting on a book or wedge
4. Tilt backward ~45° — back edge resting on a book or wedge
5. Tilt left ~45° — left edge resting on a wedge
6. Tilt right ~45° — right edge resting on a wedge

> **Note:** The antenna connectors block two of the six faces required for `accel=3`. `accel=2` is the expected ceiling on this hardware and is acceptable.

**Stage 3/3 — Magnetometer (~2 minutes)**

Pick up the box and rotate it slowly through a figure-eight pattern — as if drawing a large "8" in the air. Rules:

- Move **slowly and smoothly** — fast motion does not help
- Stay **at least 30 cm** away from metal objects and cables
- Continue rotating for at least 30 seconds, even if `mag=3` is reached early

When `mag=3` is confirmed for the minimum time, the offsets are saved automatically to `bno055_calibration.json` (or the path specified in your unit config).

### 1.4 Verify the saved file

```bash
cat bno055_calibration.json
```

Confirm the output shows `"mag": 3` in the `calibration_status` block. If it shows `"mag": 0` or `"mag": 1`, the magnetometer stage did not complete — re-run the procedure.

---

## Stage 2 — Post-mount validation

Run this **after** the unit is permanently installed on its pole. Stage 1 must be complete first.

### 2.1 Survey a known bearing

Using the viDoc RTK rover, occupy two points that are visible from the camera — for example, two painted GCP markers in the road. Record RTK-fixed coordinates for each point (≥30 s occupation, PDOP < 2.5).

Compute the true bearing from the camera's position toward one of the surveyed points. In Python:

```python
import math
lat1, lon1 = 43.15814, -76.13810   # camera position (RTK-surveyed)
lat2, lon2 = 43.15800, -76.13750   # reference point (RTK-surveyed)

dlat = math.radians(lat2 - lat1)
dlon = math.radians(lon2 - lon1)
lat1r = math.radians(lat1)
lat2r = math.radians(lat2)

x = math.sin(dlon) * math.cos(lat2r)
y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
bearing = (math.degrees(math.atan2(x, y)) + 360) % 360
print(f"True bearing: {bearing:.2f}°")
```

### 2.2 Run mount mode

```bash
python tools/bno055_calibration.py \
  --unit-config unit_config_UFO006.json \
  --mode mount \
  --known-bearing 70.3 \
  --save-correction
```

Replace `70.3` with the true bearing you computed. The `--save-correction` flag writes the measured correction directly back to `imu_heading_correction_deg` in the unit config.

The script will:
1. Load the saved calibration offsets
2. Wait for `gyro=3` (unit must be completely still — ~10 seconds)
3. Check that magnetometer offsets are active (`mag≥2`)
4. Collect 50 heading samples and compare against the known bearing
5. Compute and save `imu_heading_correction_deg`

### 2.3 Verify the result

The script prints a result like:

```
[VALIDATE] Results:
  Known true bearing  : 70.30°
  IMU mean heading    : 72.85° (after declination correction)
  Residual error      : +2.55°
  Heading std dev     : 0.142° (noise level)

  Measured imu_heading_correction_deg : -2.550°
  Updated unit_config_UFO006.json
    imu_heading_correction_deg: 0.0 → -2.55
```

A **residual error < 2°** is good. Between 2–5° is acceptable but note it in the installation record. Above 5° indicates a problem — see Troubleshooting below.

> **Copy the unit config to the Georeferencing repo.** The `imu_heading_correction_deg` value is also used by the Georeferencing pipeline's `resolve_heading()` function. Update `unit_config_UFO006.json` in the Georeferencing repository to match.

---

## Maintenance

| Event | Action required |
|---|---|
| Normal power cycle | Nothing — offsets load automatically on boot |
| Hardware change inside box (new PCB revision, repositioned sensor) | Repeat Stage 1 from scratch |
| Unit moved to a different pole or site | Repeat Stage 2 (Stage 1 offsets are still valid) |
| Mount disturbed or pole replaced | Repeat Stage 2 |
| Every ~6 months | Repeat Stage 2 to check for drift; repeat Stage 1 only if heading error has grown > 5° |

The Earth's magnetic field drifts slowly (~0.1°/year at Syracuse, NY). Annual Stage 2 re-validation is sufficient unless hardware changes.

---

## Checking calibration status at any time

```bash
python tools/bno055_calibration.py \
  --unit-config unit_config_UFO006.json \
  --mode log \
  --interval 2
```

This loads offsets and prints live calibration status alongside heading output. Watch the `cal=` field:

```
2026-06-23T10:14:32  hdg=  70.8°  pitch= -5.2°  roll=  0.3°  T=28°C  cal=3/3/2/3
                                                                           ^ ^ ^ ^
                                                               sys gyro accel mag
```

Expected when fully calibrated: `cal=3/3/2/3` (sys=3, gyro=3, accel=2, mag=3).  
If `mag < 2` after boot: the calibration file is missing or corrupted — re-run Stage 1.

---

## Troubleshooting

**`mag` stays at 0 after loading offsets**

The calibration file is missing, has the wrong path, or the offsets file has `"mag": 0` in the saved status (Stage 1 was incomplete). Re-run Stage 1 with the box off the pole.

**Residual heading error > 5° in Stage 2**

Possible causes:
- The pole is made of highly magnetic steel. Check whether the heading error sign is consistent (fixed offset) or variable (interference). A fixed offset will be fully corrected by `imu_heading_correction_deg`.
- Stage 1 magnetometer calibration was incomplete (`mag < 3` when saved). Re-run Stage 1 ensuring `mag=3` is reached before the 30-second minimum rotation time elapses.
- Incorrect magnetic declination in the unit config. Syracuse, NY is approximately −12.5° (west). Verify with NOAA's calculator for the exact site coordinates.
- The `--known-bearing` value was computed incorrectly. Double-check the RTK coordinates and bearing formula.

**Heading jumps between sessions**

The mount is not rigid — the camera box has rotated on the pole. Check physical clamping and re-run Stage 2. See the accuracy improvement plan for mount stabilization guidance.

**`gyro` does not reach 3 within 30 seconds in mount mode**

The unit is being vibrated by wind or nearby activity. Wait for calm conditions and re-run mount mode.

---

## How the corrections are applied

The full heading correction chain, from raw sensor to georeferencing pipeline:

```
Raw BNO055 heading (magnetic, sensor frame)
  + imu_mount_offset_deg      (physical rotation of sensor on PCB, e.g. +180°)
  = corrected sensor heading

  + imu_magnetic_declination_deg   (magnetic → true North, e.g. −12.5° at Syracuse)
  + imu_heading_correction_deg     (pole influence + residual, measured by Stage 2)
  = true heading (degrees from true North, clockwise)
```

The mount offset and declination are fixed values set once per unit. The heading correction is measured after each installation. All three values live in the unit config JSON and are applied automatically by the georeferencing pipeline's `unit_config.resolve_heading()` function.
