#!/home/pi/SU-WaterCam/venv/bin/python
"""
BNO055 Calibration & Orientation Logger
=========================================
Guides calibration of the BNO055 IMU, saves calibration offsets to disk,
reloads them on subsequent runs, and logs orientation data alongside image
capture timestamps for use by the georeferencing pipeline.

HARDWARE SETUP:
    BNO055 → Raspberry Pi via I2C
        VIN  → 3.3V  (Pin 1)
        GND  → GND   (Pin 6)
        SDA  → GPIO2 (Pin 3)
        SCL  → GPIO3 (Pin 5)
        PS1  → GND   (selects I2C mode; leave PS0 floating)

    Default I2C address: 0x28 (PS1=low, PS0=low)
    Alternate address:   0x29 (PS1=low, PS0=high → tie PS0 to 3.3V)

    Enable I2C on the Pi:
        sudo raspi-config → Interface Options → I2C → Enable
        sudo apt install i2c-tools python3-smbus
        i2cdetect -y 1     (should show 0x28 or 0x29)

IMPORTANT — MOUNTING NOTE:
    The BNO055 must be mounted rigidly to the camera box, not the pole.
    Any flex between the sensor and cameras introduces orientation error.
    Once mounted, do not move or re-solder the sensor — the hard-iron
    calibration (magnetometer offsets) is specific to that physical location
    because nearby ferromagnetic materials in the camera box shift the
    local magnetic field. If you remount the sensor, recalibrate from scratch.

DEPENDENCIES:
    pip install smbus2
"""

import smbus2
import struct
import time
import json
from typing import Optional
import csv
import os
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: BNO055 REGISTER MAP
# ─────────────────────────────────────────────────────────────────────────────

# I2C
BNO055_ADDRESS      = 0x28   # change to 0x29 if PS0 tied to 3.3V
I2C_BUS             = 1      # /dev/i2c-1 on all 40-pin Pi models

# Page 0 registers
REG_CHIP_ID         = 0x00   # should read 0xA0 — confirms comms working
REG_OPR_MODE        = 0x3D   # operation mode
REG_PWR_MODE        = 0x3E
REG_SYS_TRIGGER     = 0x3F
REG_UNIT_SEL        = 0x3B   # units: degrees vs radians, m/s² vs mg, etc.
REG_CALIB_STAT      = 0x35   # calibration status (read-only)
REG_SYS_STATUS      = 0x39
REG_SYS_ERR         = 0x3A
REG_TEMP            = 0x34   # on-chip temperature (°C)
REG_ACC_DATA_X_LSB  = 0x08   # accelerometer x, y, z (int16 LE; 1 LSB = 0.01 m/s² in m/s² units)

# Euler angle output (heading, roll, pitch) — 3× 2-byte signed int16
REG_EUL_HEADING_LSB = 0x1A
REG_EUL_ROLL_LSB    = 0x1C
REG_EUL_PITCH_LSB   = 0x1E

# Quaternion output — 4× 2-byte signed int16 (w, x, y, z)
REG_QUA_W_LSB       = 0x20

# Calibration offset storage — 22 bytes at 0x55–0x6A
# Accel offset XYZ (6 bytes) + Mag offset XYZ (6 bytes) +
# Gyro offset XYZ (6 bytes) + Accel radius (2 bytes) + Mag radius (2 bytes)
REG_CALIB_OFFSET_START = 0x55
CALIB_OFFSET_LEN       = 22

# Operation modes
MODE_CONFIG         = 0x00   # required to write config registers
MODE_NDOF           = 0x0C   # 9-DOF fusion: accel + gyro + mag (recommended)
MODE_IMUPLUS        = 0x08   # 6-DOF: accel + gyro only (no magnetometer)
                             # Use IMUPLUS if near strong magnetic interference
                             # (motors, large metal structures). Heading will
                             # drift over time but pitch/roll are stable.
MODE_AMG            = 0x07   # raw sensor data only (no fusion)

# Unit selection (REG_UNIT_SEL)
UNIT_DEG            = 0x00   # Euler angles in degrees (recommended)
UNIT_RAD            = 0x04

# Euler angle scale
DEG_SCALE           = 16.0   # 1 degree = 16 LSB


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: LOW-LEVEL I2C INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

