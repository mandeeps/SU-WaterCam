# Open Issue Progress

Documents work already in the codebase against each open GitHub issue.

---

## #16 — Use IP when LoRa not available

**Status: Store-and-forward implemented. LoRa-unavailable detection not yet implemented.**

### What exists

| File | What it does |
|------|-------------|
| `tools/transmit_ip.py` | `IPTransmitter` class — POST uplink to `/ip/uplink`, GET downlink from `/ip/downlink/{device_id}`, exponential-backoff retry, configurable timeout/retries, `is_reachable()` health check |
| `tools/transmit_ip.py` | `IPTransmitter._enqueue()` — atomic write to `data/ip_uplink_pending/<ts>_<n>.json`; evicts oldest entries when `max_queue_depth` is reached |
| `tools/transmit_ip.py` | `IPTransmitter._drain_queue()` — oldest-first drain; evicts stale entries (> `max_queue_age_days`), deletes corrupt files, stops on first send failure |
| `ticktalk_main.py` | `ip_uplink_transmit()` — sensor collection now runs before `is_reachable()`; on unreachable or send failure the reading is enqueued; on next wake queued entries are drained before the live reading is sent |
| `ticktalk_main.py` | `ip_downlink_poll_and_apply()` — polls server for queued commands and applies them to runtime config each wake cycle |
| `runtime_config.json` | `ip_upload` block: `enabled`, `server_url`, `api_key`, `device_id`, `timeout_s`, `retry_attempts`, `retry_backoff_s`, `fallback_to_lora`, `downlink_poll_interval_s`, `max_queue_depth` (48), `max_queue_age_days` (7) |
| `tests/test_ip_upload.py` | Integration test suite for `IPTransmitter` — uplink, downlink, reachability, edge cases; skips cleanly when server is down |
| `tests/test_ip_store_and_forward.py` | 29 tests covering queue creation, depth/age eviction, drain ordering, stop-on-failure, corrupt-file recovery, integration with `ip_uplink_transmit.__wrapped__` |

### What is missing

- **LoRa-unavailable detection**: the current implementation runs IP as a parallel path alongside LoRa, not as a fallback triggered by LoRa failure. True fallback logic (detect LoRa failure → switch to IP) is not implemented. Needs design discussion — e.g. how to detect LoRa failure reliably without waiting for a full timeout on every cycle.

---

## #20 — IP Command Handling

**Status: Implemented and tested.**

### What exists

`ticktalk_main.py:1765` — `ip_downlink_poll_and_apply()`:

- Polls `IPTransmitter.poll_downlink()` at the start of each wake cycle
- Delegates to `tools.transmit_ip.apply_downlink_command()` for decoding and dispatch
- Disabled when `ip_upload.enabled=false`

`tools/transmit_ip.py:398` — `apply_downlink_command()`:

- Standalone (no TickTalkPython dependency); accepts a `set_param_fn` callable
- Dispatches all recognised codes: `10 90` area_threshold, `11 91` stage_threshold, `12 92` monitoring_freq, `13 93` emergency_freq, `14 94` flood_code_freq
- Validates payload length per code; skips malformed parts without crashing

`tests/test_ip_command_handling.py`:

- 30+ assertions across 7 test classes covering all parameter codes, index bounds, multi-part commands, malformed inputs, and queue_id propagation
- No network or hardware required; `set_param_fn` is a plain dict accumulator

---

## #19 — Downstream Command Handling (LoRa)

**Status: Implementation complete. Test coverage exists but hardware-path testing is incomplete.**

### What exists

`tools/lora_handler_concurrent.py` (≈2100 lines):

- Full TLV-based command parser (`_parse_tlv_commands`, `_apply_command_tlv`) covering all parameter channels (area threshold, stage threshold, monitoring/emergency frequency, debug mode, emergency mode activation/deactivation)
- Legacy hex command format also handled for backwards compatibility
- Command timestamp logging for frequency-adjustment tracking

Test files:

