# Configuration reference

All the switches the node reads, where each one lives, and where it is described in more detail.
The settings themselves are defined once, in `tools/config_schema.py`: the runtime parameter tables in `tools/lora_runtime_integration.py` and `runtime_config.example.json` are built from it, and `tests/test_config_schema.py` checks they match. Add or change a setting there. `python tools/config_schema.py --mismatches` lists where the template differs from the code defaults. Compiled 2026-10-27 from main. It is a list to review, not a design. Items marked **reserved** belong to features or kinds of deployment that aren't used yet. Items marked **review** look unused or inconsistent.

## Where settings live

| Place | What | Kept across software updates? |
|---|---|---|
| `~/SU-WaterCam/runtime_config.json` | The unit's operating settings: the runtime parameters below, plus the `ip_upload` and `recovery_boot` sections. Created from `runtime_config.example.json` at setup (guide 1.5). | Yes (git-ignored). Merge writes keep keys the code doesn't know. |
| `~/SU-WaterCam/lora_config.json` | The LoRa handler's own copy of some parameters, plus the LoRa queue settings (`lora_sf_*`). Created by `tools/lora_handler_concurrent.py` if missing. | Yes (git-ignored) |
| `~/SU-WaterCam/bno055_calibration.json`, `config/camera_calibration.json`, `images/registration_transform.json` | Calibration and the registration cache | Yes |
| `config/*.service` | Service command lines and `Environment=`. Installed into `/etc/systemd/system`. | Reinstalled from the repo by the update steps |
| `/etc/watercam/` | Two bench flag files (below) | Yes (outside the repo) |
| `/run/…`, `/tmp/…` | Per-boot markers and caches; not settings | No (cleared at boot) |

**Remote** in the tables below means the parameter is settable by a downlink (LoRa or IP), with the command code. **Local** means `set_parameter()` on the node accepts it, but no downlink maps to it. **File** means it is edited in the file only, because `set_parameter()` refuses keys outside its parameter list.

## Runtime parameters (`runtime_config.json`)

Defaults are the code's (`tools/lora_runtime_integration.py` `_DEFAULT_PARAMS`, which apply to a key missing from the file). Where the template `runtime_config.example.json` sets a different value, or leaves the key out, the Template column says so.

