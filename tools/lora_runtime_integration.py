#!/usr/bin/env python3
"""
LoRa Runtime Integration Module

This module provides integration between LoRa incoming commands and runtime parameters
for the ticktalk_main.py system. It allows dynamic updates to system behavior based
on received LoRa messages.

Usage:
    from lora_runtime_integration import LoRaRuntimeManager
    
    # Initialize the runtime manager
    runtime_manager = LoRaRuntimeManager()
    
    # Get current parameters
    area_threshold = runtime_manager.get_parameter('area_threshold')
    
    # Parameters are automatically updated when LoRa commands are received
"""

import math
import os
import time
import threading
from typing import Dict, Any, Optional, Callable
from datetime import datetime

import sys
import os

_current_dir = os.path.dirname(os.path.abspath(__file__))
_parent_dir = os.path.dirname(_current_dir)

try:
    # Prefer the package-qualified import so this module shares the same
    # sys.modules entry (and thus the same _lora_handler singleton/lock) as
    # ticktalk_main.py, which always imports via "tools.lora_handler_concurrent".
    # A bare "from lora_handler_concurrent import ..." here would silently
    # create a second, independent copy of the module with its own singleton,
    # causing the two copies to deadlock each other over the real serial-port
    # flock ("LoRa serial port already owned by another process") even though
    # only one OS process is actually running.
    from tools.lora_handler_concurrent import get_lora_handler, get_config_value
except ImportError:
    try:
        from lora_handler_concurrent import get_lora_handler, get_config_value
    except ImportError:
        for _d in (_current_dir, _parent_dir):
            if _d not in sys.path:
                sys.path.insert(0, _d)
        try:
            from lora_handler_concurrent import get_lora_handler, get_config_value
        except ImportError:
            import importlib.util
            _spec = importlib.util.spec_from_file_location(
                "lora_handler_concurrent",
                os.path.join(_current_dir, "lora_handler_concurrent.py")
            )
            if _spec and _spec.loader:
                _lora_module = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_lora_module)
                get_lora_handler = _lora_module.get_lora_handler
                get_config_value = _lora_module.get_config_value
            else:
                raise ImportError("lora_handler_concurrent not found")

try:
    from tools import config_io
except ImportError:
    if _current_dir not in sys.path:
        sys.path.insert(0, _current_dir)
    import config_io

