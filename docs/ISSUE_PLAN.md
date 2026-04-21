# Issue Implementation Plan

Detailed implementation plan for all open GitHub issues, organised by tier.

---

## Status Summary

| Issue | Title | Status |
|-------|-------|--------|
| #2    | Cached registration transform not used | **Needs path-normalization fix** |
| #16   | Use IP when LoRa not available | **Blocked** — IP branch not yet merged |
| #17   | Integrate calibration workflow | **Tooling exists** — extrinsics + CLI docs pending |
| #18   | Calibration applied before registration | **Needs implementation** — undistort not wired in |
| #19   | Downstream LoRa command handling | **Needs cleanup** — duplicate method, debug noise |
| #20   | IP command handling | **Blocked** — IP branch not yet merged |
| #21   | Unit ID in photo metadata | **Already done** — `_read_device_id()` in `add_metadata.py` |
| #31   | Serial port opened at import time | **Done** — `fix/issue-31-32-serial-port` |
| #32   | Serial port never closed | **Done** — `fix/issue-31-32-serial-port` |
| #33   | Race on serial readline | **Done** — `fix/issue-33-listen-lock` |
| #34   | GPS import crash | **Done** — `fix/issue-34-gps-import` |
| #35   | Bare `except:` in lora_functions | **Done** — `fix/issue-35-lora-except` |
| #36   | Config file race condition | **Done** — `fix/issue-36-config-file-lock` |
| #37   | LoRa payload bounds checking | **Done** — `fix/issue-37-lora-bounds-check` |
| #38   | Pickle file integrity | **Done** — `fix/issue-38-pickle-checksum` |
| #39   | GPS altitude on 2D fix | **Done** — `fix/issue-39-gps-mode-check` |
| #40   | Picamera2 thread safety | **Needs implementation** |
| #41   | IMU warmup silent failure | **Needs implementation** |
| #42   | LoRa callback re-entrancy | **Done** — `fix/issue-42-callback-reentrancy` |
| #43   | AHT20 CSV writes strings not floats | **Needs implementation** |
| #44   | GPS return type inconsistency | **Already done** — fixed as part of #34 |
| #45   | Hardcoded `/home/pi/SU-WaterCam/` paths | **Needs implementation** |
| #46   | Battery estimation | **Done** — merged to main |

---

## Tier 1 — Critical (already implemented in prior session)

Issues #31, #32, #33, #34, #35, #36, #37, #38, #39, #42 — see branch list above.

---

## Tier 2 — High priority (already implemented in prior session)

Issues #42, #39, #37 — see branch list above.

---

## Tier 3 — Medium priority

### #40 — Picamera2 thread safety

**File:** `tt_take_photos.py` lines 153–176

**Problem:** `take_two_photos()` reads `sq_state["picam"]` and initialises `Picamera2()` without a lock. Two concurrent calls can both observe `picam is None` and double-initialise.

**Fix:**
1. Add `import threading` near top of file.
2. Add module-level `_camera_lock = threading.Lock()`.
3. Replace the bare `if picam is None:` block with:

```python
with _camera_lock:
    if sq_state.get("picam") is None:
        picam2 = Picamera2()
        config = picam2.create_still_configuration(main={"format": "RGB888", "size": (2592, 1944)})
        picam2.configure(config)
        sq_state["picam"] = picam2
picam2 = sq_state["picam"]
```

**Branch:** `fix/issue-40-camera-lock`

---

### #41 — IMU warmup silent failure

**File:** `tools/bno055_imu.py` lines 31–40 (in `_get_sensor()`) and lines 78–86 (in `get_orientation()`)

**Problem:** Both warmup loops exit silently after 20 iterations (~2 s) with no log message if fusion never initialised.

**Fix:**
1. Add `import logging` and `logger = logging.getLogger(__name__)` at module top.
2. After each warmup loop, check if `euler` is still invalid and emit a warning:

```python
# in _get_sensor() after the warm-up for loop:
e = getattr(_sensor, 'euler', None)
if not (isinstance(e, tuple) and any(v not in (None, 0.0) for v in e)):
    logger.warning("BNO055 warmup timed out — fusion not yet initialised")

# in get_orientation() after the retry for loop:
if not (isinstance(e, tuple) and any(v not in (None, 0.0) for v in e)):
    logger.warning("BNO055 get_orientation: data still zero after retry")
```

**Branch:** `fix/issue-41-imu-warmup`

---

## Tier 4 — Low priority

### #43 — AHT20 CSV writes strings not floats

**File:** `tools/aht20_temperature.py` lines 41–43

**Problem:** The `record_csv()` function writes formatted strings (`'23.4 C'`, `'55.2 %'`) into the CSV `Temp` and `Humidity` columns. Any downstream analysis that reads the CSV expecting numeric values will fail to parse.

**Fix:**
```python
# Before:
row = {'Time': datetime.now().strftime('%Y%m%d-%H%M%S'),
       'Temp': '%0.1f C' % sensor.temperature,
       'Humidity': '%0.1f %%' % sensor.relative_humidity}
print(row)

# After:
temp_c = round(float(sensor.temperature), 1)
hum_pct = round(float(sensor.relative_humidity), 1)
print(f"Time: {datetime.now().strftime('%Y%m%d-%H%M%S')}  Temp: {temp_c} C  Humidity: {hum_pct} %")
row = {'Time': datetime.now().strftime('%Y%m%d-%H%M%S'),
       'Temp': temp_c, 'Humidity': hum_pct}
```

