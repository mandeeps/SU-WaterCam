"""tools/config_schema.py is the one definition of the node's settings.

The tables in lora_runtime_integration and runtime_config.example.json are
built from it. The snapshots below are the tables as they were written out by
hand before the schema, so a change to a default, range or settable key shows
up here as a deliberate edit.
"""
import os

from tools import config_schema as cs
from tools.lora_runtime_integration import LoRaRuntimeManager as M

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS_BEFORE = {
    'area_threshold': 10, 'camera_flood_detection_enabled': False, 'bitmap_refresh_hours': 24,
    'bitmap_method2_enabled': True, 'stage_threshold': 50, 'monitoring_frequency': 60,
    'emergency_frequency': 10, 'photo_interval': 60, 'neighborhood_emergency_frequency': 30,
    'emergency_mode': False, 'emergency_sleep': True, 'debug_mode': False, 'max_retransmissions': 3,
    'auto_shutdown_enabled': True, 'shutdown_iteration_limit': 3, 'data_retention_days': 0,
    'storage_low_free_pct': 20, 'storage_target_free_pct': 30, 'backup_enabled': True,
    'audio_recording_enabled': False, 'iteration_count': 0, 'ip_fallback_after_hours': 12,
    'ip_fallback_min_interval_hours': 6,
}
RANGES_BEFORE = {
    'area_threshold': (0, 100), 'stage_threshold': (0, 65535), 'monitoring_frequency': (1, 10080),
    'emergency_frequency': (1, 240), 'photo_interval': (30, 1440),
    'neighborhood_emergency_frequency': (1, 1440), 'max_retransmissions': (0, 10),
    'shutdown_iteration_limit': (1, 100), 'data_retention_days': (0, 365), 'compression_level': (1, 10),
    'emergency_max_hours': (0, 168), 'flood_baseline_water_pct': (0, 100), 'bitmap_refresh_hours': (0, 168),
    'ip_fallback_after_hours': (0, 168), 'ip_fallback_min_interval_hours': (0, 168),
    'gps_fix_interval_h': (1, 720), 'gps_cache_max_age_h': (0, 8760),
}
INTS_BEFORE = frozenset({
    'area_threshold', 'monitoring_frequency', 'emergency_frequency', 'photo_interval',
    'neighborhood_emergency_frequency', 'max_retransmissions', 'shutdown_iteration_limit',
    'data_retention_days', 'compression_level', 'emergency_max_hours', 'flood_baseline_water_pct',
    'bitmap_refresh_hours',
})


def test_tables_are_unchanged():
    assert M._DEFAULT_PARAMS == DEFAULTS_BEFORE
    assert list(M._DEFAULT_PARAMS) == list(DEFAULTS_BEFORE)   # the order a new file is written in
    assert M._PARAM_RANGES == RANGES_BEFORE
    assert M._INT_PARAMS == INTS_BEFORE
    assert M._SETTABLE_PARAMS == frozenset(DEFAULTS_BEFORE) | frozenset(RANGES_BEFORE) | {'emergency_since', 'schedule'}


def test_template_file_is_generated_from_the_schema():
    with open(os.path.join(REPO, "runtime_config.example.json"), encoding="utf-8") as f:
        assert f.read() == cs.template_text(), "regenerate: python tools/config_schema.py --template > runtime_config.example.json"


def test_each_setting_is_defined_once_with_a_description():
    seen = set()
    for s in cs.SETTINGS:
        ident = (s.file, s.section, s.key)
        assert ident not in seen, ident
        seen.add(ident)
        assert s.doc, ident
        assert s.status in (cs.ACTIVE, cs.RESERVED, cs.NO_EFFECT, cs.LEGACY, cs.STATE), ident
        if s.range is not None and s.default is not None:
            assert s.range[0] <= s.default <= s.range[1], ident


def test_template_layout_names_real_settings():
    listed = {k for g in cs.TEMPLATE_GROUPS for k in g}
    assert listed == {s.key for s in cs.SETTINGS if s.section is None and s.in_template}
    for name, keys in cs.TEMPLATE_SECTIONS:
        assert set(keys) == {s.key for s in cs.SETTINGS if s.section == name and s.in_template}


def test_known_mismatches_are_listed():
    # Decided in review, key by key; until then the template keeps its values
    assert ("emergency_frequency", 10, 5) in cs.template_mismatches()
