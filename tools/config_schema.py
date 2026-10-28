#!/home/pi/SU-WaterCam/venv/bin/python
"""The one definition of the node's settings (docs/CONFIGURATION.md).

Each Setting gives a key, its code default, how it may be set, which module
reads it and its status. The runtime parameter tables in
tools/lora_runtime_integration.py (_DEFAULT_PARAMS, _PARAM_RANGES, _INT_PARAMS,
_SETTABLE_PARAMS) and runtime_config.example.json are built from this list;
tests/test_config_schema.py checks that they still match.

No imports from the rest of tools/: lora_runtime_integration imports this.

    python tools/config_schema.py --template   # print runtime_config.example.json
    python tools/config_schema.py --mismatches # template values that differ from the code
"""
from dataclasses import dataclass
import json
import sys
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SHUTDOWN_LIMIT = 3          # captures per wake before the node shuts down
DEFAULT_EMERGENCY_FREQUENCY = 10    # minutes between captures in emergency mode / holds
MIN_EMERGENCY_FREQUENCY = 1
MAX_EMERGENCY_FREQUENCY = 240

_NOT_IN_TEMPLATE = object()

# status values
ACTIVE = "active"        # read and acted on
RESERVED = "reserved"    # kept for a feature or kind of deployment not in use yet
NO_EFFECT = "no-effect"  # nothing acts on it; to review
LEGACY = "legacy"        # an older path kept for compatibility; to review
STATE = "state"          # written by the node itself, not a setting


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    doc: str
    section: Optional[str] = None      # None: top level of runtime_config.json
    file: str = "runtime_config.json"
    range: Optional[Tuple[float, float]] = None   # inclusive; set_parameter() rejects values outside
    integer: bool = False              # set_parameter() stores it as int
    in_defaults: bool = False          # in _DEFAULT_PARAMS (written on first run, merged under the file)
    settable: Optional[bool] = None    # set_parameter() accepts it; None = in_defaults or ranged
    remote: Optional[str] = None       # downlink that sets it, e.g. "10 90"
    readers: Tuple[str, ...] = ()
    status: str = ACTIVE
    template: Any = _NOT_IN_TEMPLATE   # value in runtime_config.example.json, if it differs from default
    in_template: bool = False

    @property
    def template_value(self) -> Any:
        return self.default if self.template is _NOT_IN_TEMPLATE else self.template

    @property
    def accepts_set(self) -> bool:
        if self.settable is not None:
            return self.settable
        return self.in_defaults or self.range is not None


