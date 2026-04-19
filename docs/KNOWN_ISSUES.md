# Known Issues & Improvement Backlog

Findings from a codebase audit conducted 2026-04-19. Issues are grouped by severity.

---

## Critical — System Failure

### Serial port opened at import, never closed
**File:** `tools/lora_handler.py:34`
Global `ser` is created at module load time and never closed. Blocks other processes from accessing the port; causes resource exhaustion on repeated imports.
**Fix:** Use a context manager or close in a shutdown handler.

### Serial port open failure is non-fatal
**File:** `tools/lora_handler.py:44`
If `ser.is_open` is false after init, code prints a warning and continues. Downstream `transmit()` calls then fail with confusing errors.
**Fix:** Raise an exception or return an error status instead of continuing.

### Listen loop reads serial without transmit lock
**File:** `tools/lora_handler_concurrent.py:168`
`_listen_loop()` reads from the serial port without acquiring `transmit_lock`. Concurrent read and write operations corrupt messages.
**Fix:** Acquire the same lock in `_listen_loop()` during serial reads.

### `gpsd.connect()` called at module import with no error handling
**File:** `tools/get_gps.py:8`
If the gpsd daemon is not running, importing this module raises an uncaught exception and crashes the caller.
**Fix:** Wrap in `try/except`, set an `_gps_available` flag, return empty data gracefully.

### Bare `except:` and call to undefined `killScript()`
**File:** `tools/lora_functions.py:93`
A bare `except:` swallows `SystemExit` and `KeyboardInterrupt`. The handler then calls `killScript()`, which is not defined, raising `NameError`.
**Fix:** Catch specific exceptions (e.g., `serial.SerialException`, `IndexError`). Define or import `killScript()`.

---

## High — Data Loss or Corruption

### No file lock on LoRa config read/write
**File:** `tools/lora_handler_concurrent.py:142–154`
`load_config()` and `save_config()` have no file-level lock. Two processes writing `lora_config.json` simultaneously will corrupt it.
**Fix:** Use `fcntl.flock()` around file open/write.

### Received LoRa payload parsed without bounds checking
**File:** `tools/lora_runtime_integration.py:545`
Channel, command, and value fields are extracted from the payload without range validation. A malformed packet can set out-of-range thresholds or frequencies.
**Fix:** Validate parsed integers against expected ranges before applying them.

### `pickle.load()` on graph file without source validation
**Files:** `ticktalk_main.py`, `runrtm.py`
The TickTalkPython graph is loaded with `pickle.load()` from a file path. If that file is replaced by an attacker, it executes arbitrary code.
**Fix:** Validate the file's integrity (checksum or signature) before loading, or migrate to a safe serialization format.

---

## Medium — Incorrect Behavior

### GPS packet attributes accessed without mode check
**File:** `tools/add_metadata.py:152`
`packet.lat`, `packet.lon`, and `packet.alt` are accessed without checking `packet.mode`. A 2D fix (mode 2) has no altitude; mode < 2 has no position at all. Results in `AttributeError` or stale/zero coordinates written into image metadata.
**Fix:** Check `packet.mode >= 3` before accessing altitude; `>= 2` for lat/lon.

### `sq_state` dict access is not thread-safe
**File:** `tt_take_photos.py:141`
The `Picamera2()` instance is stored in a global `sq_state` dict without a lock. Two concurrent SQ nodes can both read `None` and both create camera instances simultaneously.
**Fix:** Guard the check-and-set with a `threading.Lock()`.

### IMU warmup timeout is silent
**File:** `tools/bno055_imu.py:33`
The warmup loop exits after 2 s even if Euler data never becomes valid. The caller cannot distinguish real zero-rotation from a timeout, and no warning is logged.
**Fix:** Log a warning on timeout; return a status flag or raise an exception.

### Parameter update callbacks can recurse infinitely
**File:** `tools/lora_runtime_integration.py:210`
`set_parameter()` invokes registered callbacks synchronously. A callback that calls `set_parameter()` again will recurse until the stack overflows.
**Fix:** Document that callbacks must not call `set_parameter()`; or dispatch callbacks via a queue/thread pool.

---

## Inconsistencies

### Temperature sensor returns formatted string; IMU returns raw float
**Files:** `tools/aht20_temperature.py:40`, `tools/bno055_imu.py:65`
`aht20_temperature.py` returns `'23.5 C'` (string); `bno055_imu.py` returns `23.5` (float). Callers must handle both formats.
**Fix:** Standardize all sensor functions to return raw numeric types; format at the display/log layer.

### `get_gps.py` returns `{}` in some paths and `None` in others
**File:** `tools/get_gps.py`
Different functions return inconsistent sentinel values for "no data available". Callers must check for both.
**Fix:** Standardize to always return an empty dict `{}` when data is unavailable.

### Hardcoded `/home/pi/SU-WaterCam/` paths in ~9 files
**Files:** Multiple (button service scripts, ticktalk_main.py, others)
Deployment to any user other than `pi`, or to a different path, silently breaks these files.
**Fix:** Use an environment variable (e.g., `WATERCAM_ROOT`) or derive the root from `__file__` at runtime.