| File | Coverage |
|------|---------|
| `tests/test_command_parsing.py` | LoRa command parsing unit tests |
| `tests/test_lora_command_integration.py` | End-to-end with simulated serial hardware |
| `tests/test_new_format_commands.py` | TLV `[Channel][Command][Value]` format |
| `tests/test_chirpstack_parameter_update.py` | Sending downlinks through ChirpStack API |
| `tests/test_emergency_activation.py` | Emergency mode activate/deactivate commands |
| `tests/test_emergency_mode*.py` | Emergency mode state machine |

### What is missing

- Tests require either `/dev/ttyAMA5` hardware or mock serial — no CI-safe pure-unit test that covers the full receive-parse-apply path without mocking concerns.
- The issue is open; it may still have known bugs that haven't been caught by the existing test suite.

---

## #18 — Calibration should be applied prior to Registration

**Status: Resolved. Undistortion is wired into the coregistration pipeline.**

### What exists

`tools/camera_calibration.py`:

- Captures calibration images from Picamera2 or reads from a directory
- Detects chessboard corners, runs `cv2.calibrateCamera()`
- Saves camera matrix, distortion coefficients, reprojection error, and FOV to a JSON file (default: `camera_calibration.json`)

`tools/generate_calibration_chessboard.py`:

- Generates a printable 9×6 inner-corner chessboard PNG sized for US Letter paper at 300 DPI

`tools/coreg_multiple.py`:

- `_undistort_if_calibrated()` loads `camera_calibration.json` and calls `cv2.undistort()` if the file exists
- Called on both fixed and moving images in `mutual_information_registration()` (lines 404–405) and `apply_cached_transform()` (lines 249–250) before any registration or resampling occurs

---

## #17 — Integrate Calibration Workflow

**Status: Intrinsic calibration tooling exists. Extrinsic collection and full integration are not yet implemented.**

### What exists

`tools/camera_calibration.py` — computes and saves lens intrinsics (camera matrix + distortion coefficients).

`tools/generate_calibration_chessboard.py` — printable calibration target.

### What is missing

- **Extrinsic parameter collection**: no tooling for collecting or saving the extrinsic parameters (rotation/translation of the camera relative to a world reference) needed for georeferencing.
- **Installation workflow**: no guided process to walk a new installation through capturing calibration data and validating the output.
- **Integration with coregistration**: `coreg_multiple.py` does not consume the calibration JSON (see also #18).

---

## #21 — Unit ID Metadata

**Status: Resolved. Unit ID is embedded in EXIF and XMP on every captured image.**

### What exists

`tools/add_metadata.py`:

- `_read_device_id()` reads `device_id` from `runtime_config.json` via `_load_ip_config()`
- Writes `device_id` to EXIF `BodySerialNumber` tag (0xA431)
- Writes `device_id` to XMP `DeviceID` property (DC namespace) for Pix4D compatibility
- Also writes IMU orientation (roll/pitch/yaw) to EXIF `UserComment` and XMP `Roll`/`Pitch`/`Yaw`
- Writes GPS fix (lat/lon/alt/track) to EXIF GPS IFD
- Called by `tools/take_nir_photos.py` for every captured image

---

## #2 — Cached Registration Transform not Used

**Status: Resolved. Cache is used on subsequent cycles; resolution changes are detected.**

### What exists

`tools/coreg_multiple.py`:

| Function | Role |
|----------|------|
| `save_transform_parameters()` | Serialises transform type, parameters, fixed parameters, and image size metadata to `registration_transform.json` |
| `load_transform_parameters()` | Loads cached transform + saved image size; returns `None` if not found or if `FORCE_RECALCULATE_TRANSFORM=True` |
| `apply_cached_transform()` | Resamples the moving image using the loaded transform |
| `mutual_information_registration()` | Checks type, parameter count, and image size before using the cached transform |
| `validate_transform_compatibility()` | Compares saved image size against current image dimensions (with the same scaling logic); rejects cache on mismatch |
| `--position-changed` CLI flag | Forces recalculation when the camera has been moved |

Two bugs fixed:
- `SetInitialTransform(inPlace=False)` caused `Execute()` to return a `CompositeTransform` wrapper, failing the type check every cycle. Fixed to `inPlace=True`.
- `validate_transform_compatibility()` computed `expected_size` but never compared it. Now rejects the cache when the saved size doesn't match the current image dimensions.

### What is missing

- No automated test (SimpleITK not available in the dev/test environment).