class BNO055:
    """
    Minimal driver for the BNO055 via smbus2.
    Avoids the Adafruit CircuitPython/Blinka dependency chain, which can
    be unreliable on non-standard Python installations.
    """

    def __init__(self, bus_num: int = I2C_BUS, address: int = BNO055_ADDRESS,
                 mount_heading_offset_deg: float = 0.0):
        self.bus                  = smbus2.SMBus(bus_num)
        self.address              = address
        self.mount_heading_offset = mount_heading_offset_deg
        self._verify_chip()
        self._configure()

    def _read_byte(self, reg: int) -> int:
        return self.bus.read_byte_data(self.address, reg)

    def _write_byte(self, reg: int, value: int):
        self.bus.write_byte_data(self.address, reg, value)
        time.sleep(0.01)   # BNO055 needs ~7 ms after most register writes

    def _read_bytes(self, reg: int, length: int) -> bytes:
        return bytes(self.bus.read_i2c_block_data(self.address, reg, length))

    def _set_mode(self, mode: int):
        """Switch operation mode. Must go through CONFIG mode first."""
        self._write_byte(REG_OPR_MODE, MODE_CONFIG)
        time.sleep(0.025)   # 19 ms transition time per datasheet
        self._write_byte(REG_OPR_MODE, mode)
        time.sleep(0.012)   # 7 ms transition to fusion modes

    def _verify_chip(self, attempts: int = 5):
        """The chip ID must read 0xA0. A Pi's I2C sometimes garbles a read from
        the BNO055 (it stretches the clock, which the Pi handles badly: 006 read
        0xD0 once on 2026-10-17), so a wrong ID is retried before giving up."""
        seen = []
        for _ in range(attempts):
            try:
                chip_id = self._read_byte(REG_CHIP_ID)
            except OSError:
                chip_id = -1
            if chip_id == 0xA0:
                break
            seen.append(chip_id)
            time.sleep(0.05)
        if seen and chip_id == 0xA0:
            print(f"[BNO055] Chip ID read wrong {len(seen)} time(s) before 0xA0 (I2C glitch, retried)")
        if chip_id != 0xA0:
            raise IOError(
                f"BNO055 not found at 0x{self.address:02X} on bus {I2C_BUS}. "
                f"Got chip ID 0x{chip_id:02X} (expected 0xA0). "
                f"Check wiring, I2C address (PS0/PS1 pins), and that I2C "
                f"is enabled: sudo raspi-config → Interface Options → I2C"
            )
        print(f"[BNO055] Chip verified at 0x{self.address:02X}")

    def _configure(self):
        """Initial configuration: reset, set units, enter NDOF fusion mode."""
        # Reset
        self._write_byte(REG_SYS_TRIGGER, 0x20)
        time.sleep(0.65)   # 650 ms boot time after reset

        # Use degrees, m/s², Celsius
        self._set_mode(MODE_CONFIG)
        self._write_byte(REG_UNIT_SEL, UNIT_DEG)

        # Enter full 9-DOF fusion mode
        self._set_mode(MODE_NDOF)
        print("[BNO055] Initialised in NDOF mode")

    # ── Calibration status ────────────────────────────────────────────────────

    def calibration_status(self) -> dict:
        """
        Read calibration status for each sub-system.
        Returns values 0–3 for: system, gyro, accelerometer, magnetometer.
        3 = fully calibrated, 0 = uncalibrated.

        The system status reflects the lowest of the three sensor statuses.
        For reliable heading output you need mag=3.
        For reliable tilt/roll you need accel=3.
        Gyro reaches 3 quickly (a few seconds of stillness).
        """
        stat = self._read_byte(REG_CALIB_STAT)
        return {
            "system": (stat >> 6) & 0x03,
            "gyro":   (stat >> 4) & 0x03,
            "accel":  (stat >> 2) & 0x03,
            "mag":    (stat >> 0) & 0x03,
        }

    def is_fully_calibrated(self) -> bool:
        # accel=2 is acceptable (a pose or two short of 3 leaves little tilt error);
        # gyro and mag must reach 3.
        s = self.calibration_status()
        return s["gyro"] == 3 and s["accel"] >= 2 and s["mag"] == 3

    def acceleration(self) -> tuple:
        """Accelerometer x, y, z in m/s² (gravity plus any motion)."""
        data = self._read_bytes(REG_ACC_DATA_X_LSB, 6)
        return tuple(v / 100.0 for v in struct.unpack('<hhh', data))

    # ── Euler angles ──────────────────────────────────────────────────────────

    def euler_angles(self) -> dict:
        """
        Read heading, roll, and pitch in degrees.

        BNO055 Euler angle convention:
            heading : 0–360°, 0 = magnetic North, increases clockwise
            roll    : –180 to +180°, positive = right side down
            pitch   : –180 to +180°, positive = nose up

        NOTE on heading vs yaw:
            The BNO055 'heading' is a compass bearing (magnetic North reference).
            In the georeferencing script, HEADING_DEG expects a compass bearing
            in the same convention, so use heading directly.
            Magnetic declination for your site should be added to convert to
            true North if needed. Look up your declination at:
            https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml
        """
        data    = self._read_bytes(REG_EUL_HEADING_LSB, 6)
        heading = struct.unpack_from('<h', data, 0)[0] / DEG_SCALE
        heading = (heading + self.mount_heading_offset) % 360.0
        roll    = struct.unpack_from('<h', data, 2)[0] / DEG_SCALE
        pitch   = struct.unpack_from('<h', data, 4)[0] / DEG_SCALE
        return {"heading": heading, "roll": roll, "pitch": pitch}

    def quaternion(self) -> dict:
        """
        Read orientation as a quaternion (w, x, y, z).
        More numerically stable than Euler angles for interpolation.
        Useful if you want to log raw orientation and convert later.
        """
        data  = self._read_bytes(REG_QUA_W_LSB, 8)
        scale = 1.0 / (1 << 14)   # 1/16384 per datasheet
        w, x, y, z = [struct.unpack_from('<h', data, i*2)[0] * scale
                       for i in range(4)]
        return {"w": w, "x": x, "y": y, "z": z}

    def temperature(self) -> float:
        return float(self._read_byte(REG_TEMP))

    # ── Calibration offsets ───────────────────────────────────────────────────

    def read_offsets(self, attempts: int = 5) -> bytes:
        """
        Read the 22-byte calibration offset block from the sensor.
        Must be in CONFIG mode to read these registers accurately.

        Read until two reads in a row agree (an I2C glitch, see _verify_chip,
        must not end up in the saved calibration); IOError if they never do.
        """
        self._set_mode(MODE_CONFIG)
        try:
            previous = None
            for _ in range(attempts):
                try:
                    offsets = self._read_bytes(REG_CALIB_OFFSET_START, CALIB_OFFSET_LEN)
                except OSError:
                    offsets = None
                if offsets is not None and offsets == previous:
                    return offsets
                previous = offsets
                time.sleep(0.02)
            raise IOError("BNO055 calibration offsets read differently each time "
                          f"({attempts} reads): I2C unreliable, not saving them")
        finally:
            self._set_mode(MODE_NDOF)

    def write_offsets(self, offsets: bytes, attempts: int = 3):
        """
        Write previously saved calibration offsets back to the sensor.
        This restores calibration across power cycles without re-running
        the calibration procedure. Must be in CONFIG mode.

        Reads the block back and writes it again if it differs (I2C glitch);
        IOError if it never matches. The read-back stays in CONFIG mode
        (read_offsets returns to NDOF afterwards): once fusion runs, the sensor
        refines the offsets itself and reads the magnetometer radius as 0 until
        it re-confirms calibration, so a read-back in NDOF never matched
        (006 at its site, 2026-10-24).
        """
        if len(offsets) != CALIB_OFFSET_LEN:
            raise ValueError(f"Expected {CALIB_OFFSET_LEN} bytes, got {len(offsets)}")
        for _ in range(attempts):
            self._set_mode(MODE_CONFIG)
            for i, byte in enumerate(offsets):
                self._write_byte(REG_CALIB_OFFSET_START + i, byte)
            try:
                if self.read_offsets() == offsets:
                    return
            except IOError:
                pass
        raise IOError("BNO055 calibration offsets didn't read back as written: I2C unreliable")

    def close(self):
        self.bus.close()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: SAVE / LOAD CALIBRATION
