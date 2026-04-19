"""IP uplink/downlink transport for SU-WaterCam.

Sends sensor channel data to the WaterCam FastAPI server over HTTP (WiFi or
cellular) and polls for queued downlink commands.  Designed to run outside of
TickTalkPython so the logic can be tested standalone before integration.

Usage (module)
--------------
    from tools.transmit_ip import IPTransmitter
    tx = IPTransmitter()                  # reads runtime_config.json
    result = tx.send_uplink(channels)     # {"success": bool, "status_code": int|None, ...}
    reply  = tx.poll_downlink()           # {"success": bool, "status_code": int|None, "command": dict|None, ...}
    cmd    = reply["command"]             # dict or None

Usage (CLI smoke-test)
----------------------
    python tools/transmit_ip.py

Configuration
-------------
All settings live in runtime_config.json under the "ip_upload" key.  See the
project docs/IP_TRANSMISSION.md for a full field reference.

Channel format
--------------
Each element of `channels` must match the ChannelItem schema the server expects:
    {"code": "02 01", "payload_hex": "00000064"}

Codes used by this device (from API CHANNEL_REGISTRY):
    "00 01"  device_ts        (8 bytes, UNIX seconds as u64 big-endian)
    "02 01"  battery_pct      (4 bytes, uint32)
    "03 01"  imu_block        (12 bytes)
    "04 01"  gps_block        (8 bytes: lat int32 /1e6, lon int32 /1e6)
    "05 01"  temperature_c    (2 bytes, int16 /100.0)
    "06 01"  humidity_pct     (1 byte, uint8)
    "07 17"  camera_flood_detect  (4 bytes, bool as uint32)
    "07 27"  camera_new_local_max (4 bytes, bool as uint32)
    "08 18"  camera_flood_bitmap  (variable length, raw bitmap bytes as hex string)
    "09 19"  sr_area_threshold_pct        (4 bytes)
    "09 29"  sr_stage_threshold_cm        (4 bytes)
    "09 39"  sr_monitoring_frequency      (4 bytes)
    "09 49"  sr_emergency_frequency       (4 bytes)
    "09 59"  sr_neighborhood_emerg_freq   (4 bytes)
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Default config path — relative to the project root (one level above tools/).
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "runtime_config.json"
)


def _coerce_int(value: Any, default: int) -> int:
    """Return ``int(value)`` or ``default`` if value is missing/invalid."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: float) -> float:
    """Return ``float(value)`` or ``default`` if value is missing/invalid."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_ip_config(config_path: str = _DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Load and return the ip_upload section from runtime_config.json.

    Returns an empty dict (all defaults) if the file is missing or contains
    invalid JSON — runtime_config.json is gitignored and may not exist in fresh
    dev or CI environments.
    """
    try:
        with open(config_path) as f:
            cfg = json.load(f)
    except OSError as exc:
        # Covers FileNotFoundError, PermissionError, and other I/O failures.
        logger.debug("Cannot read config at %s (%s); using defaults", config_path, exc)
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in %s: %s; using defaults", config_path, exc)
        return {}
    if not isinstance(cfg, dict):
        logger.warning("Unexpected config format in %s; using defaults", config_path)
        return {}
    ip_cfg = cfg.get("ip_upload", {})
    if not isinstance(ip_cfg, dict):
        logger.warning("ip_upload in %s is not an object; using defaults", config_path)
        return {}
    return ip_cfg


