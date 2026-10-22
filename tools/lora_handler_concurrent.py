#!/usr/bin/env python
import base64
import fcntl
import time
from sys import getsizeof
import socket
import stat
import struct
import serial
import threading
import queue
import json
import math
import re
import os
from typing import Dict, Any, Optional

# Transmission Data Format
# Channel 00 Type 01 Time stamp UNIX
# Channel 01 Type 04 emergency status 0/1 where 0 is monitoring & 1 is emergency
# 01 05 health 0/1 where 0 is normal operation
# 01 06 coordinate move threshold 0/1 where 0 is insignificant movement
# 02 01 battery percent
# 03 01 tilt/roll/yaw
# 04 01 lat/lon/z
# 05 01 temp four digit float as ints, celsius
# 06 01 rel humidity percent
# 07 17 camera flood detect status 0/1
# 07 27 new local max (flood growing) 0/1
# 08 18 flood bitmap compressed binary
# 09 19 status area threshold %
# 09 29 stage threshold, u16 cm
# 09 39 monitoring freq
# 09 49 emergency freq
# 09 59 neighborhood emergency status freq
# 0A 01 wittypi temperature (float, celsius; no longer sent)
# 0A 03 wittypi internal voltage (float, volts; no longer sent)

# Reception Data Format
# Format: [Channel][Command][Value]
# Channel: Two digits (00-99) representing the data channel
# Command: Two digits representing the command type
# Value: Remaining digits representing the value to set
# 
# Examples:
# 1090 -> Channel 10, Command 90, Value 0 (Area Threshold 0%)
# 1010 -> Channel 10, Command 90, Value 10 (Area Threshold 100%)
# 1191 -> Channel 11, Command 91, Value 1 (Stage Threshold 1 cm)
# 1192 -> Channel 11, Command 91, Value 92 (Stage Threshold 92 cm)
# 1292 -> Channel 12, Command 92, Value 2 (Monitoring Frequency 2 minutes)
# 1393 -> Channel 13, Command 93, Value 3 (Emergency Frequency 3 minutes)
# 2100 -> Channel 21, Command 00, Value 0 (Emergency status: system enters emergency mode)
# 9999 -> Emergency clear message (Deactivate emergency mode)




# Compact packet: channel 0C type 01 and nine bytes, 11 in all, so it fits
# US915 DR0 (SF10, 11-byte payloads). Sent instead of the full sensor packet
# when the mDot's current limit is below CHUNK_MIN_LIMIT; otherwise the sensor
# packet goes in chunks. Layout after 0C 01:
#   u32 capture time (as 00 01)
#   u8  flags: bit 0 emergency, 1 flood detected, 2 flood growing, 3 health
#       fault, 4 moved from mount, 5 under-voltage now, 6 under-voltage since boot
#   u8  battery percent            (255: none)
#   u8  Witty Pi input voltage (data['wittypi_vin']), 25 mV steps, 0-6.35 V (255: none)
#   i8  temperature, whole degrees C (-128: none)
#   u8  relative humidity percent  (255: none)
COMPACT_PACKET_SIZE = 11
# Below this payload limit the compact packet is sent in place of the sensor packet;
# from it up the sensor packet goes in chunks (encode_sensor_chunks)
CHUNK_MIN_LIMIT = COMPACT_PACKET_SIZE + 1
# Chunk size for sensor packets queued without knowing the data rate they'll go
# at: fits DR1 and up (US915 53 B, less the MAC answers a payload may share the frame with)
QUEUE_CHUNK_LIMIT = 51


def encode_compact_packet(data: Dict[str, Any]) -> bytes:
    """The key readings of a capture in 11 bytes (layout above)."""
    def num(key):
        v = data.get(key)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    flags = 0
    for bit, key in ((0, 'emergency_status'), (1, 'camera_flood_detected'),
                     (2, 'camera_flood_growing'), (3, 'health_status'), (4, 'movement_threshold')):
        if data.get(key):
            flags |= 1 << bit
    if data.get('pi_throttled') is not None:
        from tools.pi_power import pack_throttled_u8
        packed = pack_throttled_u8(data['pi_throttled'])
        flags |= (1 << 5) if packed & 0x01 else 0
        flags |= (1 << 6) if packed & 0x10 else 0

    def u8(v, scale=1.0, top=254):
        return 255 if v is None else max(0, min(top, int(round(v * scale))))

    temp = num('temperature_celsius')
    t8 = -128 if temp is None else max(-127, min(127, int(round(temp))))
    return (bytes([0x0C, 0x01]) + struct.pack(">I", int(data.get('timestamp') or 0))
            + bytes([flags, u8(num('battery_percent')), u8(num('wittypi_vin') if data.get('wittypi_vin') is not None else num('wittypi_battery_voltage'), 40)])
            + struct.pack(">b", t8) + bytes([u8(num('relative_humidity'))]))


def _config_io():
    """tools/config_io, importable whether this module was loaded as tools.x or x."""
    try:
        from tools import config_io
    except ImportError:
        import config_io
    return config_io


class LoRaSerialPortConflict(RuntimeError):
    """Raised when another OS process already owns the LoRa serial port."""