# ─────────────────────────────────────────────────────────────────────────────

CALIB_FILE = "./bno055_calibration.json"

# The three calibrated parts and their bytes in the 22-byte offset block
# (0x55-0x6A): accel XYZ 0-5 + accel radius 18-19; mag XYZ 6-11 + mag radius
# 20-21; gyro XYZ 12-17. Calibrating some parts (--stages) keeps the others as
# they were in the saved file. A part never calibrated is saved as zeros and
# marked null under "components", and the loaders skip it, so bench-learned
# magnetometer offsets never reach the file (calibrate the magnetometer at a
# magnetically clean outdoor spot: --stages mag).
STAGES = ("gyro", "accel", "mag")
_SEGMENTS = {"accel": ((0, 6), (18, 20)), "mag": ((6, 12), (20, 22)), "gyro": ((12, 18),)}


def merge_offsets(primary: bytes, secondary: Optional[bytes], from_primary: set,
                  from_secondary: set) -> bytes:
    """A 22-byte offset block taking each part from `primary`, from `secondary`,
    or zeros when neither applies."""
    out = bytearray(CALIB_OFFSET_LEN)
    for part, ranges in _SEGMENTS.items():
        for a, b in ranges:
            if part in from_primary:
                out[a:b] = primary[a:b]
            elif part in from_secondary and secondary is not None:
                out[a:b] = secondary[a:b]
    return bytes(out)


def calibrated_parts(data: dict) -> set:
    """Parts a saved file holds real offsets for. Files written before
    "components" existed count as fully calibrated (unchanged behaviour)."""
    comps = data.get("components")
    if not isinstance(comps, dict):
        return set(STAGES)
    return {p for p in STAGES if comps.get(p)}

# A stage's offsets are saved only at this status or better; below it the file
# keeps that part's earlier offsets (006 saved accel offsets at status 0 once).
MIN_STATUS_TO_SAVE = {"gyro": 3, "accel": 2, "mag": 2}