class IPTransmitter:
    """Handles uplink posting and downlink polling over HTTP.

    Parameters
    ----------
    config_path:
        Path to runtime_config.json.  Defaults to the project-root copy.
    override_url:
        If provided, overrides the server_url from config (useful in tests).
    override_device_id:
        If provided, overrides the device_id from config.

    Notes
    -----
    ``self.enabled`` reflects the ``ip_upload.enabled`` config flag, but
    ``send_uplink()`` and ``poll_downlink()`` do **not** check it internally —
    the flag is an integration-layer concern enforced by ``ticktalk_main.py``.
    Direct callers (tests, CLI smoke-test) are responsible for checking
    ``tx.enabled`` themselves if they want to respect the flag.
    """

    def __init__(
        self,
        config_path: str = _DEFAULT_CONFIG_PATH,
        override_url: Optional[str] = None,
        override_device_id: Optional[str] = None,
    ) -> None:
        cfg = _load_ip_config(config_path)

        self.enabled: bool = cfg.get("enabled", False)
        self.server_url: str = (override_url or cfg.get("server_url", "http://localhost:8000")).rstrip("/")
        self.api_key: str = cfg.get("api_key", "")
        self.device_id: str = override_device_id or cfg.get("device_id", "watercam-001")
        self.timeout_s: int = max(1, _coerce_int(cfg.get("timeout_s"), 15))
        self.retry_attempts: int = max(1, _coerce_int(cfg.get("retry_attempts"), 3))
        self.retry_backoff_s: float = _coerce_float(cfg.get("retry_backoff_s"), 2.0)
        self.fallback_to_lora: bool = cfg.get("fallback_to_lora", True)

        self._session = requests.Session()
        if self.api_key:
            self._session.headers.update({"Authorization": f"Bearer {self.api_key}"})
        self._session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def send_uplink(
        self,
        channels: List[Dict[str, str]],
        device_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """POST sensor channel data to /ip/uplink.

        Parameters
        ----------
        channels:
            List of channel dicts, each with "code" and "payload_hex".
            Example:
                [
                    {"code": "02 01", "payload_hex": "00000064"},
                    {"code": "05 01", "payload_hex": "0B80"},
                ]
        device_ts:
            Optional UNIX epoch (seconds) timestamp from the device clock.
            When provided, the server uses it as the measurement timestamp
            instead of the server arrival time.

        Returns
        -------
        dict with keys:
            "success"    : bool
            "status_code": int or None
            "response"   : decoded JSON body or None
            "error"      : error message string or None
            "attempts"   : number of attempts made
        """
        if not channels:
            return _err_result("send_uplink called with empty channels list")

        payload: Dict[str, Any] = {
            "device_id": self.device_id,
            "channels": channels,
        }
        if device_ts is not None:
            payload["device_ts"] = device_ts

        url = f"{self.server_url}/ip/uplink"
        return self._post_with_retry(url, payload)

    def poll_downlink(self) -> Dict[str, Any]:
        """GET /ip/downlink/{device_id} to retrieve the oldest pending command.

        The server atomically marks the command as delivered on retrieval, so
        calling this multiple times without handling the first response will
        consume queued commands.

        Returns
        -------
        dict with keys:
            "success"    : bool
            "command"    : dict or None  (None means no pending command)
            "status_code": int or None
            "error"      : error message string or None
        """
        url = f"{self.server_url}/ip/downlink/{self.device_id}"

        try:
            resp = self._session.get(url, timeout=self.timeout_s)
        except requests.exceptions.ConnectionError as exc:
            return {"success": False, "command": None, "status_code": None,
                    "error": f"Connection error: {exc}"}
        except requests.exceptions.Timeout:
            return {"success": False, "command": None, "status_code": None,
                    "error": f"Request timed out after {self.timeout_s}s"}
        except requests.exceptions.RequestException as exc:
            return {"success": False, "command": None, "status_code": None,
                    "error": str(exc)}

        if resp.status_code == 404:
            # Device not yet registered on the server — treat as "no pending commands"
            # rather than a transport error, so callers handle it uniformly.
            return {
                "success": True,
                "command": None,
                "status_code": resp.status_code,
                "error": None,
            }

        if resp.status_code != 200:
            return {
                "success": False,
                "command": None,
                "status_code": resp.status_code,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }

        try:
            body = resp.json()
        except ValueError:
            return {"success": False, "command": None,
                    "status_code": resp.status_code,
                    "error": "Non-JSON response from server"}

        return {
            "success": True,
            "command": body.get("command"),   # dict or None
            "status_code": resp.status_code,
            "error": None,
        }

    def is_reachable(self, timeout_s: Optional[float] = None) -> bool:
        """Quick check — returns True if the server /health endpoint responds.

        Parameters
        ----------
        timeout_s:
            Override the instance timeout for this call only.  Pass a small
            value (e.g. 3) in CI/test contexts to fail fast when the server
            is not running.
        """
        t = timeout_s if timeout_s is not None else self.timeout_s
        try:
            resp = self._session.get(f"{self.server_url}/health", timeout=t)
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def close(self) -> None:
        """Close the underlying requests.Session and release pooled connections."""
        self._session.close()

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _post_with_retry(
        self, url: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """POST payload to url with exponential backoff retry.

        Sleep before retry N is ``retry_backoff_s * 2^(N-1)`` seconds, so for
        the default ``retry_backoff_s=2`` and ``retry_attempts=3``:
        attempt 1 → immediate, attempt 2 → 2 s, attempt 3 → 4 s.
        """
        last_error: str = "unknown error"
        last_status: Optional[int] = None

        for attempt in range(1, self.retry_attempts + 1):
            try:
                resp = self._session.post(
                    url, json=payload, timeout=self.timeout_s
                )
                last_status = resp.status_code

                if resp.status_code in (200, 201):
                    try:
                        body = resp.json()
                    except ValueError:
                        body = {"raw": resp.text}
                    logger.info(
                        "IP uplink OK (attempt %d/%d): %s",
                        attempt, self.retry_attempts, resp.status_code,
                    )
                    return {
                        "success": True,
                        "status_code": resp.status_code,
                        "response": body,
                        "error": None,
                        "attempts": attempt,
                    }

                # Server-side error — don't retry 4xx (client mistake)
                if 400 <= resp.status_code < 500:
                    logger.error(
                        "IP uplink rejected by server (HTTP %d): %s",
                        resp.status_code, resp.text[:300],
                    )
                    return _err_result(
                        f"HTTP {resp.status_code}: {resp.text[:200]}",
                        status_code=resp.status_code,
                        attempts=attempt,
                    )

                # 5xx — retry
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(
                    "IP uplink attempt %d/%d failed with %d, retrying...",
                    attempt, self.retry_attempts, resp.status_code,
                )

            except requests.exceptions.ConnectionError as exc:
                last_error = f"Connection error: {exc}"
                logger.warning(
                    "IP uplink attempt %d/%d connection error: %s",
                    attempt, self.retry_attempts, exc,
                )
            except requests.exceptions.Timeout:
                last_error = f"Timeout after {self.timeout_s}s"
                logger.warning(
                    "IP uplink attempt %d/%d timed out", attempt, self.retry_attempts
                )
            except requests.exceptions.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "IP uplink attempt %d/%d request error: %s",
                    attempt, self.retry_attempts, exc,
                )

            if attempt < self.retry_attempts:
                sleep_s = self.retry_backoff_s * (2 ** (attempt - 1))
                logger.debug("Waiting %.1fs before retry %d", sleep_s, attempt + 1)
                time.sleep(sleep_s)

        logger.error(
            "IP uplink failed after %d attempts. Last error: %s",
            self.retry_attempts, last_error,
        )
        return _err_result(
            last_error,
            status_code=last_status,
            attempts=self.retry_attempts,
        )


# ------------------------------------------------------------------ #
# Module-level convenience functions                                   #
# ------------------------------------------------------------------ #

def send_uplink(
    channels: List[Dict[str, str]],
    device_ts: Optional[int] = None,
    config_path: str = _DEFAULT_CONFIG_PATH,
) -> Dict[str, Any]:
    """Module-level wrapper: create a transmitter, send one uplink, then close."""
    tx = IPTransmitter(config_path=config_path)
    try:
        return tx.send_uplink(channels, device_ts=device_ts)
    finally:
        tx.close()


def poll_downlink(config_path: str = _DEFAULT_CONFIG_PATH) -> Dict[str, Any]:
    """Module-level wrapper: create a transmitter, poll for one downlink, then close."""
    tx = IPTransmitter(config_path=config_path)
    try:
        return tx.poll_downlink()
    finally:
        tx.close()


def apply_downlink_command(
    cmd: Dict[str, Any],
    set_param_fn: Callable[[str, Any], None] | None = None,
) -> Dict[str, Any]:
    """Decode a downlink command dict and apply each recognised parameter change.

    This is the standalone-tools counterpart to the TickTalkPython
    ``ip_downlink_poll_and_apply`` action in ``ticktalk_main.py``.  It contains
    no TickTalkPython dependencies and can be called from tests, CLI scripts, or
    any future integration layer.

    Parameters
    ----------
    cmd:
        The dict at ``poll_downlink()["command"]``.  Must have a ``"parts"``
        field — a list of ``{"code": str, "payload_hex": str}`` dicts.
    set_param_fn:
        ``Callable[[str, Any], None]`` used to persist each change.  Defaults
        to ``tools.lora_runtime_integration.set_parameter``.  Pass a custom
        function in tests to avoid touching the real runtime config.

    Returns
    -------
    dict with keys:
        "applied" : list[str] — "param=value" strings for each change applied
        "skipped" : list[str] — string identifiers for skipped parts: the
            command code when available, otherwise a stringified malformed part
            or invalid code value
        "queue_id": the queue_id from cmd, or None
    """
    if set_param_fn is None:
        from tools.lora_runtime_integration import set_parameter as set_param_fn

    # Index-based lookup tables — must match server app/encoders.py constants.
    _MF_HOURS = [1, 3, 6, 24, 72]       # monitoring_freq_h allowed values
    _EF_MIN   = [2, 5, 10]              # emergency_freq_min allowed values
    _FF_MIN   = [10, 20, 30, 40, 50, 60]  # flood_code_freq_min allowed values

    # Expected payload byte-lengths for fixed-width codes (variable-width codes
    # are absent from this dict and accepted at any non-zero length).
    _expected_len: Dict[str, int] = {
        "10 90": 1,
        "11 91": 2,
        "12 92": 1,
        "13 93": 1,
        "14 94": 1,
    }

    parts_raw = cmd.get("parts")
    if parts_raw is None:
        parts: list = []
    elif isinstance(parts_raw, list):
        parts = parts_raw
    else:
        logger.warning(
            "apply_downlink_command: malformed parts field "
            "(expected list, got %s) — treating as empty",
            type(parts_raw).__name__,
        )
        parts = []

    applied: List[str] = []
    skipped: List[str] = []

    for part in parts:
        if not isinstance(part, dict):
            logger.warning(
                "apply_downlink_command: malformed part (expected dict, got %s) — skipping",
                type(part).__name__,
            )
            skipped.append("<malformed_part>")
            continue

        code_raw = part.get("code", "")
        payload_hex = part.get("payload_hex", "")
        if not isinstance(code_raw, str) or not isinstance(payload_hex, str):
            logger.warning(
                "apply_downlink_command: non-string code or payload_hex in part %s — skipping",
                part,
            )
            skipped.append(str(code_raw))
            continue
        code = code_raw.strip()

        try:
            payload_bytes = bytes.fromhex(payload_hex)
        except (TypeError, ValueError):
            logger.warning(
                "apply_downlink_command: bad payload_hex in part %s — skipping", part
            )
            skipped.append(code)
            continue

        if code in _expected_len and len(payload_bytes) != _expected_len[code]:
            logger.warning(
                "apply_downlink_command: wrong payload length for %s "
                "(expected %d, got %d) — skipping",
                code, _expected_len[code], len(payload_bytes),
            )
            skipped.append(code)
            continue

        if code == "10 90":  # area_threshold_pct — direct u8 value
            val = int.from_bytes(payload_bytes, "big")
            set_param_fn("area_threshold", val)
            logger.info("Applied: area_threshold = %d%%", val)
            applied.append(f"area_threshold={val}%")

        elif code == "11 91":  # stage_threshold_cm — u16 big-endian
            val_cm = int.from_bytes(payload_bytes, "big")
            set_param_fn("stage_threshold", val_cm)
            logger.info("Applied: stage_threshold = %dcm", val_cm)
            applied.append(f"stage_threshold={val_cm}cm")

        elif code == "12 92":  # monitoring_freq_h — u8 index into _MF_HOURS
            idx = int.from_bytes(payload_bytes, "big")
            if 0 <= idx < len(_MF_HOURS):
                hours = _MF_HOURS[idx]
                set_param_fn("monitoring_frequency", hours * 60)  # stored in minutes
                logger.info("Applied: monitoring_frequency = %dh (%dmin)", hours, hours * 60)
                applied.append(f"monitoring_frequency={hours}h")
            else:
                logger.warning("apply_downlink_command: monitoring_freq index %d out of range", idx)
                skipped.append(code)

        elif code == "13 93":  # emergency_freq_min — u8 index into _EF_MIN
            idx = int.from_bytes(payload_bytes, "big")
            if 0 <= idx < len(_EF_MIN):
                mins = _EF_MIN[idx]
                set_param_fn("emergency_frequency", mins)
                logger.info("Applied: emergency_frequency = %dmin", mins)
                applied.append(f"emergency_frequency={mins}min")
            else:
                logger.warning("apply_downlink_command: emergency_freq index %d out of range", idx)
                skipped.append(code)

        elif code == "14 94":  # flood_code_freq_min — u8 index into _FF_MIN
            idx = int.from_bytes(payload_bytes, "big")
            if 0 <= idx < len(_FF_MIN):
                mins = _FF_MIN[idx]
                set_param_fn("neighborhood_emergency_frequency", mins)
                logger.info("Applied: neighborhood_emergency_frequency = %dmin", mins)
                applied.append(f"neighborhood_emergency_frequency={mins}min")
            else:
                logger.warning("apply_downlink_command: flood_code_freq index %d out of range", idx)
                skipped.append(code)

        else:
            logger.warning("apply_downlink_command: unrecognised code '%s' — ignoring", code)
            skipped.append(code)

    return {
        "applied": applied,
        "skipped": skipped,
        "queue_id": cmd.get("queue_id"),
    }


# ------------------------------------------------------------------ #
# Internal util                                                        #
# ------------------------------------------------------------------ #

def _err_result(
    message: str,
    status_code: Optional[int] = None,
    attempts: int = 0,
) -> Dict[str, Any]:
    return {
        "success": False,
        "status_code": status_code,
        "response": None,
        "error": message,
        "attempts": attempts,
    }


# ------------------------------------------------------------------ #
# CLI smoke-test                                                       #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import struct

    tx = IPTransmitter()
    print(f"Server : {tx.server_url}")
    print(f"Device : {tx.device_id}")
    print(f"Enabled: {tx.enabled}")
    print()

    # Encode a minimal uplink: battery 75%, temperature 22.50 °C
    battery_hex = struct.pack(">I", 75).hex()            # "0000004B"
    temp_raw = int(22.50 * 100)                          # 2250 → 0x08CA
    temp_hex = struct.pack(">h", temp_raw).hex()         # signed int16
    ts_now = int(time.time())
    ts_hex = struct.pack(">Q", ts_now).hex()             # 8-byte big-endian u64

    channels = [
        {"code": "00 01", "payload_hex": ts_hex},
        {"code": "02 01", "payload_hex": battery_hex},
        {"code": "05 01", "payload_hex": temp_hex},
    ]

    print("--- Uplink test ---")
    print(f"Channels: {json.dumps(channels, indent=2)}")
    result = tx.send_uplink(channels, device_ts=ts_now)
    print(f"Result  : {json.dumps(result, indent=2)}")

    print()
    print("--- Downlink poll ---")
    dl = tx.poll_downlink()
    print(f"Result  : {json.dumps(dl, indent=2)}")
