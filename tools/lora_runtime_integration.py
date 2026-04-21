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

import json
import os
import time
import threading
from typing import Dict, Any, Optional, Callable
from datetime import datetime

# Import the LoRa handler
import sys
import os

# Determine the best import strategy based on current context
current_file = os.path.abspath(__file__)
current_dir = os.path.dirname(current_file)
parent_dir = os.path.dirname(current_dir)
working_dir = os.getcwd()

print(f"🔧 Import context:")
print(f"   Current file: {current_file}")
print(f"   Current directory: {current_dir}")
print(f"   Parent directory: {parent_dir}")
print(f"   Working directory: {working_dir}")

try:
    # Strategy 1: Direct import (when running from tools directory)
    from lora_handler_concurrent import get_lora_handler, get_config_value
    print("✅ Imported lora_handler_concurrent directly")
except ImportError:
    try:
        # Strategy 2: Tools prefix (when running from parent directory)
        from tools.lora_handler_concurrent import get_lora_handler, get_config_value
        print("✅ Imported lora_handler_concurrent via tools. prefix")
    except ImportError:
        try:
            # Strategy 3: Path manipulation with explicit file path
            # Add both current and parent directories to Python path
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
                print(f"🔧 Added to Python path: {current_dir}")
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
                print(f"🔧 Added to Python path: {parent_dir}")
            
            # Try direct import again
            from lora_handler_concurrent import get_lora_handler, get_config_value
            print("✅ Imported lora_handler_concurrent via path manipulation")
        except ImportError:
            try:
                # Strategy 4: Import from specific file path
                import importlib.util
                spec = importlib.util.spec_from_file_location("lora_handler_concurrent", os.path.join(current_dir, "lora_handler_concurrent.py"))
                if spec and spec.loader:
                    lora_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(lora_module)
                    get_lora_handler = lora_module.get_lora_handler
                    get_config_value = lora_module.get_config_value
                    print("✅ Imported lora_handler_concurrent via explicit file path")
                else:
                    raise ImportError("Could not load module from file path")
            except Exception as e:
                print(f"❌ Failed to import lora_handler_concurrent: {e}")
                print(f"   Current working directory: {working_dir}")
                print(f"   File location: {current_file}")
                print(f"   Python path: {sys.path}")
                print(f"   Available files in tools/: {os.listdir(current_dir) if os.path.exists(current_dir) else 'N/A'}")
                print(f"   Available files in parent/: {os.listdir(parent_dir) if os.path.exists(parent_dir) else 'N/A'}")
                raise