S = Setting
SETTINGS: List[Setting] = [
    # --- runtime_config.json, top level (in _DEFAULT_PARAMS order) ---
    S("area_threshold", 10, "Points of water above the baseline that count as a flood, and the rise between captures that counts as growing",
      range=(0, 100), integer=True, in_defaults=True, remote="10 90", readers=("ticktalk_main", "bitmap_policy", "flood_baseline"), in_template=True),
    S("camera_flood_detection_enabled", False, "Node-side flood flags (07 17 detected, 07 27 growing)",
      in_defaults=True, readers=("flood_baseline", "ticktalk_main"), in_template=True),
    S("bitmap_refresh_hours", 24, "LoRa sends the mask bitmap on change, a flood flag, emergency mode, or this often; 0 = every capture",
      range=(0, 168), integer=True, in_defaults=True, readers=("bitmap_policy",)),
    S("bitmap_method2_enabled", True, "LoRa bitmap method 2 (lossless context model, up to 640 px)",
      in_defaults=True, readers=("bitmap_fit", "ticktalk_main")),
    S("stage_threshold", 50, "Reserved: kept and reported (09 29) but unused on the node; the server applies its own",
      range=(0, 65535), in_defaults=True, remote="11 91", readers=("ticktalk_main",), status=RESERVED, in_template=True),
    S("monitoring_frequency", 60, "Minutes between checks/reports in normal operation; written by the 15 95 schedule",
      range=(1, 10080), integer=True, in_defaults=True, remote="15 95", readers=("ticktalk_main", "schedule_control"), in_template=True),
    S("emergency_frequency", DEFAULT_EMERGENCY_FREQUENCY, "Minutes between captures in emergency mode and keep-awake holds",
      range=(MIN_EMERGENCY_FREQUENCY, MAX_EMERGENCY_FREQUENCY), integer=True, in_defaults=True, remote="13 93",
      readers=("ticktalk_main", "emergency_sleep", "photo_interval_watcher", "max_awake"), template=5, in_template=True),
    S("photo_interval", 60, "Seconds between captures within a wake",
      range=(30, 1440), integer=True, in_defaults=True, readers=("ticktalk_main", "photo_interval_watcher"), in_template=True),
    S("neighborhood_emergency_frequency", 30, "Reporting frequency once a neighbouring unit signals emergency (minutes)",
      range=(1, 1440), integer=True, in_defaults=True, remote="14 94", readers=("ticktalk_main",), in_template=True),
    S("emergency_mode", False, "Emergency mode",
      in_defaults=True, remote="21 91 / 99 99", readers=("ticktalk_main", "emergency_sleep", "max_awake", "bitmap_policy", "lora_handler_concurrent"), in_template=True),
    S("emergency_sleep", True, "Power off between emergency captures instead of staying up",
      in_defaults=True, readers=("emergency_sleep",)),
    S("debug_mode", False, "Debug output: status prints, and the LoRa handler's DEBUG: and per-poll GPS lines (read once per process)",
      in_defaults=True, readers=("ticktalk_main", "debug_status_command", "debug_log"), in_template=True),
    S("max_retransmissions", 3, "Echoed in a status print only; the LoRa handler has its own",
      range=(0, 10), integer=True, in_defaults=True, readers=(), status=NO_EFFECT, in_template=True),
    S("auto_shutdown_enabled", True, "Shut down after shutdown_iteration_limit captures",
      in_defaults=True, readers=("ticktalk_main", "lora_runtime_integration"), in_template=True),
    S("shutdown_iteration_limit", DEFAULT_SHUTDOWN_LIMIT, "Captures per wake before shutdown",
      range=(1, 100), integer=True, in_defaults=True, readers=("ticktalk_main", "photo_interval_watcher"), in_template=True),
    S("data_retention_days", 0, "Days to keep photos and audio; 0 keeps all",
      range=(0, 365), integer=True, in_defaults=True, readers=("ticktalk_main", "data_retention"), in_template=True),
    S("storage_low_free_pct", 20, "Below this free space, remove the oldest photos...",
      in_defaults=True, readers=("data_retention",)),
    S("storage_target_free_pct", 30, "... until this much is free",
      in_defaults=True, readers=("data_retention",)),
    S("backup_enabled", True, "Nothing acts on it", in_defaults=True, readers=(), status=NO_EFFECT, in_template=True),
    S("audio_recording_enabled", False, "USB microphone recording (only some units have one)",
      in_defaults=True, remote="16 96", readers=("ticktalk_main", "audio_recorder"), in_template=True),
    S("iteration_count", 0, "Wake cycles since boot (reset each boot)",
      in_defaults=True, readers=("lora_runtime_integration", "photo_interval_watcher"), status=STATE),
    S("ip_fallback_after_hours", 12, "One IP upload, even with ip_upload off, when no bitmap was delivered for this long; 0 = off",
      range=(0, 168), in_defaults=True, readers=("ip_fallback", "wifi_policy", "ticktalk_main"), in_template=True),
    S("ip_fallback_min_interval_hours", 6, "At most one such fallback upload this often",
      range=(0, 168), in_defaults=True, readers=("ip_fallback",), in_template=True),

    # --- top level, ranged but not in _DEFAULT_PARAMS ---
    S("compression_level", None, "Nothing reads it", range=(1, 10), integer=True, readers=(), status=NO_EFFECT),
    S("emergency_max_hours", 24, "Emergency mode ends after this many hours; 0 = no expiry",
      range=(0, 168), integer=True, remote="21 91", readers=("wittypi_control", "max_awake", "emergency_sleep")),
    S("flood_baseline_water_pct", None, "The unit's normal water %, set by tools/flood_baseline.py",
      range=(0, 100), integer=True, readers=("flood_baseline",)),
    S("gps_fix_interval_h", 24, "A live GPS fix this often", range=(1, 720), readers=("gps_window",)),
    S("gps_cache_max_age_h", 720, "How old a cached fix may be; 0 = cache off", range=(0, 8760), readers=("get_gps",)),

    # --- top level, set by the node ---
    S("emergency_since", None, "When emergency mode started (set by 21 91)", settable=True, remote="21 91",
      readers=("wittypi_control", "max_awake", "emergency_sleep"), status=STATE),
    S("schedule", None, "The wake schedule object written by 15 95", settable=True, remote="15 95",
      readers=("schedule_control",), status=STATE),

    # --- top level, read with a fallback but refused by set_parameter() (edit the file) ---
    S("always_transmit_sensors", False, "Send every sensor field every capture", settable=False, readers=("ticktalk_main",), in_template=True),
    S("lora_full_send_hours", 24, "All sensor fields go out this often", settable=False, readers=("ticktalk_main",), in_template=True),
    S("lora_imu_threshold_deg", 2.0, "Orientation change that counts as changed", settable=False, readers=("ticktalk_main",), in_template=True),
    S("coreg_tilt_threshold_deg", 5.0, "Registration orientation check: tilt", settable=False, readers=("ticktalk_main",), in_template=True),
    S("coreg_rotation_threshold_deg", 15.0, "Registration orientation check: rotation", settable=False, readers=("ticktalk_main",), in_template=True),
    S("max_awake_minutes", 60, "Longest a unit stays up; 0 = off", settable=False, readers=("max_awake",), in_template=True),
    S("wifi_when_lora_healthy", False, "Join WiFi even when LoRa is healthy", settable=False, readers=("wifi_policy",)),
    S("lora_min_margin_db", 5, "Link margin that counts as healthy", settable=False, readers=("lora_health",)),
    S("gps_move_alert_m", 50.0, "Distance from home that counts as moved", settable=False, readers=("gps_home",)),
    S("wittypi_start_hour", 8, "Old schedule path, used when emergency mode ends", settable=False, readers=("ticktalk_main", "wittypi_control"), status=LEGACY),
    S("wittypi_start_minute", 0, "Old schedule path", settable=False, readers=("ticktalk_main", "wittypi_control"), status=LEGACY),
    S("wittypi_interval_minutes", 30, "Old schedule path", settable=False, readers=("ticktalk_main", "wittypi_control"), status=LEGACY),
    S("wittypi_repetitions_per_day", 8, "Old schedule path", settable=False, readers=("ticktalk_main", "wittypi_control"), status=LEGACY),

    # --- runtime_config.json, ip_upload section ---
    S("enabled", False, "IP upload", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("server_url", "http://localhost:8000", "API base URL", section="ip_upload", settable=False, readers=("transmit_ip",),
      template="http://<server-hostname>:8000", in_template=True),
    S("api_key", "", "Set with tools/set_ip_token.py", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("device_id", "watercam-001", "The unit's name on the server", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("timeout_s", 15, "Request timeout", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("retry_attempts", 3, "Retries per upload", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("retry_backoff_s", 2.0, "Backoff between retries", section="ip_upload", settable=False, readers=("transmit_ip",),
      template=2, in_template=True),
    S("fallback_to_lora", True, "Send over LoRa if the IP upload fails", section="ip_upload", settable=False, readers=("transmit_ip", "ticktalk_main"), in_template=True),
    S("only_if_lora_unavailable", True, "Upload over IP only when LoRa can't carry it", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("cellular_connection", "Quectel", "NetworkManager connection for cellular", section="ip_upload", settable=False, readers=("transmit_ip", "remote_debug"), in_template=True),
    S("cellular_connect_timeout_s", 90, "Time to bring up cellular", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("wifi_grace_s", 45, "Wait for the server over WiFi before cellular", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("downlink_poll_interval_s", 60, "Reserved: polling the server for commands, for deployments without Tailscale",
      section="ip_upload", settable=False, readers=(), status=RESERVED, in_template=True),
    S("max_queue_depth", 48, "IP upload queue depth", section="ip_upload", settable=False, readers=("transmit_ip",), in_template=True),
    S("max_queue_age_days", 7.0, "IP upload queue age limit", section="ip_upload", settable=False, readers=("transmit_ip",),
      template=7, in_template=True),

    # --- runtime_config.json, recovery_boot section ---
    S("enabled", True, "Recovery boot after an outage", section="recovery_boot", settable=False, readers=("recovery_boot",), in_template=True),
    S("min_delay_minutes", 120, "Earliest recovery wake after an outage", section="recovery_boot", settable=False, readers=("recovery_boot",), in_template=True),
    S("fallback_delay_minutes", 120, "Recovery wake when no schedule slot fits", section="recovery_boot", settable=False, readers=("recovery_boot",), in_template=True),
    S("scheduled_wake_window_minutes", 20, "A schedule slot this close is used instead", section="recovery_boot", settable=False, readers=("recovery_boot",), in_template=True),

    # --- lora_config.json (the LoRa handler's file) ---
    S("lora_sf_enabled", True, "Pi-side LoRa queue (tools/lora_store_forward.py)", file="lora_config.json", settable=False, readers=("ticktalk_main",)),
    S("lora_sf_drain_per_cycle", 2, "Queued frames sent per cycle", file="lora_config.json", settable=False, readers=("ticktalk_main",)),
    S("lora_sf_max_depth", 96, "Queue depth", file="lora_config.json", settable=False, readers=("ticktalk_main",)),
    S("lora_sf_max_age_days", 7, "Queue age limit", file="lora_config.json", settable=False, readers=("ticktalk_main",)),
]

# runtime_config.example.json: top-level groups (a blank line between them), then the sections
TEMPLATE_GROUPS: List[List[str]] = [
    ["area_threshold", "camera_flood_detection_enabled", "stage_threshold", "monitoring_frequency",
     "emergency_frequency", "photo_interval", "neighborhood_emergency_frequency", "emergency_mode",
     "debug_mode", "always_transmit_sensors", "lora_full_send_hours", "lora_imu_threshold_deg",
     "coreg_tilt_threshold_deg", "coreg_rotation_threshold_deg"],
    ["max_retransmissions", "auto_shutdown_enabled", "shutdown_iteration_limit", "max_awake_minutes",
     "data_retention_days", "backup_enabled", "audio_recording_enabled", "ip_fallback_after_hours",
     "ip_fallback_min_interval_hours"],
]
TEMPLATE_SECTIONS: List[Tuple[str, List[str]]] = [
    ("ip_upload", ["enabled", "server_url", "api_key", "device_id", "timeout_s", "retry_attempts",
                   "retry_backoff_s", "fallback_to_lora", "only_if_lora_unavailable", "cellular_connection",
                   "cellular_connect_timeout_s", "wifi_grace_s", "downlink_poll_interval_s",
                   "max_queue_depth", "max_queue_age_days"]),
    ("recovery_boot", ["enabled", "min_delay_minutes", "fallback_delay_minutes", "scheduled_wake_window_minutes"]),
]


def get(key: str, section: Optional[str] = None, file: str = "runtime_config.json") -> Setting:
    for s in SETTINGS:
        if s.key == key and s.section == section and s.file == file:
            return s
    raise KeyError(f"{file}:{section + '.' if section else ''}{key}")


def _top() -> List[Setting]:
    return [s for s in SETTINGS if s.section is None and s.file == "runtime_config.json"]


def default_params() -> Dict[str, Any]:
    """lora_runtime_integration._DEFAULT_PARAMS."""
    return {s.key: s.default for s in _top() if s.in_defaults}


def param_ranges() -> Dict[str, Tuple[float, float]]:
    """lora_runtime_integration._PARAM_RANGES."""
    return {s.key: s.range for s in _top() if s.range is not None}


def int_params() -> frozenset:
    """lora_runtime_integration._INT_PARAMS."""
    return frozenset(s.key for s in _top() if s.integer)


def settable_params() -> frozenset:
    """lora_runtime_integration._SETTABLE_PARAMS."""
    return frozenset(s.key for s in _top() if s.accepts_set)


def _dump(value: Any) -> str:
    return json.dumps(value)


def template_text() -> str:
    """runtime_config.example.json, exactly as committed."""
    blocks = []
    for group in TEMPLATE_GROUPS:
        blocks.append([f'  "{k}": {_dump(get(k).template_value)}' for k in group])
    for name, keys in TEMPLATE_SECTIONS:
        inner = ",\n".join(f'    "{k}": {_dump(get(k, section=name).template_value)}' for k in keys)
        blocks.append([f'  "{name}": {{\n{inner}\n  }}'])
    return "{\n" + ",\n\n".join(",\n".join(lines) for lines in blocks) + "\n}\n"


def template_mismatches() -> List[Tuple[str, Any, Any]]:
    """(key, code default, template value) where they differ: to decide in review."""
    out = []
    for s in SETTINGS:
        if s.in_template and s.template is not _NOT_IN_TEMPLATE and s.template != s.default:
            out.append((f"{s.section + '.' if s.section else ''}{s.key}", s.default, s.template))
    return out


def not_in_template() -> List[str]:
    """Active runtime_config settings the template leaves out."""
    return [f"{s.section + '.' if s.section else ''}{s.key}" for s in SETTINGS
            if s.file == "runtime_config.json" and not s.in_template and s.status == ACTIVE]


if __name__ == "__main__":
    if "--template" in sys.argv:
        sys.stdout.write(template_text())
    elif "--mismatches" in sys.argv:
        for k, d, t in template_mismatches():
            print(f"{k}: code {d!r}, template {t!r}")
        print("not in the template:", ", ".join(not_in_template()))
    else:
        print(__doc__)