# Max rounds of _flush_mac_commands() to try, within one retry attempt of
# transmit(), when the mDot reports "Tx buffer filled by MAC Commands". A
# single flush only drains whatever's pending at that instant; this bounds
# how many extra probe uplinks we're willing to send chasing a clean drain
# before giving up and retrying the real payload anyway.
def _finite(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def _scaled_int(v, scale: float, lo: int, hi: int) -> int:
    """round(v * scale), clamped to [lo, hi]."""
    return max(lo, min(hi, int(round(float(v) * scale))))


MAC_FLUSH_MAX_ROUNDS = 3

# LoRaHandler.transmit() gives up and returns False after this long in total
# (waiting for the locks, every attempt, MAC flush rounds). It must stay well
# below LoRaHandlerClient._LONG_TIMEOUT_S: a client that times out first defers
# the packet to store-and-forward while the daemon may still send it, and the
# gateway gets it twice. transmit() never starts an AT+SENDB or a MAC flush
# that couldn't finish its response window inside the deadline, so a False
# here means the packet was not handed to the mDot after that point.
TRANSMIT_DEADLINE_S = 75
# How long _attempt_transmission() waits for the mDot's answer to AT+SENDB
SENDB_WINDOW_HEX_S = 15
SENDB_WINDOW_BYTES_S = 20
MAC_FLUSH_WINDOW_S = 20
# The listener gives up (and the daemon restarts) after this many failures in a row
LISTEN_MAX_CONSECUTIVE_FAILURES = 30


class _SkipField(Exception):
    """A value that can't be encoded (not a number, not finite, out of range)."""


def _int_in(v, lo: int, hi: int) -> int:
    """int(v) when v is a finite number inside [lo, hi]; otherwise _SkipField."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise _SkipField(f"not a number: {v!r}")
    if not math.isfinite(f) or not lo <= int(f) <= hi:
        raise _SkipField(f"out of range {lo}..{hi}: {v!r}")
    return int(f)


def _f32_val(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise _SkipField(f"not a number: {v!r}")
    if not math.isfinite(f):
        raise _SkipField(f"not finite: {v!r}")
    return f


def _u8_field(ch, t, key):
    return lambda d: bytes([ch, t, _int_in(d[key], 0, 255)])


def _u16_field(ch, t, key):
    return lambda d: bytes([ch, t]) + struct.pack(">H", _int_in(d[key], 0, 65535))


def _u32_field(ch, t, key):
    return lambda d: bytes([ch, t]) + struct.pack(">I", _int_in(d[key], 0, 0xFFFFFFFF))


def _stage_field(d):
    # centimetres, a u16: clamped rather than skipped
    return bytes([0x09, 0x29]) + struct.pack(">H", max(0, min(65535, int(_f32_val(d['stage_threshold'])))))


def _latlon_field(d):
    v = d['lat_lon_z']
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        raise _SkipField(f"expected three numbers: {v!r}")
    return bytes([0x04, 0x01]) + b"".join(struct.pack(">f", _f32_val(x)) for x in v)


def _bitmap_field(d):
    b = d['flood_bitmap_compressed']
    if isinstance(b, str):
        b = b.encode('utf-8')
    elif not isinstance(b, (bytes, bytearray)):
        b = str(b).encode('utf-8')
    return bytes([0x08, 0x18]) + struct.pack(">H", len(b)) + bytes(b)


def _throttled_field(d):
    from tools.pi_power import pack_throttled_u8
    return bytes([0x01, 0x07, pack_throttled_u8(d['pi_throttled'])])


def _tilt_field(d):
    tilt = d['tilt_roll_yaw']
    if not (isinstance(tilt, (list, tuple)) and len(tilt) == 3 and all(_finite(x) for x in tilt)):
        raise _SkipField(f"expected three finite numbers: {tilt!r}")
    return bytes([0x03, 0x02]) + struct.pack(">Hhh", int(round(float(tilt[0]) * 100)) % 36000,
                                             _scaled_int(tilt[1], 100, -32768, 32767),
                                             _scaled_int(tilt[2], 100, -32768, 32767))


def _temperature_field(d):
    return b"\x05\x02" + struct.pack(">h", _scaled_int(_f32_val(d['temperature_celsius']), 100, -32768, 32767))


# (key, builder) in wire order. Optional fields are left out when None. A value that
# can't be encoded drops its own field and nothing else. Decoders that predate a
# channel stop at the first pair they don't know, so newer channels come last
# (01 07 and after), as the comments in the history of this list explain.
_SENSOR_FIELDS = (
    ('timestamp', _u32_field(0x00, 0x01, 'timestamp')),
    ('emergency_status', _u8_field(0x01, 0x04, 'emergency_status')),
    ('health_status', _u8_field(0x01, 0x05, 'health_status')),
    ('movement_threshold', _u8_field(0x01, 0x06, 'movement_threshold')),
    ('battery_percent', _u8_field(0x02, 0x01, 'battery_percent')),
    ('lat_lon_z', _latlon_field),
    ('relative_humidity', _u8_field(0x06, 0x01, 'relative_humidity')),
    ('camera_flood_detected', _u8_field(0x07, 0x17, 'camera_flood_detected')),
    ('camera_flood_growing', _u8_field(0x07, 0x27, 'camera_flood_growing')),
    ('flood_bitmap_compressed', _bitmap_field),
    ('status_area_threshold', _u8_field(0x09, 0x19, 'status_area_threshold')),
    ('stage_threshold', _stage_field),
    ('monitoring_frequency', _u16_field(0x09, 0x39, 'monitoring_frequency')),
    ('emergency_frequency', _u16_field(0x09, 0x49, 'emergency_frequency')),
    ('neighborhood_emergency_frequency', _u16_field(0x09, 0x59, 'neighborhood_emergency_frequency')),
    ('audio_recording_enabled', _u8_field(0x09, 0x69, 'audio_recording_enabled')),
    # Pi throttled/under-voltage flags (tools/pi_power.py), packed to one byte
    ('pi_throttled', _throttled_field),
    # Registration orientation check (tools/registration_orientation.py)
    ('registration_status', _u8_field(0x01, 0x08, 'registration_status')),
    # Software release and loaded model (tools/node_version.py); the commit is IP only
    ('software_version', _u32_field(0x01, 0x09, 'software_version')),
    ('model_id', _u32_field(0x01, 0x0B, 'model_id')),
    # Dropped store-and-forward entries and the unverified-clock flag
    ('sf_dropped', lambda d: bytes([0x01, 0x0C]) + struct.pack(">I", min(_int_in(d['sf_dropped'], 0, 1 << 64), 0xFFFFFFFF))),
    ('clock_unverified', lambda d: bytes([0x01, 0x0D, 1 if d['clock_unverified'] else 0])),
    # Water percentage of the capture's mask (tools/flood_baseline.py)
    ('camera_water_pct', lambda d: bytes([0x07, 0x37, max(0, min(100, _int_in(d['camera_water_pct'], -(1 << 31), 1 << 31)))])),
    # Compact forms of the old float32 channels, 2 bytes a value instead of 4:
    # 03 02 orientation in 0.01 deg (u16 heading 0-360, i16 roll, i16 pitch),
    # 05 02 temperature as i16 0.01 C. The Witty Pi temperature, input and output
    # voltages aren't sent over LoRa (the server doesn't store them).
    ('tilt_roll_yaw', _tilt_field),
    ('temperature_celsius', _temperature_field),
)

# Order of the fields in a chunked sensor packet (encode_sensor_chunks): what an
# emergency needs first (flood, water, emergency, battery, health), then the
# readings, then GPS, IMU, the flags about the node itself, the settings, and the
# release last. A field that doesn't fit the room left in a chunk starts the next.
_CHUNK_PRIORITY = (
    'emergency_status', 'camera_flood_detected', 'camera_flood_growing', 'camera_water_pct',
    'battery_percent', 'health_status', 'movement_threshold',
    'temperature_celsius', 'relative_humidity', 'pi_throttled', 'clock_unverified',
    'lat_lon_z', 'tilt_roll_yaw', 'registration_status', 'sf_dropped',
    'status_area_threshold', 'stage_threshold', 'monitoring_frequency', 'emergency_frequency',
    'neighborhood_emergency_frequency', 'audio_recording_enabled',
    'software_version', 'model_id',
)


def _encode_fields(data: Dict[str, Any]) -> Dict[str, bytes]:
    """{key: TLV bytes} for every field of `data` that can be encoded, in wire order."""
    out = {}
    for key, build in _SENSOR_FIELDS:
        if data.get(key) is None:
            continue
        try:
            out[key] = build(data)
        except (_SkipField, TypeError, ValueError, OverflowError, KeyError, struct.error) as e:
            print(f"Skipping field {key}: {e}")
    return out


def _encode_compressed_packet(data: Dict[str, Any]) -> bytes:
    """Encode sensor data into the TLV wire format for LoRa transmission.

    Pure function of `data` alone -- no handler state involved -- so it's
    shared verbatim by LoRaHandler.compressed_encoding() (real handler,
    inside the daemon) and LoRaHandlerClient.compressed_encoding() (every
    other process), computed locally in both rather than round-tripped over
    IPC. A field that can't be encoded (not a number, not finite, out of range)
    is left out; the others still go.
    """
    return b"".join(_encode_fields(data).values())


def encode_sensor_chunks(data: Dict[str, Any], limit: int) -> list:
    """Split the sensor fields of `data` into packets of at most `limit` bytes.

    Each chunk starts with the capture time (00 01) so the server can tell which
    capture it belongs to, and the chunks are independent TLV packets. Fields go in
    _CHUNK_PRIORITY order, so the first chunk holds the most important ones. A field
    too big for an empty chunk is dropped. Returns [] without a capture time or when
    `limit` can't hold a capture time and one field; the caller then sends the
    compact packet (encode_compact_packet).
    """
    fields = _encode_fields(data)
    header = fields.pop('timestamp', None)
    fields.pop('flood_bitmap_compressed', None)  # its own packet
    if header is None or limit <= len(header):
        return []
    chunks, cur = [], bytearray(header)
    for key in _CHUNK_PRIORITY:
        tlv = fields.pop(key, None)
        if tlv is None:
            continue
        if len(header) + len(tlv) > limit:
            print(f"Field {key} ({len(tlv)} B) doesn't fit a {limit} B chunk: dropped")
            continue
        if len(cur) + len(tlv) > limit:
            chunks.append(bytes(cur))
            cur = bytearray(header)
        cur += tlv
    if len(cur) > len(header):
        chunks.append(bytes(cur))
    return chunks


class LoRaHandler:
    def __init__(self, port='/dev/ttyAMA5', config_file='lora_config.json'):
        self.port = port
        self.config_file = config_file
        self.config = self.load_config()
        
        # Threading components
        self.transmit_queue = queue.Queue()
        self.listening = False
        self.listener_thread = None
        self.transmit_lock = threading.Lock()

        # Callback for runtime integration
        self.runtime_callback = None

        # Size limit tracking
        self.current_size_limit = 242  # Default LoRaWAN payload size

        # Transmission status tracking
        self.last_transmission_status = None
        self.transmission_history = []

        # Configure the serial port
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=115200,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=1,
                write_timeout=1   # a stuck UART must not block a write forever
            )

            if not self.ser.is_open:
                print(f"❌ Serial port {port} failed to open")
                raise RuntimeError(f"Serial port {port} failed to open")

        except serial.SerialException as e:
            print(f"❌ Serial port error on {port}: {e}")
            print(f"   Check if device exists and has proper permissions")
            raise RuntimeError(f"Serial port {port} error: {e}")
        except Exception as e:
            print(f"❌ Unexpected error initializing serial port {port}: {e}")
            raise RuntimeError(f"Serial port {port} initialization failed: {e}")

        # Inter-process UART lock (fcntl.lockf — process-level, POSIX record locks).
        # Opened after serial port succeeds to avoid leaking the FD if init fails.
        # /run/lock is the standard runtime lock directory (tmpfs, cleared on reboot).
        # os.open with mode 0o600 creates the file with owner-only permissions,
        # preventing symlink/hardlink attacks possible with world-writable /tmp.
        _lock_path = '/run/lock/watercam-lora.lock'
        self._lock_fd = os.fdopen(
            os.open(_lock_path, os.O_CREAT | os.O_WRONLY, 0o600), 'w'
        )
        # Construction guard: flock(LOCK_EX | LOCK_NB) is independent of the
        # lockf calls used during transmission and is held for the lifetime of
        # this object.  A second OS process that tries to construct a handler
        # will fail here instead of silently opening the same serial port.
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_fd.close()
            # self.ser (the already-opened serial port, above) was leaked here
            # on every conflict: never closed, so a failed construction still
            # held an open FD on /dev/ttyAMA5 for as long as this exception's
            # traceback kept the partially-built handler alive. Close it
            # before raising so a failed attempt doesn't hold hardware/FDs.
            try:
                self.ser.close()
            except Exception:
                pass
            raise LoRaSerialPortConflict(
                "LoRa serial port already owned by another process"
            ) from None
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        default_config = {
            'area_threshold': 10,
            'stage_threshold': 50,
            'monitoring_frequency': 60,
            'emergency_frequency': 10,
            'neighborhood_emergency_frequency': 30,
            'photo_interval': 60,
            'debug_mode': False,
            'max_retransmissions': 3,
            'auto_shutdown_enabled': True,
            'shutdown_iteration_limit': 2,
            'data_retention_days': 0,   # keep everything (tools/data_retention.py)
            'backup_enabled': True,
            'emergency_mode': False,
            'lora_sf_enabled': True,
            'lora_sf_drain_per_cycle': 2,
            'lora_sf_max_depth': 96,
            'lora_sf_max_age_days': 7,
        }
        
        config_io = _config_io()
        try:
            # restores lora_config.json from its .bak if a power cut damaged it
            return {**default_config, **config_io.read_json(self.config_file)}
        except FileNotFoundError:
            self.save_config(default_config)
            return default_config
        except Exception as e:
            print(f"Error loading config: {e}, using defaults")
            return default_config
    
    def save_config(self, config: Dict[str, Any]):
        """Save configuration to file (atomically: a power cut leaves old or new)"""
        config_io = _config_io()
        try:
            with config_io.locked(self.config_file):
                config_io.write_json(self.config_file, config)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def update_config(self, key: str, value: Any):
        """Update a configuration value and save to file"""
        self.config[key] = value
        self.save_config(self.config)
        print(f"Updated {key} to {value}")
        
        # Call runtime callback if registered
        print(f"DEBUG: Runtime callback registered: {self.runtime_callback is not None}")
        if self.runtime_callback:
            try:
                print(f"DEBUG: Calling runtime callback with key='{key}', value={value}")
                self.runtime_callback(key, value)
                print(f"DEBUG: Runtime callback completed successfully")
            except Exception as e:
                print(f"Error in runtime callback for {key}: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"DEBUG: No runtime callback registered")
    
    def get_config(self, key: str, default: Any = None) -> Any:
        """Get a configuration value"""
        return self.config.get(key, default)
    
    def set_runtime_callback(self, callback):
        """Set callback for runtime integration updates"""
        self.runtime_callback = callback
        print("Runtime callback registered with LoRa handler")
    
    def start_listening(self):
        """Start the listening thread"""
        if not self.listening:
            self.listening = True
            self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
            self.listener_thread.start()
            print("LoRa listener started")
    
    def stop_listening(self):
        """Stop the listening thread"""
        self.listening = False
        if self.listener_thread and self.listener_thread.is_alive():
            self.listener_thread.join(timeout=2)
            print("LoRa listener stopped")

    def _at_query(self, command: bytes, timeout_s: float, done) -> list:
        """Send one AT command and collect reply lines until done(line) or timeout.

        Holds transmit_lock + the inter-process fcntl lock so the listener thread
        cannot consume the reply between the write and the read. Lines go through
        _read_line(), so a downlink arriving meanwhile is queued, not mistaken for
        part of the reply. Returns the stripped non-empty lines read.
        """
        lines = []
        with self.transmit_lock:
            fcntl.lockf(self._lock_fd, fcntl.LOCK_EX)
            try:
                self._drain_input()
                self.ser.write(command)
                end = time.time() + timeout_s
                while time.time() < end:
                    if not self.ser.in_waiting > 0:
                        time.sleep(0.05)
                        continue
                    raw = self._read_line()
                    if not raw:
                        continue
                    line = raw.decode('utf-8', errors='replace').strip()
                    if not line:
                        continue
                    lines.append(line)
                    if done(line):
                        break
            finally:
                fcntl.lockf(self._lock_fd, fcntl.LOCK_UN)
        return lines

    def is_joined(self) -> bool:
        """Return True if the mDot is currently joined to a LoRaWAN network.

        AT+NJS? answers a line that is exactly 0 or 1 (after the echo); nothing
        else in the reply counts. Waits up to 3 s for it before concluding.
        """
        try:
            lines = self._at_query(b'AT+NJS?\r\n', 3.0, lambda l: l in ('0', '1'))
            return bool(lines) and lines[-1] == '1'
        except Exception:
            return False


    def keepalive(self) -> bool:
        """Send the mDot AT+ALIVE, the LoRa daemon's keep-alive; True on OK.

        mDot firmware 4.1.5-wc2610-sw3 and later arm their serial-silence restart
        only after this command (older firmware answers ERROR, which is harmless).
        Holds the same locks as is_joined() so the listener can't take the reply.
        """
        try:
            lines = self._at_query(b'AT+ALIVE\r\n', 2.0, lambda l: 'OK' in l or 'ERROR' in l)
            return bool(lines) and 'OK' in lines[-1]
        except Exception:
            return False

    def link_check(self, timeout_s: float = 20.0) -> Dict[str, Any]:
        """Ask the network whether it hears this node (AT+NLC, a LinkCheckReq).

        AT+NJS only reports the saved session, which stays 1 with no gateway in
        range. A link check needs the network server's answer: on success the mDot
        prints "<dB above the demodulation floor>,<gateways that heard it>".
        Returns {"ok", "margin_db", "gateways", "error"}; never raises.
        """
        import re as _re
        result = {"ok": False, "margin_db": None, "gateways": 0, "error": None}
        try:
            lines = self._at_query(b'AT+NLC\r\n', timeout_s, lambda l: 'OK' in l or 'ERROR' in l)
            text = '\n'.join(lines)
            m = None
            for line in lines:
                m = _re.fullmatch(r'\s*(\d+),(\d+)\s*', line) or m
            if m and 'OK' in text:
                result.update(ok=int(m.group(2)) > 0, margin_db=int(m.group(1)), gateways=int(m.group(2)))
            else:
                result["error"] = text.strip().splitlines()[-1] if text.strip() else "no answer"
        except Exception as e:
            result["error"] = str(e)
        return result

    # ------------------------------------------------------------------
    # Reading lines from the mDot
    #
    # Every line any code reads from the port goes through _read_line(). The
    # mDot prints each Class C downlink on its own ("PACKET:PORT=..") and
    # "EMERGENCY:" lines whenever they arrive, including in the middle of an
    # AT reply. _read_line() puts those on a queue instead of returning them,
    # so an AT path can never swallow one. They are decoded by
    # _dispatch_downlinks(), which only the listener thread calls and never
    # while it holds the serial lock (decode() may transmit a status reply).
    # ------------------------------------------------------------------

    _DOWNLINK_RE = re.compile(r'PACKET:PORT=(\d+),SIZE=(\d+),DATA=([0-9A-Fa-f]*)')
    _ASYNC_PREFIXES = ('PACKET:', 'EMERGENCY:')
    _RX_PARTIAL_MAX_BYTES = 1024
    _RX_PARTIAL_MAX_AGE_S = 5.0
    # Class defaults so a handler built with __new__ (tests) still works.
    _rx_partial = b''
    _rx_partial_t = 0.0
    listener_failed = False

    def _downlink_q(self) -> "queue.Queue":
        return self.__dict__.setdefault('_downlink_queue', queue.Queue())

    def _read_line(self):
        """Read one line from the port; the caller holds the serial lock.

        Returns the line as bytes (terminator included) when it is an ordinary
        line, b'' when nothing was read, and None when the bytes read were a
        downlink/EMERGENCY line (queued), or the start of a line that a
        readline() timeout cut short (kept and joined with the next read).
        """
        raw = self.ser.read_until()
        if not isinstance(raw, (bytes, bytearray)):
            return b''
        now = time.time()
        if self._rx_partial and now - self._rx_partial_t > self._RX_PARTIAL_MAX_AGE_S:
            print(f"DEBUG: Dropping stale partial line: {self._rx_partial[:40]!r}")
            self._rx_partial = b''
        if not raw:
            return b''
        line = self._rx_partial + bytes(raw)
        if not line.endswith(b'\n'):
            if len(line) > self._RX_PARTIAL_MAX_BYTES:
                print(f"DEBUG: Dropping oversize partial line ({len(line)} bytes)")
                self._rx_partial = b''
            else:
                if not self._rx_partial:
                    self._rx_partial_t = now
                self._rx_partial = line
            return None
        self._rx_partial = b''
        try:
            text = line.decode('utf-8').strip()
        except UnicodeDecodeError:
            return line
        if text.startswith(self._ASYNC_PREFIXES):
            self._downlink_q().put(text)
            return None
        return line

    def _drain_input(self) -> None:
        """Replace reset_input_buffer(): read what is buffered so downlinks are kept.

        Ordinary lines are discarded, as the reset did. A partial line is kept
        only if it can be the start of a downlink.
        """
        try:
            for _ in range(200):
                if not self.ser.in_waiting > 0:
                    break
                if self._read_line() == b'':
                    break
        except TypeError:
            pass
        if self._rx_partial and not self._rx_partial.startswith(
                tuple(p.encode() for p in self._ASYNC_PREFIXES)):
            self._rx_partial = b''

    def _dispatch_downlinks(self) -> None:
        """Handle queued downlink lines. Call without holding the serial lock."""
        q = self._downlink_q()
        while True:
            try:
                text = q.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_async_line(text)
            except Exception as e:
                print(f"Error processing downlink line: {e}")
                import traceback
                traceback.print_exc()

    def _handle_async_line(self, text: str) -> None:
        """One queued line: a downlink to decode, or an EMERGENCY: note."""
        if text.startswith('EMERGENCY:'):
            # The mDot's line for the remote start downlink '!' (0x21). '!' only
            # powers a sleeping Pi on; it is not emergency mode, which is its own
            # command (21 91 HH). The Pi only sees the line when it was already on.
            if self._is_emergency_message(text):
                print(f"ℹ️ Remote start request from the mDot (Pi already on): '{text}'")
            else:
                print(f"ℹ️ mDot: {text}")
            return
        m = self._DOWNLINK_RE.fullmatch(text)
        if not m:
            print(f"⚠️ Malformed downlink line ignored: '{text[:80]}'")
            return
        size, data = int(m.group(2)), m.group(3)
        if len(data) != 2 * size:
            print(f"⚠️ Downlink size mismatch (SIZE={size}, {len(data) // 2} bytes of data): ignored")
            return
        if not data:
            return
        print(f"📡 LoRa packet received: {data}")
        self.decode(data)

    def _listen_loop(self):
        """Main listening loop - runs in separate thread"""
        print('Listening for incoming packets')
        failures = 0
        while self.listening:
            try:
                self._dispatch_downlinks()
                if self.ser.in_waiting > 0:
                    with self.transmit_lock:
                        fcntl.lockf(self._lock_fd, fcntl.LOCK_EX)
                        try:
                            # the lock wait can be long (a send); look again inside
                            raw = self._read_line() if self.ser.in_waiting > 0 else b''
                        finally:
                            fcntl.lockf(self._lock_fd, fcntl.LOCK_UN)
                    self._dispatch_downlinks()
                    failures = 0
                    if not raw:
                        continue
                    try:
                        res = raw.decode().strip()
                        print(f"DEBUG: Raw received: '{res}'")
                        # Skip empty messages
                        if not res:
                            continue

                        # Skip AT+SENDB= messages (these are transmission commands, not incoming data)
                        if res.startswith('AT+SENDB='):
                            print(f"DEBUG: Skipping transmission command echo: '{res[:50]}...'")
                            continue

                        # Validate message integrity before processing
                        if not self._is_valid_message(res):
                            print(f"⚠️ Skipping corrupted/invalid message: '{res}'")
                            continue

                        # Check for +TXS: responses (size limit information)
                        if self._is_txs_response(res):
                            size_limit = self._extract_txs_size_limit(res)
                            if size_limit is not None:
                                print(f"📏 mDot size limit received: {size_limit} bytes")
                                # Store the size limit for use in transmission
                                self.current_size_limit = size_limit
                            else:
                                print(f"DEBUG: Could not extract size limit from TXS response: '{res}'")
                        # Check for transmission-related responses
                        elif self._is_transmission_response(res):
                            status = self._extract_transmission_status(res)
                            # Store the transmission status
                            self.last_transmission_status = status
                            self.transmission_history.append(status)
                            # Keep only last 10 entries to prevent memory bloat
                            if len(self.transmission_history) > 10:
                                self.transmission_history.pop(0)

                            if status['success']:
                                print(f"✅ Transmission success: {status['details'].get('confirmation', 'OK')}")
                            else:
                                print(f"❌ Transmission error: {status['details'].get('error', 'Unknown error')}")
                            print(f"📡 Transmission response details: {status}")
                            # Could trigger callbacks or update transmission status here
                        # Then check if it's an AT command response (lower priority)
                        elif self._is_at_response(res):
                            print(f"DEBUG: AT response (ignoring): '{res}'")
                        else:
                            print(f"DEBUG: Non-LoRa message (ignoring): '{res}'")
                    except UnicodeDecodeError:
                        print(f"Received binary data (hex): {raw.hex()}")
                else:
                    time.sleep(0.5)  # Small delay to prevent busy waiting
                    failures = 0
            except Exception as e:
                print(f"Error in listen loop: {e}")

                # Handle serial port disconnection specifically
                if "device disconnected" in str(e).lower() or "no data" in str(e).lower():
                    print(f"⚠️ Serial port disconnected or no data available: {e}")
                    # Check connection and attempt reconnection if needed
                    if not self._check_serial_connection():
                        if self._attempt_reconnection():
                            print("✅ Reconnection successful, continuing...")
                            failures = 0
                            continue
                        else:
                            print("❌ Reconnection failed, stopping listener")
                            self.listener_failed = True
                            break
                    else:
                        print("⚠️ Connection appears fine, waiting before retry...")
                        time.sleep(2)
                        continue
                else:
                    import traceback
                    traceback.print_exc()
                    failures += 1
                    if failures >= LISTEN_MAX_CONSECUTIVE_FAILURES:
                        print(f"❌ Listener failed {failures} times in a row, stopping listener")
                        self.listener_failed = True
                        break
                    time.sleep(1)  # Longer delay on error

    def transmit(self, content: bytes, max_retries: int = 2,
                 deadline_s: float = TRANSMIT_DEADLINE_S) -> bool:
        """Transmit data with thread safety and error recovery.

        Returns False once deadline_s (TRANSMIT_DEADLINE_S) has passed, counting
        the wait for the locks: no new attempt or MAC flush starts unless its
        response window fits in the time left.
        """
        deadline = time.monotonic() + deadline_s
        window = SENDB_WINDOW_HEX_S if isinstance(content, str) else SENDB_WINDOW_BYTES_S

        def left() -> float:
            return deadline - time.monotonic()

        if not self.transmit_lock.acquire(timeout=max(0.0, left() - window)):
            print(f"ERROR: LoRa transmit lock still held after {deadline_s - left():.0f} s; not sent")
            return False
        try:
            # Hold inter-process lock for entire TX sequence so concurrent processes
            # (e.g. lora_listener subprocess) cannot steal the SENDB response.
            if not self._lockf_until(deadline - window):
                print(f"ERROR: LoRa UART lock still held after {deadline_s - left():.0f} s; not sent")
                return False
            try:
                for attempt in range(max_retries + 1):
                    if left() < window:
                        print(f"ERROR: Transmit deadline ({deadline_s:.0f} s) reached before attempt "
                              f"{attempt + 1}: giving up")
                        return False
                    try:
                        if attempt > 0:
                            print(f"DEBUG: Retry attempt {attempt}/{max_retries}")
                            if not self._clear_mdot_input():
                                print("ERROR: Failed to clear mDot input, aborting retry")
                                return False

                        print(f"DEBUG: Starting transmission of {len(content)} bytes (attempt {attempt + 1})")
                        print(f"DEBUG: Content to send: {content}")

                        # Use the stored size limit (kept current by the listener loop).
                        # Do NOT send AT+TXS here — it pollutes the RX buffer with "242\r\nOK\r\n"
                        # that _attempt_transmission() would then misread as the SENDB response.
                        size_limit = self.current_size_limit
                        if size_limit < 11:
                            print(f"WARNING: Stored size limit {size_limit}B is invalid; refreshing from mDot")
                            self._query_txs_locked()
                            size_limit = self.current_size_limit
                        print(f"DEBUG: Using stored size limit: {size_limit} bytes")

                        if left() < window:
                            print(f"ERROR: Transmit deadline ({deadline_s:.0f} s) reached: giving up")
                            return False
                        success, error_message = self._attempt_transmission(content, size_limit)

                        if success:
                            return True
                        else:
                            print(f"ERROR: Transmission attempt {attempt + 1} failed: {error_message}")
                            if attempt < max_retries:
                                if 'Tx buffer filled by MAC Commands' in error_message:
                                    # A single flush only drains whatever MAC command
                                    # backlog is pending at that instant -- if the
                                    # backlog needs more than one uplink to fully clear
                                    # (or a new command queues in the gap), one flush
                                    # attempt isn't enough and the real payload's retry
                                    # hits the same error again. Loop until the mDot
                                    # confirms a clean drain (or a small round cap is
                                    # hit) before retrying, rather than assuming success
                                    # after exactly one attempt.
                                    print("DEBUG: MAC buffer full — flushing LoRaWAN MAC queue with probe uplink...")
                                    for flush_round in range(1, MAC_FLUSH_MAX_ROUNDS + 1):
                                        # leave room for the retry itself
                                        if left() < MAC_FLUSH_WINDOW_S + window:
                                            print("DEBUG: MAC flush skipped: not enough time left before the deadline")
                                            break
                                        if self._flush_mac_commands():
                                            print(f"DEBUG: MAC queue confirmed clear after {flush_round} flush round(s)")
                                            break
                                        print(f"DEBUG: MAC flush round {flush_round}/{MAC_FLUSH_MAX_ROUNDS} "
                                              f"did not confirm a clean drain")
                                        if flush_round < MAC_FLUSH_MAX_ROUNDS:
                                            time.sleep(0.3)
                                print(f"DEBUG: Will retry transmission (attempt {attempt + 2}/{max_retries + 1})")
                                time.sleep(1)
                                continue
                            else:
                                print(f"ERROR: All {max_retries + 1} transmission attempts failed")
                                return False

                    except Exception as e:
                        print(f"Error in transmission attempt {attempt + 1}: {e}")
                        import traceback
                        traceback.print_exc()
                        if attempt < max_retries:
                            print(f"DEBUG: Will retry transmission after exception (attempt {attempt + 2}/{max_retries + 1})")
                            time.sleep(1)
                            continue
                        else:
                            print(f"ERROR: All {max_retries + 1} transmission attempts failed due to exceptions")
                            return False
                return False
            finally:
                fcntl.lockf(self._lock_fd, fcntl.LOCK_UN)
        finally:
            self.transmit_lock.release()

    def _lockf_until(self, until_monotonic: float, poll_s: float = 0.1) -> bool:
        """Take the inter-process UART lock, waiting until until_monotonic at most."""
        while True:
            try:
                fcntl.lockf(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.monotonic() >= until_monotonic:
                    return False
                time.sleep(poll_s)

    def queue_transmit(self, data: Dict[str, Any]) -> bool:
        """Queue sensor data for transmission (thread-safe)"""
        try:
            packet = self.compressed_encoding(data)
            self.transmit_queue.put(('sensor', packet))
            return True
        except Exception as e:
            print(f"Error queuing transmission: {e}")
            return False
    
    def queue_file_transmit(self, file_data: bytes) -> bool:
        """Queue file data for transmission (thread-safe)"""
        try:
            self.transmit_queue.put(('file', file_data))
            return True
        except Exception as e:
            print(f"Error queuing file transmission: {e}")
            return False
    
    @staticmethod
    def _unwrap_text_hex(payload: str) -> str:
        """'35303031' -> '5001' when the bytes are themselves hex-digit text; else unchanged."""
        clean = (payload or "").strip()
        hexdigits = '0123456789abcdefABCDEF'
        if not clean or len(clean) % 4 or any(c not in hexdigits for c in clean):
            return payload
        text = bytes.fromhex(clean).decode('ascii', errors='replace')
        if all(c in hexdigits for c in text):
            print(f"ℹ️ Command arrived as hex text ({clean}); reading it as {text}")
            return text
        return payload

    @staticmethod
    def _parse_server_frame(payload: str):
        """The bytes of a server command frame ([seq][code][value]...), or None if not hex."""
        clean = payload.strip()
        if len(clean) < 6 or len(clean) % 2 or any(c not in '0123456789abcdefABCDEF' for c in clean):
            return None
        return bytes.fromhex(clean)

    def _apply_server_frame(self, frame: bytes) -> None:
        """Apply a server command frame once and ack it with the status reply.

        The server resends until it has the ack ({"ak":seq,"ar":result}); a
        frame applied before is only acked again (tools/command_log.py).
        """
        try:
            from tools.transmit_ip import apply_command_frame
        except ImportError:
            from transmit_ip import apply_command_frame
        r = apply_command_frame(frame, self._apply_parts)
        print(f"📥 Server command seq {r['seq']}: applied {r['applied']} skipped {r['skipped']}"
              f"{' (already applied)' if r['duplicate'] else ''}; ack result {r['result']}")
        self._reply_with_status(ack=(r['seq'], r['result']))

    def _apply_parts(self, parts) -> Dict[str, Any]:
        """Apply a frame's blocks: the handler for actions, the IP decoder for parameters."""
        applied, skipped, rest = [], [], []
        for part in parts:
            code, value = part['code'], part['payload_hex']
            if code in ('18 98', '18 99', '18 9A'):
                self._start_remote_debug(value, code)
                applied.append(f"remote_debug={value}")
            elif code == '21 91':
                (applied if self._start_emergency(value) else skipped).append(code)
            elif code == '99 99':
                self.update_config('emergency_mode', False)
                print('✅ Emergency mode deactivated')
                applied.append('emergency_mode=False')
            else:
                rest.append(part)
        if rest:
            try:
                from tools.transmit_ip import apply_downlink_command
            except ImportError:
                from transmit_ip import apply_downlink_command
            result = apply_downlink_command({'parts': rest}, set_param_fn=self.update_config)
            applied += result['applied']
            skipped += result['skipped']
        return {'applied': applied, 'skipped': skipped}

    def _start_emergency(self, value: str) -> bool:
        """Emergency mode on for HH hours (21 91 HH; 00 keeps emergency_max_hours).

        The duration starts now, even if emergency mode was already on. The
        frame's ack ({"ak":seq,...,"em":1}) tells the server it arrived.
        """
        try:
            hours = int(str(value).strip() or "0", 16)
        except ValueError:
            print(f"⚠️ Emergency command with a bad duration {value!r}: ignored")
            return False
        if hours:
            self.update_config('emergency_max_hours', min(hours, 168))
        self.update_config('emergency_mode', True)
        self.update_config('emergency_since', time.time())
        print(f"🚨 Emergency mode on for {hours or self.get_config('emergency_max_hours', 24)} h")
        return True

    def _emergency_mode(self) -> bool:
        """Emergency mode as the node acts on it: runtime_config.json.

        This handler's lora_config.json keeps its own copy, which only LoRa
        commands update, so it stays True after emergency mode ends some other
        way (the time limit in call_shutdown, the IP downlink, the dashboard).
        Reporting that copy told the server emergency mode was still on.
        """
        try:
            from tools.lora_runtime_integration import get_parameter
            return bool(get_parameter('emergency_mode', False))
        except Exception:
            return bool(self.get_config('emergency_mode', False))

    def _reply_with_status(self, ack=None) -> None:
        """Send the node status JSON from a helper thread; ack = (seq, result) leads it."""
        try:
            from tools.lora_debug_integration import handle_debug_status_request
            reply = handle_debug_status_request(size_limit=self.current_size_limit,
                                                emergency_mode=self._emergency_mode(), ack=ack)
            if reply['status'] == 'success':
                threading.Thread(target=self._send_debug_reply, args=(reply['data'],),
                                 name='status-reply', daemon=True).start()
        except Exception as e:
            print(f"⚠️ Status reply failed: {e}")

    def _start_remote_debug(self, value: str, code: str = '18 98') -> None:
        """Start, extend or end a remote debug session (docs/REMOTE_DEBUG_SESSION.md).

        On a thread: bringing cellular and Tailscale up takes up to ~3 min, and
        decode() runs in the serial listener. The session's status uplink goes
        through this handler's transmit(), which waits for the lock.
        """
        def run():
            try:
                try:
                    from tools import remote_debug
                except ImportError:
                    import remote_debug
                minutes = remote_debug.minutes_from_code(value)
                if minutes:
                    remote_debug.start(minutes, source='lora', handler=self,
                                       connection=remote_debug.CODE_CONNECTION.get(code, 'cellular'))
                else:
                    remote_debug.stop(handler=self, reason='LoRa command')
            except Exception as e:
                print(f"⚠️ Remote debug command failed: {e}")
        print(f'🛠️ Remote debug command, value {value!r}')
        threading.Thread(target=run, name='remote-debug', daemon=True).start()

    def _send_debug_reply(self, hex_payload: str) -> None:
        """Transmit a debug-status reply (runs on its own thread; see decode())."""
        try:
            if self.transmit(hex_payload):
                print('✅ Debug status reply sent')
            else:
                print('❌ Debug status reply failed to send')
        except Exception as e:
            print(f"⚠️ Debug status reply failed: {e}")

    def queue_binary_transmit(self, binary_data) -> bool:
        """Queue arbitrary binary data for transmission (thread-safe)"""
        try:
            # If it's a string, assume it's hex data and send directly
            if isinstance(binary_data, str):
                print(f"DEBUG: Detected hex string, queuing as hex data: {binary_data[:50]}...")
                self.transmit_queue.put(('hex_string', binary_data))
            else:
                # For bytes/bytearray, queue as binary
                print(f"DEBUG: Detected binary data, queuing as binary: {len(binary_data)} bytes")
                self.transmit_queue.put(('binary', binary_data))
            return True
        except Exception as e:
            print(f"Error queuing binary transmission: {e}")
            return False
    
    def queue_auto(self, data) -> bool:
        """Automatically detect data type and queue appropriately"""
        try:
            if isinstance(data, dict):
                print("DEBUG: Auto-detected dictionary, using queue_transmit")
                return self.queue_transmit(data)
            elif isinstance(data, (bytes, bytearray)):
                print("DEBUG: Auto-detected bytes, using queue_binary_transmit")
                return self.queue_binary_transmit(data)
            elif isinstance(data, str):
                print("DEBUG: Auto-detected string, assuming hex data and using queue_binary_transmit")
                return self.queue_binary_transmit(data)  # Will be handled as hex_string
            else:
                print(f"DEBUG: Auto-detected {type(data)}, converting to string then using queue_binary_transmit")
                return self.queue_binary_transmit(str(data))  # Will be handled as hex_string
        except Exception as e:
            print(f"Error in auto-queue: {e}")
            return False
    
    def process_transmit_queue(self):
        """Process queued transmissions"""
        while not self.transmit_queue.empty():
            try:
                item = self.transmit_queue.get_nowait()
                print(f"DEBUG: Processing queue item: {item}, type: {type(item)}")
                
                if isinstance(item, tuple) and len(item) == 2:
                    data_type, packet = item
                    print(f"DEBUG: Processing {data_type} packet, type: {type(packet)}")
                    
                    if data_type == 'hex_string':
                        # Hex string data - send directly without conversion
                        print(f"DEBUG: Sending hex string: {len(packet)} chars")
                        self.transmit(packet)
                    else:
                        # For other data types, ensure packet is bytes
                        if isinstance(packet, str):
                            print(f"WARNING: Converting string packet to bytes: {packet[:50]}...")
                            packet = packet.encode('utf-8')
                        elif not isinstance(packet, (bytes, bytearray)):
                            print(f"WARNING: Converting {type(packet)} packet to bytes: {packet}")
                            packet = str(packet).encode('utf-8')
                        
                        if data_type == 'sensor':
                            # Sensor data from compressed_encoding() - already bytes
                            print(f"DEBUG: Sending sensor data: {len(packet)} bytes")
                            self.transmit(packet)
                        elif data_type == 'file':
                            # File data - already bytes
                            print(f"DEBUG: Sending file data: {len(packet)} bytes")
                            self.transmit(packet)
                        elif data_type == 'binary':
                            # Arbitrary binary data - already bytes
                            print(f"DEBUG: Sending binary data: {len(packet)} bytes")
                            self.transmit(packet)
                        else:
                            print(f"Unknown data type: {data_type}")
                            continue
                else:
                    # Handle legacy format (backward compatibility)
                    packet = item
                    print(f"DEBUG: Processing legacy packet, type: {type(packet)}")
                    
                    try:
                        # Check if this looks like a hex string
                        if isinstance(packet, str) and all(c in '0123456789abcdefABCDEF' for c in packet):
                            print(f"DEBUG: Legacy packet appears to be hex string, treating as hex")
                            # Treat as hex string instead of trying UTF-8 conversion
                            self.transmit(packet)
                        else:
                            # Ensure packet is bytes for other types
                            if isinstance(packet, str):
                                print(f"WARNING: Converting legacy string packet to bytes: {packet[:50]}...")
                                packet = packet.encode('utf-8')
                            elif not isinstance(packet, (bytes, bytearray)):
                                print(f"WARNING: Converting legacy {type(packet)} packet to bytes: {packet}")
                                packet = str(packet).encode('utf-8')
                            
                            print(f"DEBUG: Legacy packet converted to bytes: {type(packet)}, length: {len(packet)}")
                            # Use transmit method which will verify OK response
                            result = self.transmit(packet)
                            if not result:
                                print("WARNING: Legacy packet transmission failed - no OK response")
                    except Exception as e:
                        print(f"ERROR: Failed to process legacy packet: {e}")
                        print(f"  Packet: {packet}")
                        print(f"  Packet type: {type(packet)}")
                        import traceback
                        traceback.print_exc()
                        continue
                    
            except queue.Empty:
                break
            except Exception as e:
                print(f"Error processing transmit queue: {e}")
                print(f"Item that caused error: {item}")
                print(f"Item type: {type(item)}")
                print(f"Error location: {e.__traceback__.tb_lineno if hasattr(e, '__traceback__') else 'unknown'}")
                import traceback
                traceback.print_exc()

    def get_queue_depth(self) -> int:
        """Number of items currently queued for transmission."""
        return self.transmit_queue.qsize()

    def compressed_encoding(self, data: Dict[str, Any]) -> bytes:
        """Handle encoding and compression of data from sensors for transmission over LoRa"""
        return _encode_compressed_packet(data)

    def decode(self, payload: str):
        """Decode incoming payload and update configuration"""
        try:
            print(f"DEBUG: Decoding payload: '{payload}'")
            print(f"DEBUG: Payload type: {type(payload)}")
            print(f"DEBUG: Payload length: {len(payload)}")
            print(f"DEBUG: Payload hex: {payload.encode().hex()}")
            print(f"DEBUG: Payload repr: {repr(payload)}")
            
            from datetime import datetime
            print(f"📡 LoRa command received at {datetime.now().strftime('%H:%M:%S')}")
            
            # A command typed as text in ChirpStack's enqueue form ("5001" as ASCII)
            # arrives hex-encoded twice ("35303031"). No real command consists only
            # of ASCII hex-digit bytes (the codes are 10-21, 50, 99), so decode it once
            # more instead of reading channel 35, command 30.
            payload = self._unwrap_text_hex(payload)

            # A bare 21 is the remote start byte '!', which the mDot handles itself
            # and never forwards; emergency mode is 21 91 HH (_start_emergency).
            if payload == '21':
                print('ℹ️ Remote start byte received; emergency mode needs 21 91 HH')
                return

            # Command frames the server encodes (API app/commands.py): [seq][code 2B][value]...,
            # the same frames the IP downlink carries. 50 01 asks for the status reply.
            frame = self._parse_server_frame(payload)
            if frame:
                self._apply_server_frame(frame)
                return

            print(f"ℹ️ Unrecognised downlink ignored: '{payload}'")

        except Exception as e:
            print(f"Error decoding payload '{payload}': {e}")
            import traceback
            traceback.print_exc()
    
    def check_mdot_status(self):
        """Check mDot status and mode"""
        try:
            print("DEBUG: Checking mDot status...")
            
            # Clear buffers first
            self._drain_input()
            self.ser.reset_output_buffer()
            
            # Send newline to clear any partial commands first
            self.ser.write('\r\n'.encode())
            time.sleep(0.1)
            
            # Check if mDot responds to basic AT command
            print("DEBUG: Sending AT command...")
            self.ser.write('AT\r\n'.encode())
            time.sleep(1)
            
            responses = []
            ver_success = False
            while self.ser.in_waiting > 0:
                res = self._read_line()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: AT response: '{response}'")
                        if 'OK' in response:
                            ver_success = True
                            print("DEBUG: AT command successful")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary AT response (hex): {response_hex}")
                        if b'OK' in res:
                            ver_success = True
                            print("DEBUG: AT command successful (binary)")
           
           
            # Check join status
            print("DEBUG: Sending AT+NJS? command...")
            self.ser.flush()
            # Send newline to clear any partial commands
            self.ser.write('\r\n'.encode())
            time.sleep(0.5)
            self.ser.write('AT+NJS?\r\n'.encode())
            time.sleep(0.5)
            
            responses = []
            njs_success = False
            while self.ser.in_waiting > 0:
                res = self._read_line()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: Join status response: '{response}'")
                        if 'OK' in response:
                            njs_success = True
                            print("DEBUG: AT+NJS? command successful")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary join status response (hex): {response_hex}")
                        if b'OK' in res:
                            njs_success = True
                            print("DEBUG: AT+NJS? command successful (binary)")
            
            if not njs_success:
                print("WARNING: AT+NJS? command did not receive OK response")
            
            return ver_success and njs_success
        except Exception as e:
            print(f"DEBUG: Error checking mDot status: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def check_reception_status(self):
        """Check if the LoRa handler is properly receiving data"""
        try:
            print("DEBUG: Checking LoRa reception status...")
            
            # Check if listener thread is running
            if not self.listening:
                print("WARNING: LoRa listener is not running")
                return False
            
            if not self.listener_thread or not self.listener_thread.is_alive():
                print("WARNING: LoRa listener thread is not alive")
                return False
            
            # Check serial port status
            if not self.ser.is_open:
                print("WARNING: Serial port is not open")
                return False
            
            # Check if there's data waiting
            if self.ser.in_waiting > 0:
                print(f"DEBUG: {self.ser.in_waiting} bytes waiting in serial buffer")
                
                # Read and display waiting data
                while self.ser.in_waiting > 0:
                    res = self._read_line()
                    if res:
                        try:
                            response = res.decode('utf-8').strip()
                            print(f"DEBUG: Waiting data: '{response}'")
                        except UnicodeDecodeError:
                            response_hex = res.hex()
                            print(f"DEBUG: Waiting binary data (hex): {response_hex}")
            else:
                print("DEBUG: No data waiting in serial buffer")
            
            print("DEBUG: LoRa reception status check completed")
            return True
            
        except Exception as e:
            print(f"DEBUG: Error checking reception status: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def check_mdot_data_config(self):
        """Check mDot data reception configuration"""
        try:
            print("DEBUG: Checking mDot data reception configuration...")
            
            # Clear buffers first
            self._drain_input()
            self.ser.reset_output_buffer()
            
            # Send newline to clear any partial commands
            self.ser.write('\r\n'.encode())
            time.sleep(0.1)
            
            # Check current data format configuration
            print("DEBUG: Checking data format configuration...")
            self.ser.write('AT+DFORMAT?\r\n'.encode())
            time.sleep(1)
            
            responses = []
            while self.ser.in_waiting > 0:
                res = self._read_line()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        responses.append(response)
                        print(f"DEBUG: DFORMAT response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary DFORMAT response (hex): {response_hex}")
            
            # Check if there are any pending messages
            print("DEBUG: Checking for pending messages...")
            self.ser.write('AT+MSG?\r\n'.encode())
            time.sleep(1)
            
            while self.ser.in_waiting > 0:
                res = self._read_line()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        print(f"DEBUG: MSG response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        print(f"DEBUG: Binary MSG response (hex): {response_hex}")
            
            print("DEBUG: mDot data configuration check completed")
            return True
            
        except Exception as e:
            print(f"DEBUG: Error checking mDot data config: {e}")
            import traceback
            traceback.print_exc()
            return False
    
   
    def close(self):
        """Clean up resources"""
        self.stop_listening()
        if self.ser.is_open:
            self.ser.close()
        try:
            self._lock_fd.close()
        except Exception:
            pass
        print("LoRa handler closed")
    
    def _is_at_response(self, message: str) -> bool:
        """Check if a message is an AT command response that should be ignored"""
        # Check for transmission-related responses that need to be processed
        if self._is_transmission_response(message):
            return False
        
        # Check for TXS responses that need to be processed
        if self._is_txs_response(message):
            return False
        
        # Common AT command response patterns that should be ignored
        at_patterns = [
            '+NJS:',                 # Network join status
            '+DFORMAT:',             # Data format response
            '+MSG:',                 # Message status response
            'AT+',                   # AT command echo
            'AT',                    # AT command echo
        ]
        
        message_upper = message.upper()
        for pattern in at_patterns:
            if pattern in message_upper:
                return True
        
        # Check for numeric responses to AT commands (like size limits)
        # Only consider very short numeric responses as AT responses
        if message.isdigit() and len(message) <= 2:
            return True
            
        # Check for extended AT responses that start with + but are not LoRa data
        # This is a fallback for any other + patterns we haven't explicitly handled
        if message.startswith('+'):
            return True
        
        # If none of the above patterns match, classify as AT response (fallback)
        # This ensures all unclassified messages are treated as AT responses
        return True
    
    # The first line the mDot firmware prints when it handles the emergency
    # downlink '!' (CmdClassCPacketProcessor::handleEmergencyPacket). Its later
    # "EMERGENCY: ..." lines are progress messages and must not re-trigger.
    _EMERGENCY_LINE_RE = re.compile(r'^EMERGENCY: PA_6 pin state: [01]$')

    def _is_emergency_message(self, message: str) -> bool:
        """True only for the firmware's emergency line, matched exactly."""
        return bool(self._EMERGENCY_LINE_RE.match(message.strip()))
    
    def _extract_emergency_status(self, message: str) -> dict:
        """Extract emergency status information from an EMERGENCY message"""
        try:
            # Only return emergency status for messages containing "EMERGENCY"
            if self._is_emergency_message(message):
                return {
                    'emergency_triggered': True,  # Always True for any EMERGENCY message
                    'message': message,
                    'timestamp': time.time()
                }
            else:
                return None
        except Exception as e:
            print(f"Error extracting emergency status from '{message}': {e}")
            return None
    
    def _is_txs_response(self, message: str) -> bool:
        """Check if a message is a +TXS: response containing size limit information"""
        # Check for the actual mDot response format: "AT+TXS" (echo) or "242" (size limit)
        return ("AT+TXS" in message or 
                (message.isdigit() and len(message) <= 3))  # Size limits are typically 1-3 digits
    
    def _is_valid_hex_string(self, hex_string: str) -> bool:
        """Validate that a string contains only valid hexadecimal characters"""
        if not isinstance(hex_string, str):
            return False
        
        # Check if string is empty or has odd length
        if not hex_string or len(hex_string) % 2 != 0:
            return False
        
        # Check if all characters are valid hex digits
        hex_digits = set('0123456789abcdefABCDEF')
        return all(c in hex_digits for c in hex_string)
    
    def _clear_mdot_input(self):
        """Clear mDot input buffer and send blank AT command to reset state"""
        try:
            print("DEBUG: Clearing mDot input buffer...")
            self._drain_input()
            self.ser.reset_output_buffer()
            
            # Send blank AT command to clear any partial state
            print("DEBUG: Sending blank AT command to clear mDot state...")
            self.ser.write('AT\r\n'.encode())
            time.sleep(0.5)
            
            # Read any response from the blank AT command
            while self.ser.in_waiting > 0:
                res = self._read_line()
                if res:
                    try:
                        response = res.decode('utf-8').strip()
                        print(f"DEBUG: Blank AT response: '{response}'")
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        print(f"DEBUG: Blank AT binary response: {response_hex}")
            
            print("DEBUG: mDot input cleared successfully")
            return True
        except Exception as e:
            print(f"ERROR: Failed to clear mDot input: {e}")
            return False

    def _flush_mac_commands(self) -> bool:
        """Send a 1-byte probe uplink to drain the LoRaWAN MAC command queue.

        Called when AT+SENDB returns 'Tx buffer filled by MAC Commands'.
        An AT command (AT, AT+TXS) does not trigger RF and cannot carry MAC
        ACKs to the network server.  A real uplink — even a 1-byte payload —
        piggybacks the pending MAC responses and clears the mDot TX buffer so
        the next AT+SENDB for the actual payload can proceed.
        """
        try:
            print("DEBUG: MAC flush: sending 1-byte probe to drain MAC command queue...")
            self._drain_input()
            self.ser.write(b'AT+SENDB=00\r\n')
            deadline = time.time() + MAC_FLUSH_WINDOW_S
            while time.time() < deadline:
                if self.ser.in_waiting > 0:
                    res = self._read_line()
                    if not res:
                        continue
                    try:
                        response = res.decode('utf-8').strip()
                    except UnicodeDecodeError:
                        response = ""
                    if response:
                        print(f"DEBUG: MAC flush response: '{response}'")
                    if 'OK' in response:
                        print("DEBUG: MAC flush OK — MAC queue drained")
                        return True
                    if 'ERROR' in response.upper():
                        print("DEBUG: MAC flush ERROR — continuing anyway")
                        return False
                else:
                    time.sleep(0.1)
            print("DEBUG: MAC flush timed out")
            return False
        except Exception as e:
            print(f"DEBUG: MAC flush failed: {e}")
            return False

    def _attempt_transmission(self, content: bytes, size_limit: int) -> tuple[bool, str]:
        """Attempt a single transmission and return (success, error_message)"""

        def _read_mdot_response(write_deadline_s: float) -> tuple[bool, str, list]:
            """
            Poll the serial port until we get OK/ERROR or the deadline expires.
            Returns (success, error_message, responses).

            Command echoes (lines starting with 'AT') arrive within milliseconds
            of the write, but the mDot's actual OK/ERROR for AT+SENDB only arrives
            after the over-air TX completes (typically 3–10 s).  The early-exit
            idle timer must NOT be started on echo lines — only on real responses.
            """
            deadline = time.time() + write_deadline_s
            final_responses = []
            success = False
            error_message = ""
            got_real_response = False  # True once we see a non-echo line
            idle_since = None

            while time.time() < deadline:
                if self.ser.in_waiting > 0:
                    idle_since = None
                    try:
                        res = self._read_line()
                    except Exception as exc:
                        print(f"DEBUG: Serial read error: {exc}")
                        time.sleep(0.1)
                        continue
                    if not res:
                        continue
                    try:
                        response = res.decode('utf-8').strip()
                    except UnicodeDecodeError:
                        response_hex = res.hex()
                        final_responses.append(f"BINARY:{response_hex}")
                        print(f"DEBUG: Binary response (hex): {response_hex}")
                        if b'OK' in res:
                            success = True
                            got_real_response = True
                            print("DEBUG: mDot confirmed OK (binary)")
                        elif b'ERROR' in res or b'INVALID' in res:
                            error_message = f"mDot reported error in binary response: {response_hex}"
                            got_real_response = True
                        continue

                    if not response:
                        continue

                    # Command echoes (the mDot echoing our AT command back) arrive
                    # quickly but are not terminal responses.  Don't start the
                    # idle timer on them or we exit before the real OK arrives.
                    is_cmd_echo = response.upper().startswith('AT')
                    final_responses.append(response)

                    if is_cmd_echo:
                        print(f"DEBUG: mDot echo: '{response[:80]}'")
                    else:
                        print(f"DEBUG: mDot response: '{response}'")
                        got_real_response = True

                        if 'OK' in response:
                            success = True
                            print("DEBUG: mDot confirmed command execution with OK")
                        elif 'INVALID COMMAND' in response.upper():
                            error_message = f"mDot reported invalid command: {response}"
                            print(f"ERROR: {error_message}")
                        elif 'INVALID HEX' in response.upper() or 'INVALID STRING' in response.upper():
                            error_message = f"mDot reported invalid hex string: {response}"
                            print(f"ERROR: {error_message}")
                        elif 'ERROR' in response.upper():
                            error_message = f"mDot reported error: {response}"
                            print(f"ERROR: {error_message}")

                        # Stop as soon as we have a definitive terminal response
                        if success or error_message:
                            break
                else:
                    # Only start the idle timer after seeing a real (non-echo) response.
                    # This avoids premature exit while the mDot is doing its air TX.
                    if got_real_response:
                        if idle_since is None:
                            idle_since = time.time()
                        elif time.time() - idle_since > 0.5:
                            break  # 0.5 s of silence after real response → done
                    time.sleep(0.1)

            return success, error_message, final_responses

        try:
            if isinstance(content, str):
                if not self._is_valid_hex_string(content):
                    return False, f"Invalid hex string format: {content}"

                payload_size = len(content) // 2
                print(f"DEBUG: Hex string length: {len(content)} chars, payload size: {payload_size} bytes")

                if payload_size > size_limit:
                    return False, f"Payload size {payload_size} bytes exceeds limit {size_limit} bytes"

                # Discard any stale RX bytes (e.g. leftover echoes from earlier commands)
                # before sending so they don't pollute our response window.
                self._drain_input()

                at_command = f"AT+SENDB={content}\r\n".encode()
                print(f"DEBUG: Sending AT command: {at_command}")
                self.ser.write(at_command)
                print("DEBUG: AT command sent, polling for response (up to 15 s)...")

                success, error_message, final_responses = _read_mdot_response(SENDB_WINDOW_HEX_S)

            else:
                payload_size = len(content)
                print(f"DEBUG: Bytes length: {payload_size} bytes")

                if payload_size > size_limit:
                    return False, f"Payload size {payload_size} bytes exceeds limit {size_limit} bytes"

                hex_content = content.hex()
                if not self._is_valid_hex_string(hex_content):
                    return False, f"Generated invalid hex string from bytes: {hex_content}"

                # Discard stale RX bytes before sending
                self._drain_input()

                print(f"DEBUG: Sending data payload...")
                self.ser.write(f'AT+SENDB={hex_content}\r\n'.encode())
                print("DEBUG: Data payload sent, polling for response (up to 20 s)...")

                success, error_message, final_responses = _read_mdot_response(SENDB_WINDOW_BYTES_S)

            if success:
                print("Data sent to mDot successfully!")
                return True, ""
            else:
                # Promote the specific MAC buffer message so transmit() can detect it
                # and call _flush_mac_commands() before retrying.
                if not error_message or error_message == "mDot reported error: ERROR":
                    for resp in final_responses:
                        if 'Tx buffer filled' in resp or 'MAC Commands' in resp:
                            error_message = f"mDot: Tx buffer filled by MAC Commands — {resp}"
                            break
                if not error_message:
                    error_message = f"mDot did not respond with OK - responses: {final_responses}"
                print(f"ERROR: {error_message}")
                return False, error_message

        except Exception as e:
            error_message = f"Exception during transmission: {e}"
            print(f"Error sending to mDot: {e}")
            import traceback
            traceback.print_exc()
            return False, error_message
    
    def _is_transmission_response(self, message: str) -> bool:
        """Check if a message is a transmission-related AT response that needs processing"""
        # Look for transmission-related response patterns
        transmission_patterns = [
            'SENDB:',                # AT+SENDB response
            'SEND:',                 # AT+SEND response
            'TX:',                   # Transmission status
            'TRANSMIT:',             # Transmission status
            'PACKET:',               # Packet information
            'PAYLOAD:',              # Payload information
        ]
        
        message_upper = message.upper()
        for pattern in transmission_patterns:
            if pattern in message_upper:
                return True
        
        # Check for transmission confirmation messages
        if any(keyword in message_upper for keyword in ['TRANSMITTED', 'SENT', 'DELIVERED', 'ACK']):
            return True
        
        return False
    
    def _extract_txs_size_limit(self, message: str) -> int:
        """Extract size limit from +TXS: response"""
        try:
            # Handle the actual mDot response format: "AT+TXS" (echo) or "242" (size limit)
            
            # If it's the command echo, no size limit here
            if "AT+TXS" in message:
                return None
            
            # If it's a numeric message, it might be the size limit
            if message.isdigit() and len(message) <= 3:
                size_limit = int(message)
                # LoRaWAN minimum payload is 11 B (SF12/BW125). Reject anything smaller
                # to avoid false matches on short numeric messages like single-byte
                # downlink payloads ('02', '1', etc.) poisoning current_size_limit.
                if 11 <= size_limit <= 255:
                    return size_limit
            
            # Legacy support for +TXS: format (in case it's used elsewhere)
            if "+TXS:" in message.upper():
                # Extract the numeric value after +TXS:
                size_part = message.split(":", 1)[1].strip()
                # Remove any non-numeric characters and convert to int
                size_str = ''.join(filter(str.isdigit, size_part))
                if size_str:
                    return int(size_str)
            
            return None
        except Exception as e:
            print(f"Error extracting TXS size limit from '{message}': {e}")
            return None
    
    def _extract_transmission_status(self, message: str) -> dict:
        """Extract transmission status information from AT response"""
        try:
            status = {
                'success': False,
                'message': message,
                'details': {},
                'timestamp': time.time()
            }
            
            message_upper = message.upper()
            
            # Check for success indicators
            if 'OK' in message_upper:
                status['success'] = True
                status['details']['confirmation'] = 'OK'
                status['details']['type'] = 'success_response'
            
            # Check for error indicators
            if 'ERROR' in message_upper:
                status['success'] = False
                status['details']['error'] = 'ERROR'
                status['details']['type'] = 'error_response'
            
            # Extract transmission-specific information
            if 'SENDB:' in message_upper:
                # Extract data after SENDB:
                data_part = message.split("SENDB:", 1)[1].strip()
                status['details']['command'] = 'SENDB'
                status['details']['data'] = data_part
                status['details']['type'] = 'command_response'
            
            if 'SEND:' in message_upper:
                # Extract data after SEND:
                data_part = message.split("SEND:", 1)[1].strip()
                status['details']['command'] = 'SEND'
                status['details']['data'] = data_part
                status['details']['type'] = 'command_response'
            
            # Check for transmission confirmation keywords
            for keyword in ['TRANSMITTED', 'SENT', 'DELIVERED', 'ACK']:
                if keyword in message_upper:
                    status['success'] = True
                    status['details']['confirmation'] = keyword
                    status['details']['type'] = 'status_response'
            
            # If no specific type was identified, classify based on content
            if 'type' not in status['details']:
                if 'OK' in message_upper or 'ERROR' in message_upper:
                    status['details']['type'] = 'standard_response'
                else:
                    status['details']['type'] = 'unknown_response'
            
            return status
            
        except Exception as e:
            print(f"Error extracting transmission status from '{message}': {e}")
            return {
                'success': False,
                'message': message,
                'error': str(e),
                'timestamp': time.time()
            }
    
    def get_size_limit(self) -> int:
        """Get the current payload size limit"""
        return self.current_size_limit

    def size_limit_now(self) -> int:
        """Ask the mDot (AT+TXS) for its payload limit at the current data rate.

        The stored limit lags: after a rejoin the mDot is back at DR0 (11 bytes)
        until ADR raises it. Under both locks, like link_check(), so the reply
        can't be mistaken for a send's.
        """
        try:
            with self.transmit_lock:
                fcntl.lockf(self._lock_fd, fcntl.LOCK_EX)
                try:
                    self._query_txs_locked()
                    if self.current_size_limit < COMPACT_PACKET_SIZE:
                        # MAC commands waiting for an uplink take the room: send
                        # them in one probe (as transmit() does on "Tx buffer
                        # filled by MAC Commands"), then ask again
                        print(f"📏 {self.current_size_limit} B free: flushing pending MAC commands")
                        self._flush_mac_commands()
                        self._query_txs_locked()
                finally:
                    fcntl.lockf(self._lock_fd, fcntl.LOCK_UN)
        except Exception as e:
            print(f"⚠️ AT+TXS failed, keeping {self.current_size_limit} B: {e}")
        return self.current_size_limit
    
    def get_last_transmission_status(self) -> dict:
        """Get the last transmission status"""
        return self.last_transmission_status
    
    def get_transmission_history(self) -> list:
        """Get the transmission history"""
        return self.transmission_history.copy()
    

    
    def _query_txs_locked(self) -> None:
        """Send AT+TXS and update current_size_limit.

        Caller must already hold both transmit_lock and the fcntl inter-process
        lock.  Use refresh_size_limit() from outside the transmit path instead.
        """
        self._drain_input()
        self.ser.write('AT+TXS\r\n'.encode())
        time.sleep(1)
        while self.ser.in_waiting > 0:
            res = self._read_line()
            if res:
                try:
                    response = res.decode('utf-8').strip()
                    # The answer to our own AT+TXS (input flushed first), so a small
                    # number is real: pending MAC commands take room from the payload,
                    # leaving under 11 B at DR0. The listener's parser rejects those.
                    if response.isdigit() and len(response) <= 3:
                        size_limit = int(response)
                    else:
                        size_limit = self._extract_txs_size_limit(response)
                    if size_limit is not None:
                        self.current_size_limit = size_limit
                        print(f"DEBUG: Size limit refreshed to: {size_limit} bytes")
                except UnicodeDecodeError:
                    pass

    def refresh_size_limit(self) -> bool:
        """Manually refresh the size limit by sending AT+TXS command"""
        try:
            print("DEBUG: Manually refreshing size limit...")
            # Temporarily disable listening to avoid interference
            was_listening = self.listening
            if was_listening:
                self.stop_listening()
                time.sleep(0.5)
            
            # Acquire transmit_lock first (intra-process) then fcntl.lockf
            # (inter-process).  The listener thread holds transmit_lock when it
            # reads from the serial port, so this prevents it from stealing the
            # AT+TXS response even if stop_listening() timed out.
            with self.transmit_lock:
                fcntl.lockf(self._lock_fd, fcntl.LOCK_EX)
                try:
                    # Clear buffers and send AT+TXS command
                    self._drain_input()
                    self.ser.write('AT+TXS\r\n'.encode())
                    time.sleep(1)

                    # Read response directly to get immediate size limit.
                    # _extract_txs_size_limit() handles both "+TXS: 242" and bare "242"
                    # responses — run it on every line so we don't miss the numeric reply.
                    responses = []
                    while self.ser.in_waiting > 0:
                        res = self._read_line()
                        if res:
                            try:
                                response = res.decode('utf-8').strip()
                                responses.append(response)
                                print(f"DEBUG: Manual TXS response: '{response}'")

                                size_limit = self._extract_txs_size_limit(response)
                                if size_limit is not None:
                                    self.current_size_limit = size_limit
                                    print(f"DEBUG: Size limit refreshed to: {size_limit} bytes")
                            except UnicodeDecodeError:
                                print(f"DEBUG: Binary manual TXS response (hex): {res.hex()}")
                finally:
                    fcntl.lockf(self._lock_fd, fcntl.LOCK_UN)
            
            # Re-enable listening if it was active
            if was_listening:
                self.start_listening()
            
            return True
        except Exception as e:
            print(f"Error refreshing size limit: {e}")
            # Re-enable listening on error
            if was_listening:
                self.start_listening()
            return False

    def _is_valid_message(self, message: str) -> bool:
        """Check if a message is valid and complete before processing"""
        try:
            # Skip empty or whitespace-only messages
            if not message or message.isspace():
                return False
            
            # Skip messages that are too short (likely incomplete)
            if len(message) < 2:
                return False
            
            # Skip messages that are too long (likely corrupted)
            # Exception: Allow longer messages for debug status (AT+SENDB=...)
            if len(message) > 100 and not message.startswith('AT+SENDB='):
                return False
            
            # Skip messages with excessive special characters (likely corrupted)
            special_char_count = sum(1 for c in message if not c.isalnum() and not c.isspace() and c not in '=:+-_.')
            if special_char_count > len(message) * 0.3:  # More than 30% special chars
                return False
            
            # Skip messages that look like corrupted data (based on actual logs)
            corrupted_patterns = [
                'irgncmatitw', 'ereca', 'megny_cki', 'gaeay', 
                'nput pin hih nton takn', 'sent t gaeay', 'megny_cki=u=0',
                'yo', 'irgncmatitw:ereca:n0,ot'
            ]
            message_lower = message.lower()
            if any(pattern in message_lower for pattern in corrupted_patterns):
                return False
            
            # Skip messages that are mostly non-printable characters
            printable_ratio = sum(1 for c in message if c.isprintable()) / len(message)
            if printable_ratio < 0.7:  # Less than 70% printable
                return False
            
            # Skip messages that look like partial/corrupted emergency messages
            if 'emergency:' in message_lower and len(message) < 20:
                return False
            
            return True
            
        except Exception as e:
            print(f"Error validating message '{message}': {e}")
            return False
    
    def _check_serial_connection(self) -> bool:
        """Check if the serial port is still connected and functional"""
        try:
            if not self.ser or not self.ser.is_open:
                return False
            
            # Try to get port status
            self.ser.get_settings()
            return True
        except Exception as e:
            print(f"Serial port connection check failed: {e}")
            return False
    
    def _attempt_reconnection(self) -> bool:
        """Attempt to reconnect to the serial port"""
        try:
            print("🔄 Attempting to reconnect to serial port...")
            
            # Close existing connection if any
            if self.ser and self.ser.is_open:
                self.ser.close()
            
            # Wait a moment before reconnecting
            time.sleep(2)
            
            # Try to reconnect
            self.ser = serial.Serial(
                port=self.port,
                baudrate=115200,
                timeout=1,
                write_timeout=1
            )
            
            if self.ser.is_open:
                print("✅ Serial port reconnection successful")
                return True
            else:
                print("❌ Serial port reconnection failed")
                return False
                
        except Exception as e:
            print(f"Serial port reconnection error: {e}")
            return False

# Global instance for easy access from other modules -- only ever populated
# in the LoRa daemon process (tools/lora_daemon.py) now, via
# create_lora_handler_with_retry(). Every other process gets a
# LoRaHandlerClient from get_lora_handler() instead; see that function.
_lora_handler = None
_lora_handler_lock = threading.Lock()


def create_lora_handler_with_retry() -> Optional[LoRaHandler]:
    """Construct the one real LoRaHandler instance (thread-safe singleton
    within this process), retrying briefly on a serial-port conflict.

    Returns None if another OS process still owns the serial port after
    retrying.  Callers must check for None before dereferencing the result.

    Called exactly once, at startup, by tools/lora_daemon.py -- the single
    process that owns the physical LoRa serial port for its entire lifetime.
    Every other process talks to the daemon over IPC via get_lora_handler()'s
    LoRaHandlerClient instead of calling this directly.

    TickTalk's own runtime spawns multiple separate OS processes to execute
    graph nodes (confirmed via `ps` PPID chains on a real device,
    2026-07-22) -- a per-process singleton like this one cannot provide
    cross-process exclusivity on its own; that's exactly why only the daemon
    calls it now, and every other process goes through the daemon's IPC
    socket instead of constructing its own LoRaHandler. Retrying with a short
    backoff here remains useful defense-in-depth (e.g. a stale leftover
    process still releasing the port during a daemon restart).
    """
    global _lora_handler
    if _lora_handler is not None:
        return _lora_handler
    with _lora_handler_lock:
        # Re-check inside the lock — another thread may have created it while we waited.
        if _lora_handler is not None:
            return _lora_handler

        max_attempts = 3
        retry_delay_s = 1.0
        for attempt in range(1, max_attempts + 1):
            try:
                print(f"🔧 Creating new LoRaHandler instance... (attempt {attempt}/{max_attempts})")
                # Build in a local variable so the global is only written once
                # initialization is fully complete.  Any thread hitting the outer
                # fast-path before this point sees None and will block on the lock,
                # then pick up the fully-ready instance from the inner re-check.
                handler = LoRaHandler()
                # Seed current_size_limit with the actual AT+TXS value BEFORE starting
                # the listener so there is no response-parsing interference.
                # Without this, current_size_limit stays at the 242 B default indefinitely
                # because nothing else sends AT+TXS, and the SF-adaptive bitmap logic
                # silently behaves as SF7 for the entire session.
                try:
                    handler.refresh_size_limit()
                    print(f"🔧 Initial mDot size limit: {handler.current_size_limit} B")
                except Exception as txs_err:
                    print(f"⚠️ Could not refresh initial size limit: {txs_err}; using default {handler.current_size_limit} B")
                handler.start_listening()
                _lora_handler = handler  # publish only after full init
                print("✅ LoRa handler initialized successfully")
                return _lora_handler
            except LoRaSerialPortConflict:
                if attempt < max_attempts:
                    print(
                        f"ℹ️ LoRa serial port busy (attempt {attempt}/{max_attempts}); "
                        f"retrying in {retry_delay_s}s"
                    )
                    time.sleep(retry_delay_s)
                    continue
                _lora_handler = None
                print(
                    f"ℹ️ LoRa serial port still owned by another process after "
                    f"{max_attempts} attempts; skipping handler creation in PID {os.getpid()}"
                )
                return None
            except Exception as e:
                _lora_handler = None  # explicit: global was never written, but make intent clear
                # If LoRaHandler() itself succeeded (flock + serial port both
                # acquired) but a later step (e.g. start_listening()) is what
                # raised, `handler` is bound to a fully-flock-holding object that
                # was never published to _lora_handler and would otherwise leak
                # its FDs -- and keep the real flock held -- for as long as this
                # exception's traceback keeps it referenced.
                try:
                    handler.close()
                except NameError:
                    pass
                except Exception:
                    pass
                print(f"❌ Failed to initialize LoRa handler: {e}")
                print("⚠️ LoRa functionality will not be available")
                raise RuntimeError(f"LoRa handler initialization failed: {e}") from e
    return None  # unreachable: the loop above always returns or raises


# ---------------------------------------------------------------------------
# IPC helpers shared by LoRaHandlerClient and tools/lora_daemon.py
# ---------------------------------------------------------------------------

LORA_DAEMON_SOCKET_PATH = "/run/lora/lora.sock"


def _jsonify_bytes(obj):
    """Recursively wrap bytes/bytearray values so they survive json.dumps.

    queue_transmit()'s data dict and transmit()'s content argument can both
    carry raw bytes (e.g. a compressed flood bitmap) alongside plain
    JSON-serializable values -- json.dumps() cannot handle bytes directly, so
    wrap each one in a small tagged dict and reverse it with
    _unjsonify_bytes() on the other end.
    """
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_b64__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, dict):
        return {k: _jsonify_bytes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonify_bytes(v) for v in obj]
    return obj


def _unjsonify_bytes(obj):
    """Inverse of _jsonify_bytes()."""
    if isinstance(obj, dict):
        if set(obj.keys()) == {"__bytes_b64__"}:
            return base64.b64decode(obj["__bytes_b64__"])
        return {k: _unjsonify_bytes(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unjsonify_bytes(v) for v in obj]
    return obj


class LoRaHandlerClient:
    """RPC proxy for the one real LoRaHandler owned by tools/lora_daemon.py.

    Exposes the same method names the real handler's callers already use
    (is_joined, queue_transmit, queue_binary_transmit,
    process_transmit_queue, transmit, get_queue_depth, get_size_limit) so
    ticktalk_main.py's existing call sites need no changes -- only
    get_lora_handler() itself changed, from constructing a real LoRaHandler
    to returning one of these.

    compressed_encoding() is NOT proxied: it's pure/stateless (confirmed --
    no `self` references in its body), so it's computed locally, matching
    what the daemon does with the exact same code.

    Connects fresh per call (mirroring ticktalk_main.py's
    segformer_via_daemon() style) rather than holding a persistent
    connection, since calls here are infrequent (once per wake cycle, not a
    tight loop).
    """

    # Generous budgets: transmit()/process_transmit_queue() can block on real
    # mDot retries for tens of seconds; queue_*/get_* calls are fast, in-memory
    # ops on the daemon side and should fail fast if the daemon is stuck.
    # The daemon's transmit() gives up at TRANSMIT_DEADLINE_S (75 s), safely
    # inside _LONG_TIMEOUT_S, so the two never disagree about a send.
    _SHORT_TIMEOUT_S = 8
    _LONG_TIMEOUT_S = 90

    def __init__(self, socket_path: str = LORA_DAEMON_SOCKET_PATH, config_file: str = 'lora_config.json'):
        self.socket_path = socket_path
        self.config_file = config_file

    @property
    def config(self) -> Dict[str, Any]:
        """Read lora_config.json fresh on every access.

        Not proxied over IPC: this file is written by LoRaHandler.save_config()
        (fcntl-locked) and read here the same way LoRaHandler.load_config()
        does, so any process can see the current config without round-
        tripping through the daemon -- consistent with get_config_value()/
        get_config(), which stay local for the same reason.
        """
        try:
            with open(self.config_file, 'r') as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception as e:
            print(f"Error loading config: {e}, using empty config")
            return {}

    def get_config(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def _call(self, action: str, timeout: float, **params):
        req = json.dumps({"action": action, "params": _jsonify_bytes(params)}) + "\n"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(min(5, timeout))  # connect: fail fast if daemon isn't ready
            sock.connect(self.socket_path)
            sock.settimeout(timeout)          # read: full budget for the RPC itself
            sock.sendall(req.encode())
            sock.shutdown(socket.SHUT_WR)

            data = bytearray()
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)

        resp = json.loads(data.strip())
        if resp.get("status") != "ok":
            raise RuntimeError(resp.get("message", "LoRa daemon returned an error"))
        return _unjsonify_bytes(resp.get("result"))

    def is_joined(self) -> bool:
        try:
            return bool(self._call("is_joined", self._LONG_TIMEOUT_S))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (is_joined): {e}")
            return False

    def link_check(self) -> Dict[str, Any]:
        try:
            return dict(self._call("link_check", self._LONG_TIMEOUT_S))
        except Exception as e:
            return {"ok": False, "margin_db": None, "gateways": 0, "error": f"LoRa daemon: {e}"}

    def queue_transmit(self, data: Dict[str, Any]) -> bool:
        try:
            return bool(self._call("queue_transmit", self._SHORT_TIMEOUT_S, data=data))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (queue_transmit): {e}")
            return False

    def queue_binary_transmit(self, binary_data) -> bool:
        try:
            return bool(self._call(
                "queue_binary_transmit", self._SHORT_TIMEOUT_S, binary_data=binary_data
            ))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (queue_binary_transmit): {e}")
            return False

    def process_transmit_queue(self) -> None:
        try:
            self._call("process_transmit_queue", self._LONG_TIMEOUT_S)
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (process_transmit_queue): {e}")

    def transmit(self, content, max_retries: int = 2) -> bool:
        try:
            sent = bool(self._call(
                "transmit", self._LONG_TIMEOUT_S, content=content, max_retries=max_retries
            ))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (transmit): {e}")
            return False
        if sent:
            # A bitmap went out over LoRa, live or from the queue (tools/ip_fallback.py)
            try:
                from tools import ip_fallback
                if ip_fallback.is_bitmap_packet(content):
                    ip_fallback.record_delivery("lora")
            except Exception as e:
                print(f"⚠️ IP fallback: LoRa bitmap delivery not recorded: {e}")
        return sent

    def get_queue_depth(self) -> int:
        try:
            return int(self._call("get_queue_depth", self._SHORT_TIMEOUT_S))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (get_queue_depth): {e}")
            return 0

    def get_size_limit(self) -> int:
        try:
            return int(self._call("get_size_limit", self._SHORT_TIMEOUT_S))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (get_size_limit): {e}, using default")
            return 242  # default LoRaWAN payload size, matches LoRaHandler's own default

    def size_limit_now(self) -> int:
        try:
            return int(self._call("size_limit_now", self._LONG_TIMEOUT_S))
        except Exception as e:
            print(f"⚠️ LoRa daemon unreachable (size_limit_now): {e}, using the stored limit")
            return self.get_size_limit()

    def compressed_encoding(self, data: Dict[str, Any]) -> bytes:
        # Pure/stateless -- computed locally, not via RPC (see class docstring).
        return _encode_compressed_packet(data)


def get_lora_handler(socket_path: str = LORA_DAEMON_SOCKET_PATH) -> Optional["LoRaHandlerClient"]:
    """Get a client connected to the LoRa daemon that owns the serial port.

    Returns None if the daemon's socket doesn't exist -- e.g. the daemon
    hasn't started yet, isn't installed on this system, or (in tests/dev)
    isn't running at all. Callers must check for None before dereferencing
    the result, exactly as before this function returned a real LoRaHandler.

    Before this, get_lora_handler() constructed a real LoRaHandler directly
    in whichever OS process called it -- broken because TickTalk's own
    runtime spawns multiple separate OS processes to execute graph nodes, so
    a per-process singleton can't provide real cross-process exclusivity
    over the one physical serial port (confirmed via `ps` PPID chains on a
    real device, 2026-07-22; see docs/LORA_HANDLER_MULTIPROCESS_ISSUE.md).
    Now exactly one process -- tools/lora_daemon.py -- ever constructs a real
    LoRaHandler (via create_lora_handler_with_retry()), and every other
    process gets this lightweight IPC client instead.
    """
    if not os.path.exists(socket_path):
        return None
    try:
        if not stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            print(f"⚠️ {socket_path} exists but is not a socket — LoRa daemon unavailable")
            return None
    except OSError:
        return None
    return LoRaHandlerClient(socket_path)


def lora_unavailable_reason(socket_path: str = LORA_DAEMON_SOCKET_PATH) -> str:
    """Why get_lora_handler() returned None, in words an operator can act on.

    get_lora_handler() returns None only when the daemon's socket is missing or
    is not a socket. A serial-port conflict is handled inside the daemon and
    never surfaces here, so "serial port busy" is the wrong diagnosis.
    """
    if not os.path.exists(socket_path):
        return (f"LoRa daemon not running: no socket at {socket_path} "
                "-- is lora_daemon.service installed and started?")
    try:
        if not stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            return f"{socket_path} exists but is not a socket -- LoRa daemon unavailable"
    except OSError as e:
        return f"cannot stat {socket_path}: {e}"
    return "LoRa daemon socket present but no handler was returned"


def transmit_data(data: Dict[str, Any]) -> bool:
    """Convenience function to transmit sensor data"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.queue_transmit(data)

def transmit_file(file_data: bytes) -> bool:
    """Convenience function to transmit file data"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.queue_file_transmit(file_data)

def transmit_binary(binary_data) -> bool:
    """Convenience function to transmit arbitrary binary data (bytes or hex string)"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.queue_binary_transmit(binary_data)

def transmit_auto(data) -> bool:
    """Convenience function to automatically detect data type and transmit"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.queue_auto(data)

def get_config_value(key: str, default: Any = None) -> Any:
    """Convenience function to get configuration values"""
    handler = get_lora_handler()
    if handler is None:
        return default
    return handler.get_config(key, default)

def check_lora_reception():
    """Convenience function to check LoRa reception status"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.check_reception_status()

def check_mdot_data_config():
    """Convenience function to check mDot data reception configuration"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.check_mdot_data_config()

def get_size_limit() -> int:
    """Convenience function to get the current payload size limit"""
    handler = get_lora_handler()
    if handler is None:
        return 242  # default LoRaWAN payload size, matches LoRaHandler's own default
    return handler.get_size_limit()

def size_limit_now() -> int:
    """The mDot's payload limit asked now (AT+TXS), not the stored one."""
    handler = get_lora_handler()
    if handler is None:
        return 242
    return handler.size_limit_now()

def refresh_size_limit() -> bool:
    """Convenience function to manually refresh the size limit"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.refresh_size_limit()

def get_last_transmission_status() -> dict:
    """Convenience function to get the last transmission status"""
    handler = get_lora_handler()
    if handler is None:
        return {}
    return handler.get_last_transmission_status()

def get_transmission_history() -> list:
    """Convenience function to get the transmission history"""
    handler = get_lora_handler()
    if handler is None:
        return []
    return handler.get_transmission_history()



# Legacy functions for backward compatibility
def transmit(content):
    """Legacy transmit function"""
    handler = get_lora_handler()
    if handler is None:
        return False
    return handler.transmit(content)

def compressed_encoding(data):
    """Legacy encoding function"""
    handler = get_lora_handler()
    if handler is None:
        return None
    return handler.compressed_encoding(data)

# Example packet for testing
example_packet = {
    "timestamp": 1748892908,
    "emergency_status": 1,
    "health_status": 1,
    "battery_percent": 5,
    "temperature_celsius": 23.5,
    "tilt_roll_yaw": [0.1, 0.2, 0.3],
    "lat_lon_z": [40.7128, -74.0060, 12.5],
    "relative_humidity": 55,
    "wittypi_temperature": 25.3,
    "wittypi_battery_voltage": 3.8,
    "wittypi_internal_voltage": 5.1
}

if __name__ == "__main__":
    from sys import argv
    
    handler = LoRaHandler()
    
    try:
        # send a packet when run without an argument
        if len(argv) == 1:
            handler.queue_transmit(example_packet)
            handler.process_transmit_queue()
        elif argv[1] == 'listen':
            # Start listening and keep running
            handler.start_listening()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\nStopping...")
        elif argv[1] == 'test':
            # Test the new reception format
            if len(argv) >= 3:
                test_payload = argv[2]
                handler.decode(test_payload)
            else:
                print("Usage: python lora_handler_concurrent.py test <payload>")
                print("Example: python lora_handler_concurrent.py test 109032")
                print("Example: python lora_handler_concurrent.py test 119100c8")
        # else transmit the file param
        elif argv[1] == 'binary':
            # Transmit arbitrary binary data
            binary_data = b'\x01\x02\x03\x04\x05\x06\x07\x08'
            handler.queue_binary_transmit(binary_data)
            handler.process_transmit_queue()
        else:
            with open(argv[1], 'rb') as file:
                file_data = file.read()
                handler.queue_file_transmit(file_data)
                handler.process_transmit_queue()
    finally:
        handler.close()

def test_error_handling():
    """Test error handling with invalid commands and hex strings"""
    print("Testing error handling...")
    
    # Test invalid hex strings
    invalid_hex_strings = [
        "GG",           # Invalid hex characters
        "123G",         # Mixed valid/invalid
        "123",          # Odd length
        "",             # Empty string
        "12 34",        # Contains spaces
        "12-34",        # Contains hyphens
    ]
    
    for invalid_hex in invalid_hex_strings:
        print(f"Testing invalid hex string: '{invalid_hex}'")
        if not LoRaHandler._is_valid_hex_string(None, invalid_hex):
            print(f"  ✓ Correctly rejected: '{invalid_hex}'")
        else:
            print(f"  ERROR: Should have been rejected: '{invalid_hex}'")
    
    # Test valid hex strings
    valid_hex_strings = [
        "1234",
        "ABCD",
        "abcd",
        "1234ABCD",
        "00FF",
    ]
    
    for valid_hex in valid_hex_strings:
        print(f"Testing valid hex string: '{valid_hex}'")
        if LoRaHandler._is_valid_hex_string(None, valid_hex):
            print(f"  ✓ Correctly accepted: '{valid_hex}'")
        else:
            print(f"  ERROR: Should have been accepted: '{valid_hex}'")
    
    print("Error handling test completed.")

def test_error_recovery():
    """Test error recovery mechanism with retry logic"""
    print("Testing error recovery mechanism...")
    
    # Create a handler instance for testing
    handler = LoRaHandler()
    
    try:
        # Test with valid data (should succeed on first attempt)
        print("\n1. Testing with valid data:")
        valid_data = b'\x01\x02\x03\x04'
        result = handler.transmit(valid_data, max_retries=2)
        print(f"   Result: {'SUCCESS' if result else 'FAILED'}")
        
        # Test with invalid hex string (should fail validation before transmission)
        print("\n2. Testing with invalid hex string:")
        invalid_hex = "GG"  # Invalid hex characters
        result = handler.transmit(invalid_hex, max_retries=2)
        print(f"   Result: {'SUCCESS' if result else 'FAILED'}")
        
        # Test retry mechanism (simulate by using a handler that might fail)
        print("\n3. Testing retry mechanism:")
        print("   (This would normally test actual mDot communication)")
        print("   In a real scenario, errors would trigger retries with input clearing")
        
    finally:
        handler.close()
    
    print("Error recovery test completed.")

def test_transmission_status_integration():
    """Test that transmission status from listening loop is properly used"""
    print("Testing transmission status integration...")
    
    # Create a handler instance for testing
    handler = LoRaHandler()
    
    try:
        # Simulate a successful transmission status from the listening loop
        handler.last_transmission_status = {
            'success': True,
            'message': 'OK',
            'details': {'confirmation': 'OK', 'type': 'success_response'},
            'timestamp': time.time()
        }
        
        print("1. Testing with simulated successful transmission status:")
        print(f"   Last transmission status: {handler.last_transmission_status}")
        
        # Test that the status is used correctly
        if handler.last_transmission_status and handler.last_transmission_status.get('success', False):
            status_time = handler.last_transmission_status.get('timestamp', 0)
            if time.time() - status_time < 5:
                print("   ✓ Recent successful status detected")
            else:
                print("   ✗ Status too old")
        else:
            print("   ✗ No successful status found")
        
        # Test with old status
        print("\n2. Testing with old transmission status:")
        handler.last_transmission_status['timestamp'] = time.time() - 10  # 10 seconds ago
        print(f"   Last transmission status: {handler.last_transmission_status}")
        
        if handler.last_transmission_status and handler.last_transmission_status.get('success', False):
            status_time = handler.last_transmission_status.get('timestamp', 0)
            if time.time() - status_time < 5:
                print("   ✓ Recent successful status detected")
            else:
                print("   ✓ Old status correctly ignored")
        else:
            print("   ✗ No successful status found")
        
    finally:
        handler.close()
    
    print("Transmission status integration test completed.")

if __name__ == "__main__":
    import sys
    from sys import argv
    
    # Run error handling tests
    test_error_handling()
    print("\n" + "="*50 + "\n")
    
    test_error_recovery()
    print("\n" + "="*50 + "\n")
    
    test_transmission_status_integration()
    print("\n" + "="*50 + "\n")
    
    # Run main handler
    handler = LoRaHandler()
    try:
        if len(argv) == 1:
            # Test with sample binary data
            binary_data = b'\x01\x02\x03\x04\x05\x06\x07\x08'
            handler.queue_binary_transmit(binary_data)
            handler.process_transmit_queue()
        else:
            with open(argv[1], 'rb') as file:
                file_data = file.read()
                handler.queue_file_transmit(file_data)
                handler.process_transmit_queue()
    finally:
        handler.close()