def save_calibration(imu: BNO055, path: str = CALIB_FILE,
                     node_id: str = "node_1",
                     magnetic_declination_deg: float = 0.0,
                     stages: tuple = STAGES):
    """
    Save calibration offsets and metadata to a JSON file.

    PARAMETERS:
        node_id : identifier for this specific camera node.
                  Calibration is node-specific because the hard-iron offsets
                  depend on the local magnetic environment of the camera box.
        magnetic_declination_deg : local magnetic declination in degrees.
                  Positive = East (magnetic North is East of true North).
                  Look this up for your deployment site at:
                  https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml
                  Syracuse NY is approximately -12.5° (as of 2025).
                  Add this value to BNO055 heading readings to get true North.
    """
    status  = imu.calibration_status()   # read BEFORE mode switch in read_offsets()
    chip    = imu.read_offsets()

    existing, existing_parts, components = None, set(), {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                old = json.load(f)
            existing = bytes(old["offsets_bytes"])
            existing_parts = calibrated_parts(old)
            old_comps = old.get("components") if isinstance(old.get("components"), dict) else {}
            for part in existing_parts:
                components[part] = old_comps.get(part) or {"calibrated_at": old.get("timestamp")}
        except (OSError, ValueError, KeyError, TypeError):
            print(f"[CALIB] WARNING: {path} unreadable; only this run's parts are saved")
    now = datetime.now().isoformat()
    reached = {p for p in stages if status[p] >= MIN_STATUS_TO_SAVE[p]}
    for part in reached:
        components[part] = {"calibrated_at": now, "status": status[part]}
    # this run's parts that reached their minimum from the chip, earlier ones from
    # the file, the rest zeros
    offsets = merge_offsets(chip, existing, reached, existing_parts - reached)

    data = {
        "node_id":             node_id,
        "timestamp":           now,
        "calibration_status":  status,
        "magnetic_declination_deg": magnetic_declination_deg,
        "components":          {p: components.get(p) for p in STAGES},
        "offsets_hex":         offsets.hex(),
        "offsets_bytes":       list(offsets),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[CALIB] Saved to {path}")
    print(f"  Status: sys={status['system']} gyro={status['gyro']} "
          f"accel={status['accel']} mag={status['mag']}")
    for p in sorted(set(stages) - reached):
        kept = "kept its earlier offsets" if p in existing_parts else "left uncalibrated"
        print(f"  {p}: status {status[p]}, below {MIN_STATUS_TO_SAVE[p]}: not saved, {kept}")
    missing = [p for p in STAGES if not data["components"].get(p)]
    if missing:
        print(f"  Not calibrated yet (saved as zeros, skipped when loading): {', '.join(missing)}")


def load_calibration(imu: BNO055, path: str = CALIB_FILE) -> float:
    """
    Load saved calibration offsets into the sensor.

    Returns magnetic declination in degrees (for adding to heading readings).

    WHEN TO RELOAD vs RECALIBRATE:
        Reload (this function):
            - Normal power-on of a deployed node
            - Same physical mounting, same location
        Recalibrate from scratch:
            - Sensor has been physically moved or remounted
            - Camera box has been modified (new hardware added nearby)
            - Heading readings seem consistently wrong by a fixed offset
            - More than ~6 months have passed (Earth's field drifts slowly)
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No calibration file found at {path}. "
            f"Run calibration procedure first."
        )
    with open(path) as f:
        data = json.load(f)

    offsets = bytes(data["offsets_bytes"])
    parts = calibrated_parts(data)
    if parts != set(STAGES):
        # the file's parts; the chip keeps its own values for the others
        offsets = merge_offsets(offsets, imu.read_offsets(), parts, set(STAGES) - parts)
        print(f"[CALIB] Not in the file (left as the chip has them): "
              f"{', '.join(p for p in STAGES if p not in parts)}")
    imu.write_offsets(offsets)

    age = (datetime.now() -
           datetime.fromisoformat(data["timestamp"])).days
    decl = data.get("magnetic_declination_deg", 0.0)

    print(f"[CALIB] Loaded from {path}")
    print(f"  Calibrated: {data['timestamp']} ({age} days ago)")
    print(f"  Node: {data['node_id']}")
    print(f"  Magnetic declination: {decl}°")
    if age > 180:
        print(f"  WARNING: Calibration is {age} days old. "
              f"Consider recalibrating.")

    return decl


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: GUIDED CALIBRATION PROCEDURE
# ─────────────────────────────────────────────────────────────────────────────

# Six faces at 90 degrees for the accelerometer stage (see hold_accel_pose()).
ACCEL_FACES = [
    ("1/6", "Base down       -- box sitting normally on its base"),
    ("2/6", "Lid down        -- box upside-down on its lid"),
    ("3/6", "Lens end down   -- box standing on its front (lens) end, on a folded towel"),
    ("4/6", "Lens end up     -- box standing on its back end"),
    ("5/6", "Left side down  -- on its left side, at a table edge so the antennas hang clear"),
    ("6/6", "Right side down -- on its right side, likewise"),
]
POSE_MIN_S = 3            # hold at least this long, even once the pose counts
POSE_MAX_S = 10           # move on after this: a pose that counts does so in 2-3 s
ACCEL_ROUNDS = 3          # go round the six faces up to this many times
# The status doesn't rise face by face: on 006 (2026-10-21) it reached 3 only on
# going back to a face already done, after all six. One round ended at 0/3.
POSE_AXIS_SHARE = 0.9     # gravity this share on one axis is within ~25 deg of 90


def gravity_axis(vec) -> tuple:
    """(axis like '+z', share of |g| on it, |g|) for an accelerometer reading, or
    (None, 0, |g|) for a reading the Pi's I2C garbled (|g| far from 9.8)."""
    import math
    mag = math.sqrt(sum(v * v for v in vec))
    if not 6.0 <= mag <= 14.0:
        return None, 0.0, mag
    k = max(range(3), key=lambda i: abs(vec[i]))
    return ("+" if vec[k] >= 0 else "-") + "xyz"[k], abs(vec[k]) / mag, mag


def hold_accel_pose(imu, clock=time.monotonic, sleep=time.sleep, out=print) -> dict:
    """Wait while one accelerometer pose is held: until the chip's accel status
    rises (after at least POSE_MIN_S), or POSE_MAX_S. Shows the status and where
    gravity points, and says so when the box isn't near 90 degrees. Returns
    {'accel': status at the end, 'counted': rose during the pose, 'axis', 'share'}."""
    start = clock()
    first = imu.calibration_status()["accel"]
    while True:
        elapsed = clock() - start
        status = imu.calibration_status()["accel"]
        try:
            axis, share, _ = gravity_axis(imu.acceleration())
        except Exception:
            axis, share = None, 0.0
        where = f"gravity {share:4.0%} along {axis}" if axis else "gravity reading garbled"
        hint = "  <- not near 90 degrees" if axis and share < POSE_AXIS_SHARE and elapsed >= POSE_MIN_S else ""
        out(f"\r  {elapsed:4.0f}s  accel = {status}/3  {where}{hint}      ", end="", flush=True)
        counted = status > first
        if (counted and elapsed >= POSE_MIN_S) or status == 3 or elapsed >= POSE_MAX_S:
            break
        sleep(0.5)
    # the status can rise while the box is being moved into place: a face that
    # starts at 3 is done, not "not counted"
    result = "Fully calibrated" if status == 3 else "Counted" if counted else "Not counted"
    out(f"\r  {result}: accel = {status}/3"
        f" ({where})                    ")
    return {"accel": status, "counted": counted, "axis": axis, "share": share}


def run_calibration_procedure(imu: BNO055,
                               save_path: str = CALIB_FILE,
                               node_id: str = "node_1",
                               magnetic_declination_deg: float = 0.0,
                               stages: tuple = STAGES):
    """
    Guided step-by-step calibration procedure, for the given stages (default
    all three). Parts not run keep their saved offsets (see merge_offsets).
    At the lab: stages=("gyro", "accel"); at a clean outdoor spot: ("mag",).

    Each stage waits for explicit user confirmation before starting.
    The accelerometer stage walks through all 6 positions with a countdown.
    The gyroscope and magnetometer stages wait for status = 3.

    BEFORE STARTING:
        - BNO055 must already be mounted rigidly inside the camera box.
        - Calibration must be performed at the deployment site (or a
          magnetically similar outdoor location), not at a workbench near
          computers, power supplies, or large metal objects.
        - The hard-iron magnetometer offsets captured here are specific to
          this sensor's physical position inside this camera box.
          If the sensor is ever remounted, recalibrate from scratch.
    """
    # The chip counts an accelerometer pose only when gravity lies along one of its
    # axes (+-x, +-y, +-z) and the box is still: six faces at 90 degrees. Tilts of
    # 30-50 degrees count for nothing (006, 2026-10-21: lid up and lid down gave
    # accel 1/3, four tilts added nothing). hold_accel_pose() waits for each.
    ACCEL_POSITIONS = ACCEL_FACES

    print("\n" + "="*60)
    print("  BNO055 CALIBRATION PROCEDURE")
    print("="*60)
    print(f"\n  Node  : {node_id}")
    names = {"gyro": "Gyroscope", "accel": "Accelerometer", "mag": "Magnetometer"}
    print(f"  Stages: {' -> '.join(names[p] for p in stages)}")
    print(f"  Status: 0 = uncalibrated, 3 = fully calibrated\n")
    n_stages = len(stages)
    stage_no = {p: i + 1 for i, p in enumerate(stages)}
    if "mag" in stages and len(stages) < len(STAGES) and os.path.exists(save_path):
        try:
            load_calibration(imu, save_path)   # saved gyro/accel offsets help the fusion
        except Exception as e:
            print(f"[CALIB] Could not load {save_path} first: {e}")

    try:
        if "gyro" in stages:
            # ── STAGE 1: GYROSCOPE ────────────────────────────────────────────────
            print("─"*60)
            print(f"STAGE {stage_no['gyro']}/{n_stages} -- GYROSCOPE (~5 seconds)")
            print()
            print("  Place the camera box on a flat, stable surface.")
            print("  Do not touch or move the box during this stage.")
            print()
            input("  Press Enter when the box is placed and still > ")
            print()

            print("  Waiting for gyroscope calibration -- keep the box still...")
            while True:
                s = imu.calibration_status()
                print(f"\r  gyro = {s['gyro']}/3  ", end="", flush=True)
                if s["gyro"] == 3:
                    break
                time.sleep(0.2)
            print("\n  Gyroscope calibrated.\n")

        if "accel" in stages:
            # ── STAGE 2: ACCELEROMETER ────────────────────────────────────────────
            print("─"*60)
            print(f"STAGE {stage_no['accel']}/{n_stages} -- ACCELEROMETER (~1-3 minutes)")
            print()
            print("  You will be guided through 6 positions: each face of the box down,")
            print("  at 90 degrees, held still for up to 10 s each. The chip often")
            print("  finishes only on a second visit, so this may go round more than once.")
            print("  Never rest the box on an antenna: for the sides, set it at a table")
            print("  edge so the antennas hang clear, or prop it upright against books.")
            print()
            input("  Press Enter to begin > ")

            done = False
            for rnd in range(1, ACCEL_ROUNDS + 1):
                if rnd > 1:
                    print(f"\n  accel = {imu.calibration_status()['accel']}/3 after round {rnd - 1}: "
                          f"round {rnd} of {ACCEL_ROUNDS}, the same six faces again.")
                for label, description in ACCEL_POSITIONS:
                    print()
                    print(f"  Position {label}: {description}")
                    input("  Press Enter when the box is in position and stable > ")
                    hold_accel_pose(imu)
                    if imu.calibration_status()["accel"] == 3:
                        print("  Accelerometer fully calibrated; the other positions aren't needed.")
                        done = True
                        break
                if done:
                    break

            s = imu.calibration_status()
            if s["accel"] >= 2:
                print(f"\n  Accelerometer at {s['accel']}/3 -- {'fully calibrated' if s['accel'] == 3 else 'acceptable'}.")
            elif s["accel"] == 1:
                print()
                print("  WARNING: accel = 1/3. Pitch/roll readings will have larger offsets.")
                print("  Proceeding -- heading (mag) accuracy is unaffected.")
            else:
                print()
                print("  WARNING: accel = 0/3. Each face must be held still at 90 degrees:")
                print("  check the readings above showed ~95% of gravity on one axis. Proceeding anyway.")
            print()

        if "mag" in stages:
            # ── STAGE 3: MAGNETOMETER ─────────────────────────────────────────────
            print("─"*60)
            print(f"STAGE {stage_no['mag']}/{n_stages} -- MAGNETOMETER (~2 minutes)")
            print()
            print("  Pick up the camera box and rotate it slowly through a")
            print("  figure-eight pattern -- as if drawing a large '8' in the air.")
            print()
            print("  Rules:")
            print("    - Move SLOWLY and SMOOTHLY -- fast motion does not help")
            print("    - Stay at least 30 cm away from metal objects and cables")
            print("    - Do this outdoors, away from buildings and cars, not at a workbench")
            print("    - Keep rotating for at least 30 seconds even if status hits 3 early")
            print()
            input("  Press Enter when you are ready to begin rotating > ")
            print()
            print("  Rotating -- keep moving in a figure-eight...")

            MIN_ROTATE_S = 30
            rotate_start = time.monotonic()
            while True:
                s       = imu.calibration_status()
                elapsed = time.monotonic() - rotate_start
                remaining = max(0, MIN_ROTATE_S - elapsed)
                print(f"\r  mag = {s['mag']}/3  sys = {s['system']}/3  "
                      f"  elapsed = {elapsed:.0f}s  (min {remaining:.0f}s left)  ",
                      end="", flush=True)
                if s["mag"] == 3 and elapsed >= MIN_ROTATE_S:
                    break
                time.sleep(0.2)
            print("\n  Magnetometer calibrated.\n")

        # ── COMPLETE ──────────────────────────────────────────────────────────
        print("─"*60)
        time.sleep(0.5)   # let sys flag catch up after final sub-system hits 3
        s = imu.calibration_status()
        reached = [p for p in stages if s[p] >= MIN_STATUS_TO_SAVE[p]]
        print("Calibrated: " + (", ".join(names[p] for p in reached) or "nothing"))
        print(f"  sys={s['system']}  gyro={s['gyro']}  "
              f"accel={s['accel']}  mag={s['mag']}")

    except KeyboardInterrupt:
        s = imu.calibration_status()
        print(f"\n\nCalibration interrupted.")
        print(f"  Current status: sys={s['system']} gyro={s['gyro']} "
              f"accel={s['accel']} mag={s['mag']}")
        if any(s[p] < 3 for p in stages):
            print("  WARNING: Saving partial calibration. "
                  "Heading accuracy may be reduced.")

    save_calibration(imu, save_path, node_id, magnetic_declination_deg, stages)
    return imu.calibration_status()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: VALIDATE HEADING AGAINST KNOWN BEARING
# ─────────────────────────────────────────────────────────────────────────────

def validate_heading(imu: BNO055,
                     known_bearing_deg: float,
                     magnetic_declination_deg: float = 0.0,
                     n_samples: int = 50):
    """
    Compare the IMU heading against a known true bearing.

    PROCEDURE:
        1. Survey two points in the camera's field of view with the viDoc RTK
           (e.g. two painted GCPs, or a known landmark and the node itself).
        2. Compute the true bearing between them using their RTK coordinates.
        3. Point the camera directly at the far point and run this function.

    This tells you the combined heading error from:
        - Magnetic declination (systematic, fixed for a location)
        - Hard-iron offsets not fully corrected by calibration
        - Soft-iron distortion from the camera box structure

    The residual offset printed here can be added to all subsequent heading
    readings as a fine correction beyond what the BNO055 calibration alone
    provides.

    PARAMETERS:
        known_bearing_deg        : true bearing from camera to target (degrees)
        magnetic_declination_deg : local magnetic declination (from calibration file)
    """
    readings = []
    print(f"[VALIDATE] Collecting {n_samples} heading samples...")
    for _ in range(n_samples):
        h = imu.euler_angles()["heading"]
        # Apply magnetic declination to convert magnetic heading to true heading
        true_heading = (h + magnetic_declination_deg) % 360.0
        readings.append(true_heading)
        time.sleep(0.05)

    mean_h = sum(readings) / len(readings)
    std_h  = (sum((r - mean_h)**2 for r in readings) / len(readings)) ** 0.5
    error  = mean_h - known_bearing_deg

    # Wrap error to [-180, 180]
    error = (error + 180) % 360 - 180

    print(f"\n[VALIDATE] Results:")
    print(f"  Known true bearing  : {known_bearing_deg:.2f}°")
    print(f"  IMU mean heading    : {mean_h:.2f}° (after declination correction)")
    print(f"  Residual error      : {error:+.2f}°")
    print(f"  Heading std dev     : {std_h:.3f}° (noise level)")
    print()

    if abs(error) > 5.0:
        print("  WARNING: Residual error > 5°. Possible causes:")
        print("    - Magnetometer calibration incomplete (mag status < 3)")
        print("    - Strong ferromagnetic material near the sensor")
        print("    - Incorrect magnetic declination value")
        print("    - Sensor not pointing at the reference target")
    elif abs(error) > 2.0:
        print("  CAUTION: Residual error 2–5°. Acceptable for flood mapping")
        print("    but consider adding this as a fixed correction offset.")
    else:
        print("  GOOD: Residual error < 2°. Heading is well calibrated.")

    print(f"\n  To correct for this, add {-error:+.2f}° to all heading readings,")
    print(f"  or set HEADING_CORRECTION_DEG = {-error:.2f} in the logger.")
    return error


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: ORIENTATION LOGGER
# ─────────────────────────────────────────────────────────────────────────────

def run_logger(imu: BNO055,
               log_path: str = "./imu_log.csv",
               interval_s: float = 1.0,
               magnetic_declination_deg: float = 0.0,
               heading_correction_deg: float = 0.0):
    """
    Log orientation data continuously to a CSV file.

    The CSV format matches what load_imu_orientation() in the georeferencing
    script expects:
        timestamp, tilt_deg, roll_deg, yaw_deg, temp_c, calib_mag, calib_sys

    PARAMETERS:
        interval_s              : seconds between log entries
        magnetic_declination_deg: added to heading to get true North bearing
        heading_correction_deg  : residual correction from validate_heading()

    INTEGRATION WITH IMAGE CAPTURE:
        When the camera captures an image, record the timestamp in ISO format.
        Pass that timestamp to load_imu_orientation() and it will return the
        closest IMU reading, giving you the orientation at time of capture.

    CALIBRATION WATCH:
        If mag calibration drops below 2 during logging, the heading data
        becomes unreliable — this is flagged in the log and printed to console.
        This can happen if the camera box is moved near a large metal object.
        Re-run the magnetometer figure-eight if this occurs.
    """
    fieldnames = ["timestamp", "tilt_deg", "roll_deg", "yaw_deg",
                  "temp_c", "calib_sys", "calib_gyro",
                  "calib_accel", "calib_mag"]

    file_exists = os.path.exists(log_path)
    print(f"[LOGGER] Logging to {log_path}  (Ctrl+C to stop)")

    try:
        with open(log_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            while True:
                ts      = datetime.now().isoformat()
                angles  = imu.euler_angles()
                status  = imu.calibration_status()
                temp    = imu.temperature()

                # Apply declination and fine correction to get true heading
                true_heading = ((angles["heading"]
                                 + magnetic_declination_deg
                                 + heading_correction_deg) % 360.0)

                row = {
                    "timestamp":  ts,
                    "tilt_deg":   round(angles["pitch"],   3),
                    "roll_deg":   round(angles["roll"],    3),
                    "yaw_deg":    round(true_heading,      3),
                    "temp_c":     round(temp,              1),
                    "calib_sys":  status["system"],
                    "calib_gyro": status["gyro"],
                    "calib_accel":status["accel"],
                    "calib_mag":  status["mag"],
                }
                writer.writerow(row)
                f.flush()

                # Console status
                mag_warn = " ⚠ MAG UNCAL" if status["mag"] < 2 else ""
                print(f"\r  {ts}  "
                      f"hdg={true_heading:6.1f}°  "
                      f"pitch={angles['pitch']:6.1f}°  "
                      f"roll={angles['roll']:6.1f}°  "
                      f"T={temp}°C  "
                      f"cal={status['system']}/{status['gyro']}/"
                      f"{status['accel']}/{status['mag']}"
                      f"{mag_warn}  ",
                      end="", flush=True)

                time.sleep(interval_s)

    except KeyboardInterrupt:
        print(f"\n[LOGGER] Stopped. Log saved to {log_path}")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7: MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(
        description="BNO055 calibration, validation, and orientation logger.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  calibrate   Full 3-stage calibration (gyro + accel + mag). Run BEFORE mounting
              the unit on the pole, while the box is free to move. Saves offsets
              to the calibration file.

  mount       Post-installation validation. Run AFTER the unit is on the pole.
              Loads saved offsets, waits for gyro to settle, then measures the
              residual heading error against a known RTK-surveyed bearing. Use
              --save-correction to write the result back to the unit config.

  validate    One-shot heading accuracy check against a known bearing (no gyro
              wait, no config write). Useful for spot-checking after mount.

  log         Load saved calibration and log orientation to CSV continuously.

Calibration workflow (see docs/IMU_CALIBRATION.md for full procedure):
  1. Before mounting: python bno055_calibration.py -u unit_config_UFO006.json --mode calibrate
     (or in two goes: --stages gyro,accel indoors, then --stages mag outdoors)
  2. After mounting:  python bno055_calibration.py -u unit_config_UFO006.json --mode mount \\
                        --known-bearing 70.3 --save-correction
        """,
    )
    p.add_argument("--unit-config", "-u", default=None, metavar="JSON",
                   help="Path to unit config JSON (preferred over individual flags).")
    p.add_argument("--stages", default=None, metavar="LIST",
                   help="calibrate mode: comma-separated stages to run, from "
                        "gyro,accel,mag (default all). The others keep their saved "
                        "offsets. Indoors: gyro,accel; outdoors at a clean spot: mag.")
    p.add_argument("--mode", choices=["calibrate", "mount", "validate", "log"],
                   default="log", help="Operating mode (default: log).")
    p.add_argument("--node-id", default=None,
                   help="Node identifier, e.g. UFO-006. Overrides unit config.")
    p.add_argument("--declination", type=float, default=None, metavar="DEG",
                   help="Magnetic declination in degrees (positive=East). Overrides unit config.")
    p.add_argument("--mount-offset", type=float, default=None, metavar="DEG",
                   help="Mount heading offset in degrees added to raw BNO055 heading. Overrides unit config.")
    p.add_argument("--heading-correction", type=float, default=None, metavar="DEG",
                   help="Residual heading correction from validate_heading(). Overrides unit config.")
    p.add_argument("--calib-file", default=None, metavar="PATH",
                   help="Path to BNO055 calibration offset JSON. Overrides unit config.")
    p.add_argument("--log-file", default=None, metavar="PATH",
                   help="CSV output path for log mode (default: ./<node_id>_imu_log.csv).")
    p.add_argument("--known-bearing", type=float, default=None, metavar="DEG",
                   help="True bearing to reference target (required for mount and validate modes).")
    p.add_argument("--save-correction", action="store_true",
                   help="(mount mode) Write the measured heading correction back to the unit config JSON.")
    p.add_argument("--interval", type=float, default=1.0, metavar="SEC",
                   help="Log interval in seconds (log mode only, default: 1.0).")
    args = p.parse_args()
    if args.stages is not None and args.mode != "calibrate":
        # Without --mode calibrate the tool would run its default (log) mode and
        # ignore the stages, which reads like a calibration that did nothing.
        p.error(f"--stages only applies to --mode calibrate (mode is {args.mode})")

    # ── Load unit config (if provided) — read as plain JSON, no external deps ──
    ucfg: dict = {}
    if args.unit_config:
        import json as _json
        with open(args.unit_config) as _f:
            ucfg = _json.load(_f)
        print(f"[BNO055] Loaded unit config: {args.unit_config}")

    # Resolve per-unit values: CLI flag > unit config > built-in default
    node_id      = args.node_id or ucfg.get("unit_id") or "unknown"
    mount_offset = args.mount_offset if args.mount_offset is not None \
                   else float(ucfg.get("imu_mount_offset_deg") or 0.0)
    declination  = args.declination if args.declination is not None \
                   else float(ucfg.get("imu_magnetic_declination_deg") or 0.0)
    correction   = args.heading_correction if args.heading_correction is not None \
                   else float(ucfg.get("imu_heading_correction_deg") or 0.0)
    calib_file   = args.calib_file or ucfg.get("imu_calibration_file") \
                   or CALIB_FILE
    log_file     = args.log_file or f"./{node_id}_imu_log.csv"

    print(f"[BNO055] node={node_id}  mount_offset={mount_offset:+.1f}°  "
          f"declination={declination:+.1f}°  correction={correction:+.1f}°")
    print(f"[BNO055] calib_file={calib_file}  log_file={log_file}")

    # ── Initialise sensor with per-unit mount offset ──────────────────────────
    imu = BNO055(mount_heading_offset_deg=mount_offset)

    if args.mode == "calibrate":
        stages = tuple(x.strip() for x in (args.stages or ",".join(STAGES)).split(",") if x.strip())
        bad = [x for x in stages if x not in STAGES]
        if bad or not stages:
            print(f"ERROR: --stages takes some of {', '.join(STAGES)} (got {args.stages!r})")
            sys.exit(2)
        stages = tuple(x for x in STAGES if x in stages)   # always in procedure order
        run_calibration_procedure(imu, calib_file, node_id, declination, stages)

    elif args.mode == "mount":
        if args.known_bearing is None:
            print("ERROR: --known-bearing is required for mount mode.")
            print("  RTK-survey two visible points and compute the true bearing between them,")
            print("  then pass it as --known-bearing <deg>.")
            print("  See docs/IMU_CALIBRATION.md §Stage 2 for the full procedure.")
            sys.exit(1)

        print("\n" + "="*60)
        print("  POST-MOUNT VALIDATION")
        print("="*60)
        print(f"\n  Node         : {node_id}")
        print(f"  Known bearing: {args.known_bearing:.2f}°")
        print(f"  Calib file   : {calib_file}\n")

        # ── Step 1: Load saved calibration offsets ────────────────────────────
        print("─"*60)
        print("STEP 1/3 -- Loading saved calibration offsets")
        print()
        try:
            load_calibration(imu, calib_file)
        except FileNotFoundError:
            print(f"ERROR: No calibration file found at {calib_file}")
            print()
            print("  The pre-mount calibration (--mode calibrate) has not been run yet,")
            print("  or the file is at a different path.")
            print("  Run --mode calibrate with the box off the pole before using mount mode.")
            sys.exit(1)

        # ── Step 2: Wait for gyroscope to settle ──────────────────────────────
        print()
        print("─"*60)
        print("STEP 2/3 -- Waiting for gyroscope calibration")
        print()
        print("  Keep the unit completely still. Do not touch the pole.")
        print("  The gyroscope should reach status 3 within ~10 seconds.")
        print()

        GYRO_TIMEOUT_S = 30
        deadline = time.monotonic() + GYRO_TIMEOUT_S
        while time.monotonic() < deadline:
            s = imu.calibration_status()
            print(f"\r  gyro={s['gyro']}/3  accel={s['accel']}/3  mag={s['mag']}/3  "
                  f"({GYRO_TIMEOUT_S - int(time.monotonic() - (deadline - GYRO_TIMEOUT_S))}s left)  ",
                  end="", flush=True)
            if s["gyro"] == 3:
                break
            time.sleep(0.5)
        else:
            print()
            print("  WARNING: Gyroscope did not reach status 3 within 30 s.")
            print("  The unit may not be fully still, or the sensor has a fault.")
            print("  Proceeding — heading results may be less stable.")

        s = imu.calibration_status()
        print(f"\n\n  Calibration status: gyro={s['gyro']}/3  accel={s['accel']}/3  mag={s['mag']}/3")

        if s["mag"] < 2:
            print()
            print("  WARNING: Magnetometer calibration is low (mag={}/3).".format(s["mag"]))
            print("  The pre-mount calibration offsets may not have loaded correctly,")
            print("  or the Stage 1 calibration was incomplete (mag did not reach 3).")
            print("  Heading validation will proceed but the measured correction may drift")
            print("  session-to-session until Stage 1 calibration is properly completed.")
        else:
            print("  Magnetometer offsets confirmed active (mag={}/3). Proceeding.".format(s["mag"]))

        # ── Step 3: Validate heading and compute correction ───────────────────
        print()
        print("─"*60)
        print("STEP 3/3 -- Heading validation")
        print()
        error = validate_heading(imu, args.known_bearing, declination)
        measured_correction = round(-error, 3)

        print()
        print("─"*60)
        print(f"  Measured imu_heading_correction_deg : {measured_correction:+.3f}°")
        print(f"  (This absorbs pole magnetic influence + any remaining systematic offset.)")

        if args.save_correction:
            if not args.unit_config:
                print()
                print("  WARNING: --save-correction requires --unit-config. Correction not saved.")
            else:
                import json as _json2
                with open(args.unit_config) as _fc:
                    cfg_data = _json2.load(_fc)
                old_val = cfg_data.get("imu_heading_correction_deg", 0.0)
                cfg_data["imu_heading_correction_deg"] = measured_correction
                with open(args.unit_config, "w") as _fc:
                    _json2.dump(cfg_data, _fc, indent=2)
                    _fc.write("\n")
                print(f"  Updated {args.unit_config}")
                print(f"    imu_heading_correction_deg: {old_val} → {measured_correction}")
        else:
            print()
            print(f"  To save: add --save-correction, or manually set")
            print(f"    imu_heading_correction_deg = {measured_correction}")
            print(f"  in your unit config JSON.")

        print()

    elif args.mode == "validate":
        if args.known_bearing is None:
            print("ERROR: --known-bearing is required for validate mode.")
            print("  Measure the true bearing from the camera to a reference point")
            print("  using RTK coordinates, then pass it as --known-bearing <deg>.")
            sys.exit(1)
        load_calibration(imu, calib_file)
        validate_heading(imu, args.known_bearing, declination)

    elif args.mode == "log":
        load_calibration(imu, calib_file)
        run_logger(imu, log_file,
                   interval_s=args.interval,
                   magnetic_declination_deg=declination,
                   heading_correction_deg=correction)

    imu.close()