Also fix the hardcoded `FILE` path in the same function (see #45 below).

**Branch:** `fix/issue-43-sensor-types`

---

### #45 — Hardcoded `/home/pi/SU-WaterCam/` paths

**Problem:** Nine files contain literal `/home/pi/SU-WaterCam/` paths that break on any non-`pi` deployment.

**Strategy:** Import `_infer_repo_root()` from `button_hold_camera.py` or replicate the same pattern using `Path(__file__).resolve()` + `WATERCAM_REPO` env-var override (matching the existing pattern in `button_hold_camera.py`).

For **`tools/`** files (one level below repo root):
```python
from pathlib import Path
import os
_REPO_ROOT = Path(os.environ.get("WATERCAM_REPO", str(Path(__file__).resolve().parent.parent)))
```

For **root-level** scripts:
```python
from pathlib import Path
import os
_REPO_ROOT = Path(os.environ.get("WATERCAM_REPO", str(Path(__file__).resolve().parent)))
```

**Files and specific changes:**

| File | Line(s) | Change |
|------|---------|--------|
| `tools/bno055_imu.py` | 92 | `OFFSET_PATH = str(_REPO_ROOT / "data" / "imu_offsets.txt")` |
| `tools/bno08x_imu.py` | 51 | same pattern |
| `tools/aht20_temperature.py` | 33 | `FILE = str(_REPO_ROOT / "data" / "temp_humidity.csv")` |
| `tools/take_nir_photos.py` | 47, 50, 52, 55 | `str(_REPO_ROOT / "capture")`, etc. |
| `tools/take_nir_photos.py` | 132 (main guard) | `filepath = str(_REPO_ROOT / "images" / "")` |
| `tools/watercam.py` | 30, 32–34 | `_REPO_ROOT / "images"`, segformer paths |
| `tools/take_photo.py` | 33 (main guard) | `filepath = str(_REPO_ROOT / "images" / "")` |
| `ticktalk_main.py` | 633, 640 | `str(Path(os.environ.get("WATERCAM_REPO", ...)) / "images" / date)` |
| `ticktalk_main.py` | 658–661 | segformer Python/script paths from env vars |
| `button-service-gpiozero.py` | 15 | `str(_REPO_ROOT / "tools" / "take_nir_photos.py")` |

**Service files** (`config/*.service`): left as-is — these are deploy-time config files that require site-specific paths.

**Branch:** `fix/issue-45-hardcoded-paths`

---

## Tier 5 — Enhancement / Research

### #2 — Cached registration transform: trailing slash edge case

**File:** `tools/coreg_multiple.py` lines 152–153 and 165–166

**Problem:** `os.path.dirname("path/to/dir/")` returns `"path/to/dir"` (same dir), not the parent. When `directory` ends with `/`, `save_transform_parameters` writes the cache into the wrong location relative to `load_transform_parameters`.

**Fix:**
```python
# save_transform_parameters(), line 152:
parent_directory = os.path.dirname(os.path.normpath(directory)) or os.path.normpath(directory)

# load_transform_parameters(), line 165:
parent_directory = os.path.dirname(os.path.normpath(directory)) or os.path.normpath(directory)
```

**Branch:** `fix/issue-2-transform-cache`

---

### #17 — Integrate calibration workflow

**Status:** `tools/camera_calibration.py` and `tools/generate_calibration_chessboard.py` already exist and compute + save intrinsics. Gap is extrinsic collection and a guided workflow doc.

**Remaining work (out of scope for code fixes — tracked in issue):**
- Extrinsic parameter collection tooling
- Installation guide documenting the calibration capture procedure

**No code change needed in this cycle.** Tracked as documentation/workflow gap.

---

### #18 — Apply calibration before registration

**File:** `tools/coreg_multiple.py`

**Problem:** `coreg_multiple.py` never calls `cv2.undistort()`. The `camera_calibration.json` produced by `tools/camera_calibration.py` is ignored.

**Fix:** Add `_undistort_if_calibrated(image, calib_path)` helper and call it on both optical frames before passing them to `mutual_information_registration()`.

```python
def _undistort_if_calibrated(image: np.ndarray, calib_path: str) -> np.ndarray:
    if not os.path.exists(calib_path):
        return image
    try:
        with open(calib_path) as f:
            cal = json.load(f)
        mtx = np.array(cal["camera_matrix"])
        dist = np.array(cal["dist_coefficients"])
        h, w = image.shape[:2]
        new_mtx, _ = cv2.getOptimalNewCameraMatrix(mtx, dist, (w, h), 1, (w, h))
        return cv2.undistort(image, mtx, dist, None, new_mtx)
    except Exception as e:
        print(f"Warning: calibration load failed ({e}), skipping undistort")
        return image
```

Call site in `mutual_information_registration()` (and `apply_cached_transform()`):
```python
calib_path = os.path.join(os.path.dirname(fixed_image_path), "camera_calibration.json")
fixed_image_cv = _undistort_if_calibrated(fixed_image_cv, calib_path)
moving_image_cv = _undistort_if_calibrated(moving_image_cv, calib_path)
```

Also add `--calibration-file` CLI argument to allow specifying a non-default calibration JSON.

**Branch:** `feature/issue-18-apply-calibration`

---

### #19 — LoRa downstream command dispatch cleanup

**File:** `tools/lora_runtime_integration.py`

**Problems:**
1. `process_lora_payload` is **defined twice** (Python keeps the second; first is dead code). The first stub (lines ~219–221 on main) returns `self.process_lora_command(payload)` which calls the old 2-argument method with only 1 arg — a `TypeError` at runtime.
2. `DEBUG:` prefix in `process_lora_payload` will print every received payload in production.
3. `_init_lora_handler` emits verbose emoji debug prints on every startup.
4. The import fallback block emits debug prints including full file listings.

**Fix:**
1. Remove the first `process_lora_payload` definition entirely.
2. Remove `print(f"DEBUG: ...")` lines from `process_lora_payload` and `_apply_command_tlv`.
3. Replace emoji `print()` calls in `_init_lora_handler` with a single `print("LoRa runtime integration initialised")` on success and `print("LoRa unavailable: ...")` on failure.
4. In the import fallback block, remove the verbose directory listing prints; keep only the `raise` on final failure.

**Branch:** `fix/issue-19-lora-dispatch`