| Key | Default; range | Template | Read by | Set | Described in |
|---|---|---|---|---|---|
| `area_threshold` | 10; 0–100 | 10 | ticktalk_main, bitmap_policy, flood_baseline | remote `10 90` | guide 1.5, IP_TRANSMISSION |
| `camera_flood_detection_enabled` | false | false | flood_baseline, ticktalk_main | local | guide 1.5 |
| `bitmap_refresh_hours` | 24; 0–168 | — | bitmap_policy | local | guide 1.5, BITMAP_SF_ADAPTIVE_COMPRESSION |
| `bitmap_method2_enabled` | true | — | bitmap_fit, ticktalk_main | local | guide 1.5, BITMAP_DECODING_SPEC |
| `stage_threshold` | 50; 0–65535 cm | 50 | ticktalk_main (reported only; the server applies its own) | remote `11 91` | guide 1.5 |
| `monitoring_frequency` | 60; 1–10080 min | 60 | ticktalk_main; written by schedule_control | remote, via `15 95` | guide 1.5 |
| `emergency_frequency` | 10; 1–240 min | **5** | ticktalk_main, emergency_sleep, photo_interval_watcher, max_awake | remote `13 93` | guide 1.5, REMOTE_START_AND_EMERGENCY |
| `photo_interval` | 60; 30–1440 s | 60 | ticktalk_main, photo_interval_watcher | local | guide 1.5 |
| `neighborhood_emergency_frequency` | 30; 1–1440 min | 30 | ticktalk_main | remote `14 94` | guide 1.5 |
| `emergency_mode` | false | false | ticktalk_main, emergency_sleep, max_awake, bitmap_policy, lora handler | remote `21 91` on, `99 99` off | REMOTE_START_AND_EMERGENCY, IP_TRANSMISSION |
| `emergency_max_hours` | 24; 0–168 (0 = no expiry) | — | wittypi_control, max_awake, emergency_sleep | remote, in `21 91` | REMOTE_START_AND_EMERGENCY, guide |
| `emergency_since` | (timestamp) | — | wittypi_control, max_awake, emergency_sleep | set by `21 91` | REMOTE_START_AND_EMERGENCY |
| `emergency_sleep` | true | — | emergency_sleep | local | REMOTE_START_AND_EMERGENCY, POWER_ANALYSIS (not in the guide 1.5 table) |
| `debug_mode` | false | false | ticktalk_main (status prints, read live), debug_status_command | local | guide 1.5 |
| `max_retransmissions` | 3; 0–10 | 3 | echoed in a status print only; the LoRa handler has its own | local | template only. **Review:** no effect. |
| `auto_shutdown_enabled` | true | true | ticktalk_main, `atomic_increment_iteration_count` | local | template only |
| `shutdown_iteration_limit` | 3; 1–100 | 3 | ticktalk_main, photo_interval_watcher | local | POWER_ANALYSIS, SETUP_MODE_DESIGN (no table entry) |
| `max_awake_minutes` | 60 (0 = off) | 60 | max_awake | file | guide 1.9 |
| `data_retention_days` | 0; 0–365 (0 = keep all) | 0 | ticktalk_main, data_retention | local | guide 1.9.1 |
| `storage_low_free_pct` / `storage_target_free_pct` | 20 / 30 % | — | data_retention | file | guide 1.9.1 |
| `backup_enabled` | true | true | nothing | local | template only. **Review:** no effect. |
| `compression_level` | (none); 1–10 | — | nothing (in the range list only) | local | none. **Review:** no effect. |
| `audio_recording_enabled` | false | false | ticktalk_main, audio_recorder | remote `16 96` | IP_TRANSMISSION |
| `ip_fallback_after_hours` | 12; 0–168 | 12 | ip_fallback, wifi_policy, ticktalk_main | local | guide 1.5, IP_TRANSMISSION |
| `ip_fallback_min_interval_hours` | 6; 0–168 | 6 | ip_fallback | local | guide 1.5, IP_TRANSMISSION |
| `wifi_when_lora_healthy` | false | — | wifi_policy | file | guide 1.9.1 |
| `lora_min_margin_db` | 5 | — | lora_health | file | guide 1.9.1 |
| `always_transmit_sensors` | false | false | ticktalk_main | file | guide 1.5 |
| `lora_full_send_hours` | 24 | 24 | ticktalk_main | file | guide 1.5 |
| `lora_imu_threshold_deg` | 2.0 | 2.0 | ticktalk_main | file | guide 1.5 |
| `coreg_tilt_threshold_deg` / `coreg_rotation_threshold_deg` | 5.0 / 15.0 | 5.0 / 15.0 | ticktalk_main | file | guide 1.5 |
| `gps_fix_interval_h` | 24; 1–720 | — | gps_window | local | SYSTEM_REFERENCE, guide |
| `gps_cache_max_age_h` | 720; 0–8760 (0 = off) | — | get_gps | local | guide, IMU_CALIBRATION |
| `gps_move_alert_m` | 50 | — | gps_home | file | guide 1.7.1 |
| `flood_baseline_water_pct` | (none); 0–100 | — | flood_baseline; written by `tools/flood_baseline.py` | local | guide 2.4.1 describes the procedure, not the key |
| `schedule` | (object, set by `15 95`) | — | schedule_control | remote `15 95` | WITTYPI_SCHEDULING, IP_TRANSMISSION (the stored object isn't described) |
| `wittypi_start_hour` / `_start_minute` / `_interval_minutes` / `_repetitions_per_day` | 8 / 0 / 30 / 8 | — | ticktalk_main, wittypi_control (rewrites schedule.wpi when emergency mode ends) | file | none. **Review:** older schedule path; check against `schedule` (15 95). |
| `iteration_count` | 0 | — | lora_runtime_integration, photo_interval_watcher | state | internal: wakes since boot, reset each boot |

### `ip_upload` section

Described in IP_TRANSMISSION "Configuration", except where noted.

| Key | Default | Notes |
|---|---|---|
| `enabled` | false | |
| `server_url`, `api_key`, `device_id` | — | Install the token with `tools/set_ip_token.py` (guide 1.5) |
| `timeout_s` | 15 | |
| `retry_attempts` | 3 | |
| `retry_backoff_s` | 2.0 | |
| `fallback_to_lora` | true | IP_TRANSMISSION says it isn't enforced by `transmit_ip.py`, but `ticktalk_main` reads it. **Review** the wording. |
| `only_if_lora_unavailable` | true | |
| `cellular_connection` | "Quectel" | |
| `cellular_connect_timeout_s` | 90 | |
| `wifi_grace_s` | 45 | |
| `max_queue_depth` | 48 | Template only; not in IP_TRANSMISSION |
| `max_queue_age_days` | 7 | Template only; not in IP_TRANSMISSION |
| `downlink_poll_interval_s` | 60 | **Reserved:** for deployments without Tailscale, where the node polls the server for commands. Nothing reads it yet. |

### `recovery_boot` section

Described in WITTYPI_SCHEDULING "Recovery boot".

| Key | Default | Notes |
|---|---|---|
| `enabled` | true | The behaviour is described; the key is not named |
| `min_delay_minutes` | 120 | |
| `fallback_delay_minutes` | 120 | Not described |
| `scheduled_wake_window_minutes` | 20 | |

## `lora_config.json`

| Key | Default | Read by | Notes |
|---|---|---|---|
| `lora_sf_enabled` | true | ticktalk_main | Pi-side LoRa queue (`tools/lora_store_forward.py`): frames the handler couldn't send, or sent while the mDot wasn't joined, are kept on disk and sent later. Not described in the docs yet. |
| `lora_sf_max_depth` | 96 | ticktalk_main | Pi-side queue |
| `lora_sf_max_age_days` | 7 | ticktalk_main | Pi-side queue |
| `lora_sf_drain_per_cycle` | 2 | ticktalk_main | Pi-side queue |
| copies of `area_threshold`, `emergency_frequency` (10), `shutdown_iteration_limit` (**2**), `debug_mode`, … | | lora_handler_concurrent | **Review:** duplicates of the runtime parameters. `shutdown_iteration_limit` differs (2 here, 3 in the runtime defaults). |

**Reserved:** store-and-forward on the mDot itself (a queue in the mDot firmware, which would send while the Pi is off) is not built and has no settings yet. The settings above are for the Pi's queue.

## Other config files

| File | What | Described in |
|---|---|---|
| `runtime_config.example.json` | The template for `runtime_config.json`. Lacks about ten keys the code reads (marked "—" in the Template column above). | guide 1.5 |
| `bno055_calibration.json` | IMU calibration (path override: `WATERCAM_CALIB_FILE`) | IMU_CALIBRATION, guide 1.7 |
| `config/camera_calibration.json` | Lens calibration | CAPTURE_PIPELINE_NOTES, guide 1.8 |
| `images/registration_transform.json` | Cached optical-to-thermal registration | guide 1.5 |
| `data/remote_debug_request.json`, `data/gps_request.json` | Requests left by downlinks `18 9x` and `19 99`; state, not settings | REMOTE_DEBUG_SESSION (gps_request.json: none) |

## Flag files

| Path | Effect | Described in |
|---|---|---|
| `/etc/watercam/hold-awake` | Keeps the unit up for bench work (max_awake) | guide 1.9 |
| `/etc/watercam/usb-on-at-boot` | Leaves USB on at boot (`usb-power-off.service` `ConditionPathExists=!`) | guide 1.9 |

**Review:** new switches go in `runtime_config.json` or a unit's `Environment=`, not in `/etc`. Consider moving these two the same way.

## Environment variables

| Variable | Default | Read by | Described in |
|---|---|---|---|
| `WATERCAM_REPO` | the script's repo, else `/home/pi/SU-WaterCam` | about 25 tools, ticktalk_main | ISSUE_PLAN (design note only) |
| `WATERCAM_CALIB_FILE` | `bno055_calibration.json` in the repo | bno055_imu | none |
| `SEGFORMER_DIR` / `SEGFORMER_PYTHON` | `/home/pi/segformer_5band`, the miniforge env's python | ticktalk_main | none. **Review:** `tools/watercam.py` defaults to `/home/pi/git/segformer_5band`. |
| `COREG_SINGLE_THREAD` | unset | coreg_multiple | CAPTURE_PIPELINE_NOTES |
| `PYTHONUNBUFFERED=1` | set in the button, lora_daemon, segformer_daemon and ticktalk units | Python | — |

`tools/test_bitmap_workflow.py` reads its own variables (`SU_WATERCAM_IMAGES`, `SEGFORMER_CWD` and others); it is a test tool, not production.

## Service command lines (`config/*.service`)

| Service | Command and flags | Described in |
|---|---|---|
| ticktalk | `runrtm.py -d 50 -s 0 --timeout=0 output/ticktalk_main.pickle 8080`. ExecStartPre: `wait_for_wittypi_schedule.py`, `schedule_control.py --refresh`. | guide 1.9 (the service); the `-d`/`-s` flags are not explained |
| lora_daemon | `--socket /run/lora/lora.sock` | SYSTEM_REFERENCE, LORA_HANDLER_MULTIPROCESS_ISSUE |
| segformer_daemon | `--model …` (the deployed ONNX), `--socket /run/segformer/segformer.sock` | SEGFORMER_OPTIMIZATION |
| max-awake, remote-debug, watercam-cellular-usage | `max_awake.py check`, `remote_debug.py tick`, `cellular_usage.py watch` | guide 1.9, REMOTE_DEBUG_SESSION, CELLULAR_DATA |
| usb-power-off | `ConditionPathExists=!/etc/watercam/usb-on-at-boot` | guide 1.9 |

## Per-boot markers (internal, not settings)

These are listed so a reader of `/run` and `/tmp` knows what they are:

| Marker | What it is |
|---|---|
| `/run/wittypi-recovery-wake` | recovery_boot, ensure_next_wake |
| `/run/watercam-time-source` | where the clock came from (WITTYPI_SCHEDULING) |
| `/run/watercam-rtc-untrusted` | rtc_early_clock |
| `/run/wittypi-log-offset` | this boot's offset into wittyPi.log |
| `/tmp/watercam-gps-window` | the GPS window ran this boot |
| `/tmp/watercam-lora-health.json`, `/tmp/watercam-version.json`, `/tmp/watercam-usb` | caches |
| the capture folder's unverified-time file | `capture_time.UNVERIFIED_FILENAME` |

## To review later

1. **Template vs code defaults:** `emergency_frequency` is 5 in the template and 10 in the code, and the template lacks `emergency_sleep`, `bitmap_*`, `storage_*`, `gps_*`, `wifi_when_lora_healthy` and `lora_min_margin_db`.
2. **`lora_config.json` duplicates:** the runtime parameters, with `shutdown_iteration_limit` 2 against 3.
3. **No effect:** `max_retransmissions`, `backup_enabled`, `compression_level`.
4. **The `wittypi_*` keys** against the `15 95` schedule: which one wins after emergency mode ends.
5. **Reserved:** `ip_upload.downlink_poll_interval_s` (deployments without Tailscale), and mDot store-and-forward (not built).
6. **Missing descriptions:** `lora_sf_*`, `recovery_boot.fallback_delay_minutes`, `flood_baseline_water_pct`, `WATERCAM_CALIB_FILE`, `SEGFORMER_DIR`/`SEGFORMER_PYTHON`, `ip_upload.max_queue_*`, and the ticktalk `-d`/`-s` flags.
7. **The two `/etc/watercam` flag files:** move them to `runtime_config.json` or the unit's environment.
8. **After #256:** add `WATERCAM_DEBUG` to the environment table, and note that `debug_mode` also controls the LoRa handler's `DEBUG:` lines and the per-poll GPS lines, read once per process.