class LoRaRuntimeManager:
    """
    Manages runtime parameters that can be updated via LoRa commands
    and integrates with the ticktalk_main.py system
    """

    # Inclusive (min, max) bounds for each settable parameter.
    # Values outside these ranges are rejected with a warning.
    _PARAM_RANGES: dict = {
        'area_threshold':                   (0, 100),
        'stage_threshold':                  (0, 1000),
        'monitoring_frequency':             (1, 10080),
        'emergency_frequency':              (1, 1440),
        'photo_interval':                   (1, 1440),
        'neighborhood_emergency_frequency': (1, 1440),
        'max_retransmissions':              (0, 10),
        'shutdown_iteration_limit':         (1, 100),
        'data_retention_days':              (1, 365),
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
    })

    def __init__(self, config_file='runtime_config.json'):
        self.config_file = config_file
        self.parameters = self.load_parameters()
        self.update_callbacks = {}
        self.lora_handler = None
        self.listening = False
        self.listener_thread = None
        
        # Initialize LoRa handler and start listening
        self._init_lora_handler()
    
    def _init_lora_handler(self):
        """Initialize LoRa handler and start listening for commands"""
        try:
            print(f"🔧 Getting LoRa handler...")
            self.lora_handler = get_lora_handler()
            print(f"🔧 LoRa handler received: {type(self.lora_handler)}")
            print(f"🔧 LoRa handler methods: {[method for method in dir(self.lora_handler) if not method.startswith('_')]}")
            
            # Register callback to sync LoRa commands with runtime parameters
            def sync_lora_command(key, value):
                """Sync LoRa parameter updates with runtime parameters"""
                print(f"🔄 LoRa parameter update: {key} = {value}")
                print(f"   Current runtime value: {self.get_parameter(key)}")
                
                # Update the runtime parameter directly
                self.set_parameter(key, value)
                print(f"✅ Runtime parameter '{key}' synced to {value}")
                print(f"   New runtime value: {self.get_parameter(key)}")
            
            print(f"🔧 Attempting to set runtime callback...")
            self.lora_handler.set_runtime_callback(sync_lora_command)
            
            self.lora_handler.start_listening()
            self.listening = True
            print("✓ LoRa runtime integration initialized with command sync")
        except Exception as e:
            print(f"✗ Failed to initialize LoRa runtime integration: {e}")
            print("⚠️ LoRa functionality will not be available")
            print("   Emergency mode and LoRa commands will not work")
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
            'photo_interval': 30,           # Photo capture interval (minutes)
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
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r') as f:
                    loaded_params = json.load(f)
                    # Merge with defaults to ensure all parameters exist
                    for key, value in default_params.items():
                        if key not in loaded_params:
                            loaded_params[key] = value
                    return loaded_params
            except Exception as e:
                print(f"Error loading runtime config: {e}, using defaults")
                return default_params
        else:
            self.save_parameters(default_params)
            return default_params
    
    def save_parameters(self, params: Dict[str, Any]):
        """Save runtime parameters to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(params, f, indent=2)
        except Exception as e:
            print(f"Error saving runtime config: {e}")
    
    def get_parameter(self, key: str, default: Any = None) -> Any:
        """Get a runtime parameter value"""
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
        except (TypeError, ValueError):
            print(f"Warning: non-numeric value {value!r} for parameter '{key}', rejected")
            return False
        # Reject fractional values for integer-only params (e.g. 1.9 must not silently become 1)
        if key in self._INT_PARAMS and numeric != int(numeric):
            print(f"Warning: fractional value {value} for integer parameter '{key}', rejected")
            return False
        if not (lo <= numeric <= hi):
            print(f"Warning: value {value} for '{key}' is outside allowed range [{lo}, {hi}], rejected")
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
        old_value = self.parameters.get(key)
        self.parameters[key] = coerced
        self.save_parameters(self.parameters)

        print(f"Runtime parameter '{key}' updated: {old_value} → {coerced}")

        if key in self.update_callbacks:
            for callback in self.update_callbacks[key]:
                try:
                    callback(coerced, old_value)
                except Exception as e:
                    print(f"Error in parameter update callback for '{key}': {e}")
        return True
    
    def register_update_callback(self, parameter: str, callback: Callable):
        """Register a callback to be called when a parameter is updated"""
        if parameter not in self.update_callbacks:
            self.update_callbacks[parameter] = []
        self.update_callbacks[parameter].append(callback)
    
    def process_lora_payload(self, payload: str) -> bool:
        """Process LoRa payload directly from the handler (alias for process_lora_command)"""
        return self.process_lora_command(payload)
    
    def process_lora_command(self, command: str, value: Any) -> bool:
        """Process incoming LoRa command in old format (backward compatibility)"""
        command_mapping = {
            # Basic flood detection commands
            '10': lambda v: self.set_parameter('area_threshold', v * 10),  # Area threshold (10% increments)
            '11': lambda v: self.set_parameter('stage_threshold', v),      # Stage threshold (cm)
            
            # Timing commands
            '12': lambda v: self.set_parameter('monitoring_frequency', v), # Monitoring frequency (minutes)
            '13': lambda v: self.set_parameter('emergency_frequency', v),  # Emergency frequency (minutes)
            '14': lambda v: self.set_parameter('photo_interval', v),       # Photo interval (minutes)
            '15': lambda v: self.set_parameter('neighborhood_emergency_frequency', v), # Neighborhood frequency
            
            # System control commands
            '21': lambda v: self.set_parameter('emergency_mode', True),          # Emergency mode (no value needed)
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
            print(f"DEBUG: Processing LoRa command payload: '{payload}'")
            
            # Handle legacy format (backward compatibility)
            if payload == '21':
                self.set_parameter('emergency_mode', True)
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
                    return _set('stage_threshold', float(val_int))
                elif channel == '12' and command == '92':
                    return _set('monitoring_frequency', val_int)
                elif channel == '13' and command == '93':
                    return _set('emergency_frequency', val_int)
                elif channel == '14' and command == '94':
                    return _set('photo_interval', val_int)
                elif channel == '15' and command == '95':
                    return _set('neighborhood_emergency_frequency', val_int)
                elif channel == '22' and command == '00':
                    self.set_parameter('debug_mode', bool(val_int))
                    return True
                elif channel == '31' and command == '00':
                    return True
                elif channel == '32' and command == '00':
                    return _set('max_retransmissions', val_int)
                elif channel == '40' and command == '00':
                    self.set_parameter('auto_shutdown_enabled', bool(val_int))
                    return True
                elif channel == '41' and command == '00':
                    return _set('shutdown_iteration_limit', val_int)
                elif channel == '42' and command == '00':
                    return _set('data_retention_days', val_int)
                elif channel == '43' and command == '00':
                    self.set_parameter('backup_enabled', bool(val_int))
                    return True
                elif channel == '21' and command == '00':
                    self.set_parameter('emergency_mode', True)
                    return True
                elif channel == '99' and command == '00':
                    self.set_parameter('emergency_mode', False)
                    return True
                else:
                    print(f"Warning: unknown TLV channel/command {channel}/{command}, ignored")
                    return False

            if _is_hex_string(payload):
                tlv_cmds = _parse_tlv_commands(payload)
                if tlv_cmds is not None and len(tlv_cmds) > 0:
                    results = [_apply_command_tlv(ch, cmd, vbytes) for (ch, cmd, vbytes) in tlv_cmds]
                    # All-or-nothing: any rejected/unknown command returns False so the
                    # sender knows the packet was not fully applied.
                    if not all(results):
                        failed = [f"{ch:02d}/{cmd:02d}" for (ch, cmd, _), ok in zip(tlv_cmds, results) if not ok]
                        print(f"Warning: {len(failed)}/{len(results)} TLV commands not applied: {failed}")
                    return all(results)

            # Handle new format: [Channel][Command][Value] (single)
            if len(payload) >= 4:
                channel = payload[:2]
                command = payload[2:4]
                value = payload[4:]
                
                print(f"DEBUG: Parsed - Channel: {channel}, Command: {command}, Value: {value}")
                
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
                    elif channel == '14' and command == '94':
                        return self.set_parameter('photo_interval', int(value))
                    elif channel == '15' and command == '95':
                        return self.set_parameter('neighborhood_emergency_frequency', int(value))
                    elif channel == '22' and command == '00':
                        return self.set_parameter('debug_mode', bool(int(value)))
                    elif channel == '31' and command == '00':
                        return self.set_parameter('compression_level', max(1, min(10, int(value))))
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
                    elif channel == '21' and command == '00':
                        return self.set_parameter('emergency_mode', True)
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
                'queue_size': self.lora_handler.transmit_queue.qsize() if self.lora_handler else 0
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
            
            # Update runtime parameters with any new values from LoRa
            changes = []
            for key, value in lora_config.items():
                if key in self.parameters and self.parameters[key] != value:
                    old_value = self.parameters[key]
                    self.parameters[key] = value
                    changes.append(f"{key}: {old_value} → {value}")
            
            if changes:
                print(f"🔄 Synced {len(changes)} parameters from LoRa config:")
                for change in changes:
                    print(f"  {change}")
                self.save_parameters(self.parameters)
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
        print(f"  Photo Interval: {params['photo_interval']} min")
        
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

def get_parameter(key: str, default: Any = None) -> Any:
    """Convenience function to get a runtime parameter"""
    manager = get_runtime_manager()
    return manager.get_parameter(key, default)

def set_parameter(key: str, value: Any):
    """Convenience function to set a runtime parameter"""
    manager = get_runtime_manager()
    manager.set_parameter(key, value)

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
        print(f"📸 Photo interval changed: {old_value} → {value} minutes")
    
    def on_monitoring_frequency_changed(value, old_value):
        print(f"⏰ Monitoring frequency changed: {old_value} → {value} minutes")
    
    # Register the callbacks
    manager.register_update_callback('emergency_mode', on_emergency_mode_changed)
    manager.register_update_callback('debug_mode', on_debug_mode_changed)
    manager.register_update_callback('photo_interval', on_photo_interval_changed)
    manager.register_update_callback('monitoring_frequency', on_monitoring_frequency_changed)
    
    # Perform initial sync with LoRa config
    manager.sync_with_lora_config()
    
    print("✓ LoRa runtime integration set up for ticktalk_main.py")

# Global instance for easy access from other modules
_lora_runtime_integration = None

def get_lora_runtime_integration() -> LoRaRuntimeManager:
    """Get the global LoRa runtime integration instance"""
    global _lora_runtime_integration
    if _lora_runtime_integration is None:
        _lora_runtime_integration = LoRaRuntimeManager()
    return _lora_runtime_integration

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