class LoRaRuntimeManager:
    """
    Manages runtime parameters that can be updated via LoRa commands
    and integrates with the ticktalk_main.py system
    """

    # Emergency mode keeps the unit awake and clears the Witty Pi shutdown
    # alarm; call_shutdown() ends it after this many hours (runtime_config
    # emergency_max_hours, 0 = never) so a stray command can't drain the battery.
    EMERGENCY_MAX_HOURS = 24

    # Inclusive (min, max) bounds for each settable parameter.
    # Values outside these ranges are rejected with a warning.
    _PARAM_RANGES: dict = {
        'area_threshold':                   (0, 100),
        'stage_threshold':                  (0, 65535),
        'monitoring_frequency':             (1, 10080),
        'emergency_frequency':              (1, 1440),
        'photo_interval':                   (30, 1440),   # seconds
        'neighborhood_emergency_frequency': (1, 1440),
        'max_retransmissions':              (0, 10),
        'shutdown_iteration_limit':         (1, 100),
        'data_retention_days':              (1, 365),
        'compression_level':                (1, 10),
        'emergency_max_hours':              (0, 168),     # 0 = no expiry
    }

    # Parameters that must be stored as integers (not floats).
    _INT_PARAMS: frozenset = frozenset({
        'area_threshold',
        'monitoring_frequency',
        'emergency_frequency',
        'photo_interval',
        'neighborhood_emergency_frequency',
        'max_retransmissions',
        'shutdown_iteration_limit',
        'data_retention_days',
        'compression_level',
        'emergency_max_hours',
    })

    def __init__(self, config_file='runtime_config.json', lora_handler=None):
        self.config_file = config_file
        self.parameters = self.load_parameters()
        self._config_mtime = self._get_config_mtime()
        self.update_callbacks = {}
        self._dispatching: set = set()
        self.lora_handler = None
        self.listening = False
        self.listener_thread = None

        if lora_handler is not None:
            # Daemon-mode: caller already constructed the one real LoRaHandler
            # and is passing it in directly (see tools/lora_daemon.py).
            self._adopt_daemon_owned_handler(lora_handler)
        else:
            # Client-mode: every other process talks to the daemon over IPC.
            self._init_lora_handler()

    def _adopt_daemon_owned_handler(self, lora_handler):
        """Wire incoming-message handling directly, in-process, around an
        already-constructed real LoRaHandler.

        Only tools/lora_daemon.py calls this (via
        LoRaRuntimeManager(lora_handler=...)) -- it is the single process
        that owns the real LoRaHandler and its listener thread for the
        LoRaWAN device's entire lifetime. Every other process is client-mode
        (see _init_lora_handler()) and never wires set_runtime_callback()/
        start_listening() itself, since decode() needs the real handler's own
        queue/config state and must run in exactly one place -- here.
        """
        self.lora_handler = lora_handler
        try:
            def sync_lora_command(key, value):
                if self.set_parameter(key, value):
                    print(f"LoRa sync: '{key}' updated to {self.get_parameter(key)}")
                else:
                    print(f"LoRa sync: '{key}' rejected value {value!r}")

            self.lora_handler.set_runtime_callback(sync_lora_command)
            self.lora_handler.start_listening()
            self.listening = True
            print("LoRa runtime integration initialised (daemon-owned handler)")
        except Exception as e:
            print(f"LoRa unavailable: {e}")
            self.listening = False

    def _init_lora_handler(self):
        """Connect to the LoRa daemon (client-mode).

        get_lora_handler() returns a LoRaHandlerClient (IPC proxy) or None if
        the daemon is unreachable -- this manager still serves parameter
        reads/writes either way, it just can't send AT commands or receive
        messages without a reachable daemon. Does NOT wire
        set_runtime_callback()/start_listening(): that wiring is daemon-only,
        see _adopt_daemon_owned_handler().
        """
        try:
            self.lora_handler = get_lora_handler()
            if self.lora_handler is None:
                return
            print("LoRa runtime integration initialised (client mode)")
        except Exception as e:
            print(f"LoRa unavailable: {e}")
            self.lora_handler = None
            self.listening = False

    def load_parameters(self) -> Dict[str, Any]:
        """Load runtime parameters from file or create defaults"""
        default_params = {
            # Flood detection parameters
            'area_threshold': 10,           # Flood detection area threshold (%)
            'stage_threshold': 50,          # Water stage threshold (cm)
            
            # Timing parameters
            'monitoring_frequency': 60,     # Monitoring frequency (minutes)
            'emergency_frequency': 30,       # Emergency transmission frequency (minutes)
            'photo_interval': 60,           # Capture period within a wake (seconds)
            'neighborhood_emergency_frequency': 30,  # Neighborhood emergency frequency
            
            # System control parameters
            'emergency_mode': False,        # Emergency mode flag
            'debug_mode': False,            # Debug logging mode
            
            # Performance parameters
            'max_retransmissions': 3,       # Maximum retransmission attempts
            
            # Advanced parameters
            'auto_shutdown_enabled': True,  # Enable automatic shutdown after iterations
            'shutdown_iteration_limit': 2,  # Number of iterations before shutdown
            'data_retention_days': 7,       # Days to retain data files
            'backup_enabled': True,         # Enable data backup
            'audio_recording_enabled': True,  # Enable USB microphone audio recording
        }
        
        try:
            # read_json restores the file from runtime_config.json.bak if a power
            # cut left it damaged.
            loaded_params = config_io.read_json(self.config_file)
        except FileNotFoundError:
            self.save_parameters(default_params)
            return default_params
        except Exception as e:
            # Run on defaults in memory but leave the file alone: writing the
            # defaults would erase the unit's identity and upload settings.
            print(f"⚠️ Error loading runtime config: {e}, using defaults (file left as is)")
            return default_params
        # Merge with defaults to ensure all parameters exist
        for key, value in default_params.items():
            if key not in loaded_params:
                loaded_params[key] = value
        return loaded_params
    
    def save_parameters(self, params: Dict[str, Any], merge: bool = False) -> bool:
        """Save runtime parameters to file. Returns True on success, False on failure.

        merge=True writes only the given keys onto what is on disk now, read
        and written under one exclusive lock, and refreshes the in-memory copy
        from the result. Several processes (lora_daemon, each ticktalk SQ
        process) keep their own LoRaRuntimeManager; writing a whole in-memory
        copy instead reverts every change another process (or a person) made
        since this one last read the file.
        """
        try:
            # Writes go to a temp file that replaces the config in one rename
            # (config_io), so a power cut leaves the old file or the new one.
            with config_io.locked(self.config_file):
                if merge:
                    on_disk = self._read_for_update()
                    on_disk.update(params)
                    params = on_disk
                config_io.write_json(self.config_file, params)
            if merge:
                self.parameters.update(params)
            return True
        except Exception as e:
            print(f"Error saving runtime config: {e}")
            return False
    
    def atomic_increment_iteration_count(self) -> dict:
        """Atomically increment iteration_count; return shutdown control fields.

        Uses os.open(O_CREAT|O_RDWR) so the file is created if absent, flock
        for mutual exclusion with all other writers, and updates the in-memory
        cache so subsequent set_parameter()/save_parameters() calls don't
        overwrite the new count with a stale cached value.
        """
        with config_io.locked(self.config_file):
            cfg = self._read_for_update()
            new_count = cfg.get('iteration_count', 0) + 1
            cfg['iteration_count'] = new_count
            if cfg.get('emergency_mode') and not cfg.get('emergency_since'):
                # set before emergency_since existed, or by hand: start its clock now
                cfg['emergency_since'] = time.time()
            config_io.write_json(self.config_file, cfg)
            self.parameters['iteration_count'] = new_count  # keep cache consistent
        return {
            'iteration_count': new_count,
            'auto_shutdown_enabled': cfg.get('auto_shutdown_enabled', True),
            'shutdown_iteration_limit': cfg.get('shutdown_iteration_limit', 3),
            'emergency_mode': cfg.get('emergency_mode', False),
            'emergency_since': cfg.get('emergency_since'),
            'emergency_max_hours': cfg.get('emergency_max_hours', self.EMERGENCY_MAX_HOURS),
        }

    def _read_for_update(self) -> dict:
        """The on-disk config for a read-modify-write; call with config_io.locked held.

        A damaged file is restored from its backup. If the backup is damaged
        too, the file is set aside (runtime_config.json.corrupt-*) and rebuilt
        from this process's in-memory copy, so the unit keeps counting
        iterations and shutting down rather than failing every cycle.
        """
        try:
            return config_io.read_json(self.config_file)
        except FileNotFoundError:
            return {}
        except config_io.ConfigUnreadable as e:
            print(f"⚠️ {e}; rebuilding it from memory")
            try:
                os.replace(self.config_file, f"{self.config_file}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
            except OSError:
                pass
            return dict(self.parameters)

    def _get_config_mtime(self) -> Optional[float]:
        try:
            return os.stat(self.config_file).st_mtime
        except OSError:
            return None

    def _reload_if_changed(self) -> None:
        """Re-read runtime_config.json if it changed on disk since this
        instance last read it, firing register_update_callback() hooks for
        any key whose value actually changed.

        self.parameters is loaded once at __init__ and otherwise only
        updated by this instance's own set_parameter() calls -- so a change
        written by a DIFFERENT process (chiefly: the LoRa daemon applying an
        incoming command via its own LoRaRuntimeManager) would otherwise
        never be observed here, and callbacks registered on this instance
        (e.g. ticktalk_main.py's lora_listener() callbacks) would silently
        stop firing for remotely-driven changes once decode() moved
        server-side into the daemon.

        Gated on os.stat().st_mtime so the common case is a cheap stat(),
        only re-parsing+re-locking the file when it actually changed.
        """
        current_mtime = self._get_config_mtime()
        if current_mtime is None or current_mtime == self._config_mtime:
            return
        self._config_mtime = current_mtime

        try:
            new_params = config_io.read_json(self.config_file)
        except Exception as e:
            print(f"Error reloading runtime config: {e}")
            return

        changed = [
            (key, self.parameters.get(key), value)
            for key, value in new_params.items()
            if key not in self.parameters or self.parameters[key] != value
        ]
        self.parameters.update(new_params)

        for key, old_value, value in changed:
            if key in self.update_callbacks and key not in self._dispatching:
                self._dispatching.add(key)
                try:
                    for callback in self.update_callbacks[key]:
                        try:
                            callback(value, old_value)
                        except Exception as e:
                            print(f"Error in parameter update callback for '{key}': {e}")
                finally:
                    self._dispatching.discard(key)

    def get_parameter(self, key: str, default: Any = None) -> Any:
        """Get a runtime parameter value, reloading from disk first if changed."""
        self._reload_if_changed()
        return self.parameters.get(key, default)
    
    def is_lora_available(self) -> bool:
        """Check if LoRa functionality is available"""
        return self.lora_handler is not None
    
    def _validate_param(self, key: str, value: Any) -> bool:
        """Return True if value is within the allowed range for key, False otherwise."""
        bounds = self._PARAM_RANGES.get(key)
        if bounds is None:
            return True
        # Reject booleans — bool subclasses int but is semantically wrong for numeric params
        if isinstance(value, bool):
            print(f"Warning: boolean value {value!r} for parameter '{key}', rejected")
            return False
        lo, hi = bounds
        try:
            numeric = float(value)
        except (TypeError, ValueError, OverflowError):
            print(f"Warning: non-numeric value {value!r} for parameter '{key}', rejected")
            return False
        # Reject non-finite values (inf/nan pass float() but crash int() with OverflowError/ValueError)
        if not math.isfinite(numeric):
            print(f"Warning: non-finite value {value!r} for parameter '{key}', rejected")
            return False
        # Range check before fractional check: avoids int() on huge finite floats
        if not (lo <= numeric <= hi):
            print(f"Warning: value {value} for '{key}' is outside allowed range [{lo}, {hi}], rejected")
            return False
        # Reject fractional values for integer-only params (e.g. 1.9 must not silently become 1)
        if key in self._INT_PARAMS and not numeric.is_integer():
            print(f"Warning: fractional value {value} for integer parameter '{key}', rejected")
            return False
        return True

    def _coerce_param(self, key: str, value: Any) -> Any:
        """Coerce value to the correct stored type for key.

        Safe to call only after _validate_param() has already accepted the value.
        Integer-only ranged params are stored as int; other ranged numeric params
        are stored as float so the persisted config is type-consistent even when
        callers supply string inputs such as "50" or "0.75".
        """
        if key in self._INT_PARAMS:
            return int(float(value))
        if key in self._PARAM_RANGES:
            return float(value)
        return value

    def set_parameter(self, key: str, value: Any) -> bool:
        """Set a runtime parameter value and save to file.

        Validates against _PARAM_RANGES and coerces integer-only params before
        persisting. Returns True if the value was applied, False if rejected.
        All callers — including the LoRa runtime callback — are protected
        regardless of how set_parameter() is reached.
        """
        if not self._validate_param(key, value):
            return False
        coerced = self._coerce_param(key, value)
        # Pick up changes other processes made, so `prior` and their
        # callbacks are right; the write below merges onto the file anyway.
        self._reload_if_changed()
        prior = self.parameters.get(key)
        update = {key: coerced}
        if key == 'emergency_mode':
            # call_shutdown() ends emergency mode emergency_max_hours after this
            if coerced and not prior:
                update['emergency_since'] = time.time()
            elif not coerced:
                update['emergency_since'] = None
        if not self.save_parameters(update, merge=True):
            print(f"Warning: failed to persist '{key}', change not applied")
            return False
        self.parameters[key] = coerced

        # Refresh our own mtime baseline immediately: without this, the next
        # get_parameter() call would see the file we just wrote as an
        # "external" change (mtime advanced since __init__/last reload) and
        # fire this same key's callbacks a second time via _reload_if_changed().
        self._config_mtime = self._get_config_mtime()

        print(f"Runtime parameter '{key}' updated: {prior} → {coerced}")

        if key in self.update_callbacks and key not in self._dispatching:
            self._dispatching.add(key)
            try:
                for callback in self.update_callbacks[key]:
                    try:
                        callback(coerced, prior)
                    except Exception as e:
                        print(f"Error in parameter update callback for '{key}': {e}")
            finally:
                self._dispatching.discard(key)
        return True
    
    def register_update_callback(self, parameter: str, callback: Callable):
        """Register a callback to be called when a parameter is updated"""
        if parameter not in self.update_callbacks:
            self.update_callbacks[parameter] = []
        self.update_callbacks[parameter].append(callback)
    
    def process_lora_command(self, command: str, value: Any) -> bool:
        """Process incoming LoRa command in old format (backward compatibility)"""
        command_mapping = {
            # Basic flood detection commands
            '10': lambda v: self.set_parameter('area_threshold', v * 10),  # Area threshold (10% increments)
            '11': lambda v: self.set_parameter('stage_threshold', v),      # Stage threshold (cm)
            
            # Timing commands
            '12': lambda v: self.set_parameter('monitoring_frequency', v), # Monitoring frequency (minutes)
            '13': lambda v: self.set_parameter('emergency_frequency', v),  # Emergency frequency (minutes)
            '15': lambda v: self.set_parameter('neighborhood_emergency_frequency', v), # Neighborhood frequency
            
            # System control commands
            '21': lambda v: True,   # remote start '!': powers the Pi on, not emergency mode (21 91 HH)
            '22': lambda v: self.set_parameter('debug_mode', bool(v)),           # Debug mode
            
            # Performance commands
            '32': lambda v: self.set_parameter('max_retransmissions', v),        # Max retransmissions
            
            # Advanced commands
            '40': lambda v: self.set_parameter('auto_shutdown_enabled', bool(v)), # Auto shutdown
            '41': lambda v: self.set_parameter('shutdown_iteration_limit', v),    # Shutdown limit
            '42': lambda v: self.set_parameter('data_retention_days', v),         # Data retention
            '43': lambda v: self.set_parameter('backup_enabled', bool(v)),        # Backup enable
            
            # Emergency mode deactivation
            '99': lambda v: self.set_parameter('emergency_mode', False),          # Deactivate emergency mode
        }
        
        if command in command_mapping:
            try:
                return bool(command_mapping[command](value))
            except Exception as e:
                print(f"Error processing LoRa command '{command}' with value '{value}': {e}")
                return False
        else:
            print(f"Unknown LoRa command: {command}")
            return False
    
    def process_lora_payload(self, payload: str) -> bool:
        """Process LoRa payload in new [Channel][Command][Value] format"""
        try:
            # A bare 21 is the remote start byte '!': it powers a sleeping Pi
            # on and is not emergency mode (that is 21 91 HH, handled by the
            # LoRa handler's decode()).
            if payload == '21':
                print('ℹ️ Remote start byte received; emergency mode needs 21 91 HH')
                return True
            
            # First try TLV hex multi-command format: [ch:1B][cmd:1B][len:1B][value:len]
            def _is_hex_string(s: str) -> bool:
                hexdigits = set('0123456789abcdefABCDEF')
                return len(s) % 2 == 0 and all(c in hexdigits for c in s)

            def _parse_tlv_commands(hex_payload: str):
                try:
                    data = bytes.fromhex(hex_payload)
                except Exception as e:
                    print(f"Warning: Failed to parse TLV hex payload '{hex_payload}': {e}")
                    return None
                i = 0
                cmds = []
                while i + 3 <= len(data):
                    ch = data[i]
                    cmd = data[i+1]
                    vlen = data[i+2]
                    i += 3
                    if i + vlen > len(data):
                        return None
                    value_bytes = data[i:i+vlen]
                    i += vlen
                    cmds.append((ch, cmd, value_bytes))
                if i != len(data):
                    return None
                return cmds

            def _to_int_be(b: bytes) -> int:
                if not b:
                    return 0
                return int.from_bytes(b, byteorder='big', signed=False)

            def _apply_command_tlv(ch: int, cmd: int, value_bytes: bytes) -> bool:
                channel = f"{ch:02d}"
                command = f"{cmd:02d}"
                val_int = _to_int_be(value_bytes)

                def _set(param, value) -> bool:
                    return self.set_parameter(param, value)

                if channel == '10' and command == '90':
                    return _set('area_threshold', val_int * 10)
                elif channel == '11' and command == '91':
                    return _set('stage_threshold', val_int)
                elif channel == '12' and command == '92':
                    return _set('monitoring_frequency', val_int)
                elif channel == '13' and command == '93':
                    return _set('emergency_frequency', val_int)
                # 14 94 is the flood-code frequency (server frame, decoded by tools/transmit_ip.py); it no longer sets photo_interval here (#116)
                elif channel == '15' and command == '95':
                    return _set('neighborhood_emergency_frequency', val_int)
                elif channel == '22' and command == '00':
                    return _set('debug_mode', bool(val_int))
                elif channel == '31' and command == '00':
                    return _set('compression_level', val_int)
                elif channel == '32' and command == '00':
                    return _set('max_retransmissions', val_int)
                elif channel == '40' and command == '00':
                    return _set('auto_shutdown_enabled', bool(val_int))
                elif channel == '41' and command == '00':
                    return _set('shutdown_iteration_limit', val_int)
                elif channel == '42' and command == '00':
                    return _set('data_retention_days', val_int)
                elif channel == '43' and command == '00':
                    return _set('backup_enabled', bool(val_int))
                elif channel == '99' and command == '00':
                    return _set('emergency_mode', False)
                else:
                    print(f"Warning: unknown TLV channel/command {channel}/{command}, ignored")
                    return False

            if _is_hex_string(payload):
                tlv_cmds = _parse_tlv_commands(payload)
                if tlv_cmds is not None and len(tlv_cmds) > 0:
                    results = []
                    failed = []
                    for ch, cmd, vbytes in tlv_cmds:
                        ok = _apply_command_tlv(ch, cmd, vbytes)
                        results.append(ok)
                        if not ok:
                            failed.append(f"{ch:02d}/{cmd:02d}")
                    # Commands applied sequentially — partial apply is possible:
                    # if a later command fails, earlier successful ones remain persisted.
                    fully_applied = all(results)
                    if not fully_applied:
                        print(
                            f"Warning: {len(failed)}/{len(results)} TLV commands not applied: {failed}. "
                            "Earlier commands in the same payload may already have been applied."
                        )
                    return fully_applied

            # Handle new format: [Channel][Command][Value] (single)
            if len(payload) >= 4:
                channel = payload[:2]
                command = payload[2:4]
                value = payload[4:]
                
                # Process commands based on channel and command combination.
                # set_parameter() is the single validation + coercion point; dispatchers
                # just parse the raw string into the right type and delegate.
                try:
                    if channel == '10' and command == '90':
                        return self.set_parameter('area_threshold', int(value) * 10)
                    elif channel == '11' and command == '91':
                        return self.set_parameter('stage_threshold', float(value))
                    elif channel == '12' and command == '92':
                        return self.set_parameter('monitoring_frequency', int(value))
                    elif channel == '13' and command == '93':
                        return self.set_parameter('emergency_frequency', int(value))
                    # 14 94 is the flood-code frequency (server frame, decoded by tools/transmit_ip.py); it no longer sets photo_interval here (#116)
                    elif channel == '15' and command == '95':
                        return self.set_parameter('neighborhood_emergency_frequency', int(value))
                    elif channel == '22' and command == '00':
                        return self.set_parameter('debug_mode', bool(int(value)))
                    elif channel == '31' and command == '00':
                        return self.set_parameter('compression_level', int(value))
                    elif channel == '32' and command == '00':
                        return self.set_parameter('max_retransmissions', int(value))
                    elif channel == '40' and command == '00':
                        return self.set_parameter('auto_shutdown_enabled', bool(int(value)))
                    elif channel == '41' and command == '00':
                        return self.set_parameter('shutdown_iteration_limit', int(value))
                    elif channel == '42' and command == '00':
                        return self.set_parameter('data_retention_days', int(value))
                    elif channel == '43' and command == '00':
                        return self.set_parameter('backup_enabled', bool(int(value)))
                    elif channel == '99' and command == '00':
                        return self.set_parameter('emergency_mode', False)
                    else:
                        print(f'Unknown channel/command combination: Channel {channel}, Command {command} with value: {value}')
                        return False
                except (ValueError, TypeError) as e:
                    print(f'Invalid value for channel {channel} command {command}: {value!r} ({e})')
                    return False
            else:
                print(f'Invalid payload format: {payload} (minimum 4 characters required for [Channel][Command][Value] format)')
                return False
                
        except Exception as e:
            print(f"Error processing LoRa command '{payload}': {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status including all parameters"""
        return {
            'timestamp': datetime.now().isoformat(),
            'parameters': self.parameters.copy(),
            'lora_status': {
                'listening': self.listening,
                'handler_available': self.lora_handler is not None,
                'queue_size': self.lora_handler.get_queue_depth() if self.lora_handler else 0
            }
        }
    
    def sync_with_lora_config(self):
        """Synchronize runtime parameters with LoRa handler configuration"""
        if not self.lora_handler:
            print("No LoRa handler available for sync")
            return False
        
        try:
            # Get current LoRa config
            lora_config = self.lora_handler.config
            
            # Validate/coerce all changes in-memory first, then persist once.
            # Avoids one save_parameters() call per changed key on constrained hardware.
            changes = []
            prior_values = {}
            for key, value in lora_config.items():
                if key in self.parameters and self.parameters[key] != value:
                    old_value = self.parameters[key]
                    if self._validate_param(key, value):
                        coerced = self._coerce_param(key, value)
                        prior_values[key] = old_value
                        self.parameters[key] = coerced
                        changes.append(f"{key}: {old_value} → {coerced}")
                    else:
                        print(f"  Warning: skipped out-of-range value for '{key}': {value!r}")

            if changes:
                if not self.save_parameters({k: self.parameters[k] for k in prior_values}, merge=True):
                    for key, old_value in prior_values.items():
                        self.parameters[key] = old_value
                    print(f"Warning: failed to persist {len(changes)} synced parameter(s) to disk")
                    return False
                print(f"🔄 Synced {len(changes)} parameters from LoRa config:")
                for change in changes:
                    print(f"  {change}")
                return True
            else:
                print("✓ Runtime parameters already in sync with LoRa config")
                return True
                
        except Exception as e:
            print(f"Error syncing with LoRa config: {e}")
            return False
    
    def print_status(self):
        """Print current system status"""
        status = self.get_system_status()
        print(f"\n=== System Status ({status['timestamp']}) ===")
        
        params = status['parameters']
        print("Flood Detection:")
        print(f"  Area Threshold: {params['area_threshold']}%")
        print(f"  Stage Threshold: {params['stage_threshold']} cm")
        
        print("\nTiming:")
        print(f"  Monitoring Frequency: {params['monitoring_frequency']} min")
        print(f"  Emergency Frequency: {params['emergency_frequency']} min")
        print(f"  Photo Interval: {params['photo_interval']} s")
        
        print("\nSystem Control:")
        print(f"  Emergency Mode: {params['emergency_mode']}")
        print(f"  Debug Mode: {params['debug_mode']}")
        
        print("\nPerformance:")
        print(f"  Max Retransmissions: {params['max_retransmissions']}")
        
        print(f"\nLoRa Status: {status['lora_status']}")
        print("=" * 50)
    
    def close(self):
        """Clean up resources"""
        self.listening = False
        if self.lora_handler:
            self.lora_handler.stop_listening()
        print("LoRa runtime integration closed")

# Global instance for easy access
_runtime_manager = None

def get_runtime_manager() -> LoRaRuntimeManager:
    """Get the global runtime manager instance"""
    global _runtime_manager
    if _runtime_manager is None:
        _runtime_manager = LoRaRuntimeManager()
    return _runtime_manager

def set_runtime_manager(manager: LoRaRuntimeManager) -> None:
    """Publish `manager` as this process's LoRaRuntimeManager singleton.

    Used exclusively by tools/lora_daemon.py: the daemon constructs its own
    LoRaRuntimeManager(lora_handler=<real handler>) directly, then publishes
    it here so that get_runtime_manager() -- and the module-level
    get_parameter()/set_parameter() convenience functions, which always route
    through it -- return this exact instance within the daemon process. That
    matters because _listen_loop()'s fast-path emergency-message detection
    calls the module-level set_parameter(), which must reach the same
    instance the daemon registered its emergency-mode callback against, or
    the WittyPi shutdown-schedule safety path silently never fires.
    """
    global _runtime_manager
    _runtime_manager = manager

def get_parameter(key: str, default: Any = None) -> Any:
    """Convenience function to get a runtime parameter"""
    manager = get_runtime_manager()
    return manager.get_parameter(key, default)

def set_parameter(key: str, value: Any) -> bool:
    """Convenience function to set a runtime parameter"""
    manager = get_runtime_manager()
    return manager.set_parameter(key, value)

def register_callback(parameter: str, callback: Callable):
    """Convenience function to register a parameter update callback"""
    manager = get_runtime_manager()
    manager.register_update_callback(parameter, callback)

def sync_lora_parameters():
    """Convenience function to sync runtime parameters with LoRa config"""
    manager = get_runtime_manager()
    return manager.sync_with_lora_config()

def process_lora_payload(payload: str):
    """Convenience function to process LoRa payload in new format"""
    manager = get_runtime_manager()
    return manager.process_lora_payload(payload)

# Integration functions for ticktalk_main.py
def integrate_with_ticktalk():
    """Set up integration with ticktalk_main.py system"""
    manager = get_runtime_manager()
    
    # Register callbacks for ticktalk_main.py integration
    def on_emergency_mode_changed(value, old_value):
        if value:
            print("🚨 EMERGENCY MODE ACTIVATED - System will increase monitoring frequency!")
            # Could trigger immediate photo capture or other emergency actions
        else:
            print("✅ Emergency mode deactivated - Returning to normal operation")
    
    def on_debug_mode_changed(value, old_value):
        if value:
            print("🐛 Debug mode enabled - Verbose logging active")
        else:
            print("🐛 Debug mode disabled - Normal logging")
    
    def on_photo_interval_changed(value, old_value):
        print(f"📸 Photo interval changed: {old_value} → {value} s")
    
    def on_monitoring_frequency_changed(value, old_value):
        print(f"⏰ Monitoring frequency changed: {old_value} → {value} minutes")
    
    # Register the callbacks
    manager.register_update_callback('emergency_mode', on_emergency_mode_changed)
    manager.register_update_callback('debug_mode', on_debug_mode_changed)
    manager.register_update_callback('photo_interval', on_photo_interval_changed)
    manager.register_update_callback('monitoring_frequency', on_monitoring_frequency_changed)
    
    # No sync from lora_config.json here. It ran on every wake and copied the
    # LoRa handler's copy of each shared key over runtime_config.json, which
    # reverted IP downlink and hand-made changes (only LoRa commands update
    # both files). LoRa commands already reach runtime_config.json through
    # the daemon's runtime callback (_adopt_daemon_owned_handler).
    
    print("✓ LoRa runtime integration set up for ticktalk_main.py")

# Example usage and testing
if __name__ == "__main__":
    # Set up integration
    integrate_with_ticktalk()
    
    # Get the runtime manager
    manager = get_runtime_manager()
    
    # Print initial status
    manager.print_status()
    
    # Test some parameter updates using new format
    print("\n--- Testing Parameter Updates (New Format) ---")
    manager.process_lora_payload('1090')  # Set area threshold to 0%
    manager.process_lora_payload('1010')  # Set area threshold to 100%
    manager.process_lora_payload('1191')  # Set stage threshold to 1 cm
    manager.process_lora_payload('1292')  # Set monitoring frequency to 2 min
    manager.process_lora_payload('2100')  # Activate emergency mode
    
    # Print updated status
    manager.print_status()
    
    # Test deactivating emergency mode
    manager.process_lora_payload('9900')
    manager.print_status()
    
    # Test some parameter updates using old format (backward compatibility)
    print("\n--- Testing Parameter Updates (Old Format) ---")
    manager.process_lora_command('10', 5)  # Set area threshold to 50%
    manager.process_lora_command('11', 75)  # Set stage threshold to 75 cm
    manager.process_lora_command('12', 30)  # Set monitoring frequency to 30 min
    manager.process_lora_command('21', None)  # Activate emergency mode
    
    # Print updated status
    manager.print_status()
    
    # Test deactivating emergency mode
    manager.process_lora_command('99', None)
    manager.print_status()
    
    # Clean up
    manager.close()

