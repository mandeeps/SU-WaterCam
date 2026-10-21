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

### 1.1 Go to a magnetically clean outdoor spot

Do not calibrate at a workbench or indoors near computers, power supplies, steel furniture or rebar: their fields end up in the offsets. The site itself isn't required. Any open outdoor area at least 5-10 m from cars, buildings, metal fences and power lines works, for example near the lab.

Why: the hard-iron offsets correct the unit's own field (battery cells, screws, connectors, boards), which goes wherever the unit goes. What the site adds (the pole, nearby metal) is fixed, and since a mounted camera never rotates, it appears as a single constant heading offset. Stage 2 measures that offset at the site and removes it.

Calibrate the unit fully assembled and powered as deployed (battery in place, antennas on, lid closed).

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

**Stage 2/3 — Accelerometer (~1-3 minutes)**

You will be guided through 6 positions: each face of the box down, at 90°, held
completely still. The tool shows the accelerometer status and where gravity points
("gravity 97% along -z"), and moves on once the status rises (after at least 3 s) or
after 10 s. The chip often finishes only on going back to a face it has seen, so the
tool goes round the six faces again, up to three times, until `accel=3`:

1. Base down — box sitting normally
2. Lid down — box upside-down on its lid
3. Lens end down — box standing on its front (lens) end, on a folded towel
4. Lens end up — box standing on its back end
5. Left side down — on its left side, at a table edge so the antennas hang clear
6. Right side down — on its right side, likewise

The chip counts a pose only when gravity lies along one of its axes (±x, ±y, ±z),
so each face must be close to 90°: the tool flags a reading with less than 90% of
gravity on one axis ("not near 90 degrees"). Tilts of 30-50° count for nothing; on
006 (2026-10-21) lid up and lid down reached `accel=1`, and four 45° tilts added
nothing. Never rest the box on an antenna: for the sides, set it at a table edge so
the antennas hang over it, or prop it upright against a stack of books. Once
`accel=3`, the remaining positions are skipped.

If the unit loses power when tilted (it reboots, and the Witty Pi log says
"power supply newly connected"), a connector or the battery is shifting: fix
that first, since a pole-mounted camera tips forward too.

The tool retries a chip-ID read the Pi's I2C garbled, and only saves offsets
that read the same twice in a row; if they never do it refuses to save, so a
glitch can't end up in the calibration.

> **Note:** `accel=3` needs all six faces. If a face can't be reached without loading an antenna, `accel=2` is acceptable: it leaves a little more pitch and roll error.

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
lat1, lon1 = 40.71280, -74.00600   # camera position (RTK-surveyed; placeholder)
lat2, lon2 = 40.71266, -74.00540   # reference point (RTK-surveyed; placeholder)

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

> **Copy the values to where headings are corrected.** The node sends raw headings (`tools/bno055_imu.py` applies no offset or declination); corrections are applied downstream:
> - **API:** the device's pose config (`PUT /devices/{id}/pose-config`; the dashboard's georeference page) takes `imu_mount_offset_deg`, `imu_magnetic_declination_deg` and `imu_heading_correction_deg` (`app/georef_math.py` adds the three). Enter the unit config's values there.
> - **Georeferencing:** its pipeline's `resolve_heading()` uses the same values. Update `unit_config_UFO006.json` in the Georeferencing repository to match.

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

Expected when fully calibrated: `cal=3/3/3/3` (sys, gyro, accel, mag); `accel=2` is acceptable.  
If `mag < 2` immediately after boot but climbs to 3 within a few seconds on its
own: that's normal — see the note below. If it stays below 2 for longer than
that, the calibration file is missing or corrupted — re-run Stage 1.

**Note on the first few seconds after boot:** writing saved offsets to the
sensor does not instantly restore `calibration_status` to its saved value —
the BNO055's own fusion algorithm re-earns that confidence over a short
window of live operation (typically a few seconds), even though the offsets
themselves are already correct. `tools/bno055_imu.py`'s `_get_sensor()` waits
up to 5 seconds for `mag` to reconfirm before returning, specifically to
avoid the very first photo after boot spuriously tripping
`add_metadata.py`'s "magnetometer uncalibrated" warning. If you see that
warning on a unit with valid, fully-calibrated (`mag: 3`) saved offsets, it
most likely already resolved a few seconds later — check the *next* photo's
EXIF/log rather than assuming calibration itself failed.

---

## Troubleshooting

**`accel` stays at 0 or 1**

What the BNO055 needs, from watching its status and raw accelerometer once a
second on 006 (2026-10-21, NDOF mode, the box in the case):

- **Only faces at 90° count.** A pose counts when gravity lies along one of
  the chip's axes (±x, ±y, ±z): the tool's reading shows about 95% or more of
  gravity on one axis ("gravity 97% along -z"). Lid up and lid down took it to
  `accel=1`. Four tilts of 29-50°, each held 15-25 s, added nothing. With all
  six faces at 90° it reached `accel=3`, and so did `sys`.
- **The status doesn't rise one face at a time.** It went 0 → 1 on the first
  side, fell back to 0 on another, and jumped from 1 to 3 only once all six
  axes had been covered. So a face that shows "Not counted" isn't wasted:
  keep going through all six.
- **A face counts within 2-3 s of being still,** or not at all on that visit:
  holding longer doesn't help, so the tool waits at most 10 s per face. One
  round of six faces with the tool ended at `accel=0` (006, 2026-10-21); the
  diagnostic run reached 3 on going back to lid down, so the tool now goes
  round up to three times.
- **Offsets are saved only when the stage reached its minimum** (gyro 3,
  accel 2, mag 2). Below that the file keeps the part's earlier offsets and
  the tool says so: the first 90° run on 006 saved accelerometer offsets at
  status 0 before this rule.
- **The accelerometer itself is fine** when every steady pose reads about
  9.8 m/s² in total. If all three axes stay near 0 or don't change between
  faces, it's the sensor or its wiring, not the procedure.
- **Odd readings near 0 on all three axes** (`-0.04 -0.01 -0.01`) are I2C
  reads the Pi garbled, a few times a minute on 006. The tool shows them and
  ignores them.
- **The box's base isn't level for the sensor:** base down, 006's BNO055 saw
  gravity 7-13° off its z axis (lid down, about 7°). That's the sensor's
  mounting angle; Stage 2 measures it.
- **Moving the box indoors also raises `mag`** (to 3 on 006). Don't keep
  that: bench magnetometer offsets are wrong outdoors. Run the bench stages
  as `--stages gyro,accel`, which saves only those parts, and do `--stages
  mag` outdoors.
- **The calibration lives in the chip until it is reset.** The tool resets
  the chip when it starts, so calibrate with the tool, not by moving the box
  while another program reads it.

**`mag` stays at 0 after loading offsets**

First check whether it's just slow to reconfirm rather than genuinely broken
— see the note in the previous section; this can take a few seconds even
with valid saved offsets. If it's still 0 well after that: the calibration
file is missing, has the wrong path, or the offsets file itself has
`"mag": 0` in the saved status (Stage 1 was incomplete). Re-run Stage 1 with
the box off the pole.

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
