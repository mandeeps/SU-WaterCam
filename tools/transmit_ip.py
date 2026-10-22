#!/home/pi/SU-WaterCam/venv/bin/python
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

import os as _os, sys as _sys  # noqa: E401
_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO not in _sys.path:
    _sys.path.insert(0, _REPO)   # so ./tools/transmit_ip.py can import the tools package

import ipaddress
import json
import logging
import os
import socket
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from tools import config_io, drop_counter

logger = logging.getLogger(__name__)

# Default config path — relative to the project root (one level above tools/).
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "runtime_config.json"
)

# Pending-uplink queue directory — persists failed readings for retry next cycle.
_DEFAULT_QUEUE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "ip_uplink_pending"
)

# On cellular a wake drains at most this many queued readings, or this many bytes of
# them (WiFi: all), so a long backlog doesn't cost a month's data in one wake.
CELLULAR_DRAIN_MAX_ENTRIES = 5
CELLULAR_DRAIN_MAX_BYTES = 200_000
# A .tmp file left by a write that never finished, older than this, is removed
ORPHAN_TMP_AGE_S = 300
# The channel code of the flood bitmap: only the newest one stays queued
BITMAP_CODE = "08 18"
# A 4xx other than these says the entry itself is wrong; these say try again later
_RETRY_STATUSES = (401, 403, 429)


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


# Commands and the clock are only taken from the server over a transport that
# proves who answered (SU-WaterCam #203): the tailnet (WireGuard to the API's
# tailnet address) or HTTPS with a verified certificate. Anything else still
# gets the upload, but the node neither asks for nor applies commands from it,
# and ignores its Date header.
_TAILNET_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def _resolves_into_tailnet(host: str) -> bool:
    """True when every address `host` resolves to is a tailnet address (100.64.0.0/10)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {i[4][0].split("%")[0] for i in infos}
    try:
        return bool(addrs) and all(any(ipaddress.ip_address(a) in net for net in _TAILNET_NETS)
                                   for a in addrs)
    except ValueError:
        return False


def trusted_server(url: str) -> bool:
    """True when replies from `url` can carry commands and the time."""
    from urllib.parse import urlparse
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https":
        return True                     # requests verifies the certificate
    if host == "localhost":
        return True
    if host.endswith(".ts.net"):
        # A tailnet name proves nothing by itself: over plain http the traffic is only
        # protected if it resolves into the tailnet (WireGuard), so check where it goes
        return _resolves_into_tailnet(host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or any(ip in net for net in _TAILNET_NETS)


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

    _key_refused_logged = False
    _untrusted_logged = False

    @property
    def trusted(self) -> bool:
        """Whether this server's replies may carry commands and the time (trusted_server)."""
        return trusted_server(self.server_url)

    def _auth_headers(self) -> Dict[str, str]:
        """The API key header, only for a server it may go to: https, or a tailnet
        address (a *.ts.net name must resolve into 100.64.0.0/10). Checked per
        request, as the name only resolves once the link is up. Otherwise the key
        stays home and the server will refuse the request."""
        if not self.api_key:
            return {}
        if trusted_server(self.server_url):
            return {"Authorization": f"Bearer {self.api_key}"}
        if not self._key_refused_logged:
            logger.warning("Not sending the API key to %s: not https and not a tailnet address",
                           self.server_url)
            self._key_refused_logged = True
        return {}

    def __init__(
        self,
        config_path: str = _DEFAULT_CONFIG_PATH,
        override_url: Optional[str] = None,
        override_device_id: Optional[str] = None,
        queue_dir: Optional[str] = None,
    ) -> None:
        cfg = _load_ip_config(config_path)

        self.enabled: bool = cfg.get("enabled", False)
        # A server to send to: the IP fallback (tools/ip_fallback.py) uses it
        # even with enabled false, but never the localhost default
        self.configured: bool = bool(override_url or cfg.get("server_url"))
        self.server_url: str = (override_url or cfg.get("server_url", "http://localhost:8000")).rstrip("/")
        if self.configured and not trusted_server(self.server_url):
            logger.warning("server_url %s is neither https nor (yet) a tailnet address: uploads go "
                           "without the API key, and commands and the server's time are ignored (#203)",
                           self.server_url)
        self.api_key: str = cfg.get("api_key", "")
        self.device_id: str = override_device_id or cfg.get("device_id", "watercam-001")
        self.timeout_s: int = max(1, _coerce_int(cfg.get("timeout_s"), 15))
        self.retry_attempts: int = max(1, _coerce_int(cfg.get("retry_attempts"), 3))
        self.retry_backoff_s: float = _coerce_float(cfg.get("retry_backoff_s"), 2.0)
        self.fallback_to_lora: bool = cfg.get("fallback_to_lora", True)
        self.max_queue_depth: int = max(1, _coerce_int(cfg.get("max_queue_depth"), 48))
        self.max_queue_age_days: float = max(0.0, _coerce_float(cfg.get("max_queue_age_days"), 7.0))
        # Transport order LoRa -> WiFi -> cellular: skip the IP uplink while
        # the mDot is joined, and use the named NetworkManager connection only
        # when the server can't be reached without it.
        self.only_if_lora_unavailable: bool = cfg.get("only_if_lora_unavailable", True) is not False
        cell = cfg.get("cellular_connection", "Quectel")
        self.cellular_connection: str = cell if isinstance(cell, str) else ""
        self.cellular_connect_timeout_s: int = max(5, _coerce_int(cfg.get("cellular_connect_timeout_s"), 90))
        # How long to wait for a saved WiFi network that's in range but not yet
        # usable before falling back to cellular (0 = don't wait).
        self.wifi_grace_s: int = max(0, _coerce_int(cfg.get("wifi_grace_s"), 45))
        self._queue_dir: str = os.path.abspath(queue_dir or _DEFAULT_QUEUE_DIR)

        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._key_refused_logged = False
        self._untrusted_logged = False

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def send_uplink(
        self,
        channels: List[Dict[str, str]],
        device_ts: Optional[int] = None,
        want_command: bool = False,
        ip_fallback: bool = False,
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
        want_command:
            Ask the server to put the pending commands in the reply
            (`reply_commands()`) instead of the node polling for them. Only
            for a send whose reply is acted on.
        ip_fallback:
            This is a LoRa unit's IP fallback upload (tools/ip_fallback.py): the
            server keeps sending its commands over LoRa rather than switching
            the device to IP transport. A server that predates it ignores it.

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
        if want_command and self.trusted:     # only ask when we'd apply them
            payload["want_command"] = True
        if ip_fallback:
            payload["ip_fallback"] = True
        # Acks that couldn't be posted to /ip/ack (tools/command_log.py)
        acks = _pending_acks() if self.trusted else []
        if acks:
            payload["acks"] = acks

        url = f"{self.server_url}/ip/uplink"
        result = self._post_with_retry(url, payload)
        if acks and result.get("success"):
            _clear_pending_acks(a["id"] for a in acks)
        return result

    def post_acks(self, acks: List[Dict[str, Any]]) -> bool:
        """POST /ip/ack; True if the server took them. Kept for the next uplink otherwise."""
        if not acks:
            return True
        ok = False
        if self.trusted:
            try:
                resp = self._session.post(f"{self.server_url}/ip/ack",
                                          json={"device_id": self.device_id, "acks": acks},
                                          timeout=self.timeout_s, headers=self._auth_headers())
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                ok = 200 <= resp.status_code < 300 and isinstance(body, dict) and "acked" in body
                if not ok:
                    logger.warning("POST /ip/ack: HTTP %s, not the API's reply", resp.status_code)
            except requests.exceptions.RequestException as exc:
                logger.warning("POST /ip/ack failed: %s", exc)
        if not ok:
            try:
                from tools import command_log
            except ImportError:
                import command_log
            command_log.add_pending_acks(acks)
        return ok

    def poll_downlink(self) -> Dict[str, Any]:
        """GET /ip/downlink/{device_id}: the commands waiting for this node's ack.

        The server offers them until they are acked (`post_acks`), so polling
        twice is harmless; tools/command_log.py applies each frame once.

        Returns
        -------
        dict with keys:
            "success"    : bool
            "commands"   : list of {"id", "frame"} (empty: nothing pending)
            "status_code": int or None
            "error"      : error message string or None
        """
        if not self.trusted:
            # Commands from a server we don't trust are never applied
            return {"success": True, "commands": [], "status_code": None, "error": None,
                    "untrusted": True}
        url = f"{self.server_url}/ip/downlink/{self.device_id}"

        try:
            resp = self._session.get(url, timeout=self.timeout_s, headers=self._auth_headers())
        except requests.exceptions.ConnectionError as exc:
            return {"success": False, "commands": [], "status_code": None,
                    "error": f"Connection error: {exc}"}
        except requests.exceptions.Timeout:
            return {"success": False, "commands": [], "status_code": None,
                    "error": f"Request timed out after {self.timeout_s}s"}
        except requests.exceptions.RequestException as exc:
            return {"success": False, "commands": [], "status_code": None,
                    "error": str(exc)}

        if resp.status_code == 404:
            # Device not yet registered on the server: nothing pending
            return {"success": True, "commands": [], "status_code": resp.status_code, "error": None}

        if resp.status_code != 200:
            return {"success": False, "commands": [], "status_code": resp.status_code,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        try:
            body = resp.json()
        except ValueError:
            return {"success": False, "commands": [], "status_code": resp.status_code,
                    "error": "Non-JSON response from server"}

        commands = body.get("commands") if isinstance(body, dict) else None
        return {"success": True, "commands": commands if isinstance(commands, list) else [],
                "status_code": resp.status_code, "error": None}

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
            resp = self._session.get(f"{self.server_url}/health", timeout=t)  # no key needed
            if resp.status_code != 200:
                return False
            body = resp.json()          # a captive portal's 200 page is not our server
            return isinstance(body, dict) and body.get("status") == "ok"
        except (requests.exceptions.RequestException, ValueError):
            return False

    def wait_reachable(self, deadline_s: float, probe_timeout_s: float = 5) -> bool:
        """Probe /health until it answers or ``deadline_s`` seconds have passed."""
        end = time.monotonic() + deadline_s
        while True:
            if self.is_reachable(timeout_s=probe_timeout_s):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(2)

    def wait_tailnet(self, deadline_s: float) -> bool:
        """Wait for Tailscale to be back online after a link change, without
        sending anything over the link.

        Used on cellular instead of ``wait_reachable``: probing /health every
        2 s while Tailscale finds a path costs cellular data, and the uplink
        POST, with its retries, is a check of its own. This asks the local
        daemon (``tailscale status``) whether this node and the server are
        online. True straight away when the server isn't a tailnet name or the
        status can't be read, leaving the decision to the POST.
        """
        from urllib.parse import urlparse
        host = (urlparse(self.server_url).hostname or "").lower()
        if not host.endswith(".ts.net"):
            return True
        end = time.monotonic() + deadline_s
        while True:
            online = tailscale_online(host)
            if online is None or online:
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(2)

    def server_only_down(self) -> bool:
        """True when WiFi is connected and Tailscale shows the server's peer online
        (``tailscale status``, nothing sent): the server isn't answering over a
        working path, so cellular wouldn't reach it either and would only cost
        data. False when that can't be told (not a tailnet name, peer unknown)."""
        from urllib.parse import urlparse
        host = (urlparse(self.server_url).hostname or "").lower()
        if not host.endswith(".ts.net") or not wifi_connected():
            return False
        return tailscale_online(host) is True

    def close(self) -> None:
        """Close the underlying requests.Session and release pooled connections."""
        self._session.close()

    # ------------------------------------------------------------------ #
    # Store-and-forward queue                                              #
    # ------------------------------------------------------------------ #

    def _queued_files(self) -> List[str]:
        """The queue's entries, oldest first; orphaned .tmp files are cleaned up on the way."""
        try:
            names = os.listdir(self._queue_dir)
        except FileNotFoundError:
            return []
        now = time.time()
        for f in names:
            if f.endswith(".tmp") or ".tmp." in f:
                path = os.path.join(self._queue_dir, f)
                try:
                    if now - os.path.getmtime(path) > ORPHAN_TMP_AGE_S:
                        os.unlink(path)
                except OSError:
                    pass
        return sorted(f for f in names if f.endswith(".json"))

    def _drop_older_bitmaps(self, device_ts: int) -> int:
        """Remove the bitmap channel from queued readings taken at or before
        `device_ts`: a newer one is queued, and an older scene would only be
        sent over cellular to show the dashboard something out of date (as the
        LoRa queue does, tools/lora_store_forward.py). Returns how many lost it."""
        cleared = 0
        for fname in self._queued_files():
            path = os.path.join(self._queue_dir, fname)
            try:
                with open(path) as f:
                    record = json.load(f)
                channels = record["channels"]
                if (not any(isinstance(c, dict) and c.get("code") == BITMAP_CODE for c in channels)
                        or record.get("device_ts", 0) > device_ts):
                    continue
                record["channels"] = [c for c in channels
                                      if not (isinstance(c, dict) and c.get("code") == BITMAP_CODE)]
                config_io.write_json(path, record, mode=0o644)
                cleared += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return cleared

    def _enqueue(self, channels: List[Dict[str, str]], device_ts: int) -> bool:
        """Persist a failed uplink to the pending queue for retry next cycle.

        Writes an atomic, fsynced JSON file to ``self._queue_dir``.  If the queue
        is already at ``max_queue_depth``, the oldest entry is dropped to make
        room.  Only the newest bitmap stays queued: queuing one removes the
        bitmap channel from older entries.  Returns True on success, False on
        I/O error.
        """
        try:
            os.makedirs(self._queue_dir, exist_ok=True)
            if any(isinstance(c, dict) and c.get("code") == BITMAP_CODE for c in channels):
                self._drop_older_bitmaps(device_ts)
            existing = self._queued_files()
            while len(existing) >= self.max_queue_depth:
                try:
                    os.unlink(os.path.join(self._queue_dir, existing.pop(0)))
                    drop_counter.record("ip", path=drop_counter.path_for(self._queue_dir))
                except OSError:
                    pass
            record = {
                "channels": channels,
                "device_ts": device_ts,
                "enqueued_at": time.time(),
            }
            fname = f"{device_ts}_{len(existing):06d}.json"
            while os.path.exists(os.path.join(self._queue_dir, fname)):
                fname = f"{device_ts}_{int(time.time() * 1000) % 1000000:06d}.json"
            config_io.write_json(os.path.join(self._queue_dir, fname), record, mode=0o644)
            logger.info("Queued uplink to %s (%d channels)", fname, len(channels))
            return True
        except OSError as exc:
            logger.warning("Failed to enqueue uplink: %s", exc)
            return False

    def _drop_entry(self, path: str, why: str) -> None:
        logger.warning("Dropping queue entry %s: %s", os.path.basename(path), why)
        try:
            os.unlink(path)
            drop_counter.record("ip", path=drop_counter.path_for(self._queue_dir))
        except OSError:
            pass

    def _drain_queue(self, metered: bool = False) -> Dict[str, Any]:
        """Send queued entries oldest-first; stop on first send failure.

        ``metered`` (on cellular) caps the wake at CELLULAR_DRAIN_MAX_ENTRIES
        entries or CELLULAR_DRAIN_MAX_BYTES bytes, whichever comes first; the rest
        waits for the next wake. An entry the server refuses as wrong (a 4xx other
        than 401/403/429) or that can't be read is dropped, not left to block the
        queue.

        Returns a dict with keys:
            "drained"    : int            — number of entries successfully sent
            "failed"     : bool           — True if a send failed mid-drain
            "failed_file": Optional[str]  — filename of the entry that failed
            "drained_ts" : List[int]      — device_ts of each entry sent
            "limited"    : bool           — stopped at the metered cap
        """
        result: Dict[str, Any] = {"drained": 0, "failed": False, "failed_file": None,
                                  "drained_ts": [], "limited": False}
        cutoff = time.time() - self.max_queue_age_days * 86400
        sent_bytes = 0

        for fname in self._queued_files():
            path = os.path.join(self._queue_dir, fname)
            if metered and (result["drained"] >= CELLULAR_DRAIN_MAX_ENTRIES
                            or sent_bytes >= CELLULAR_DRAIN_MAX_BYTES):
                result["limited"] = True
                break
            try:
                size = os.path.getsize(path)
                with open(path) as f:
                    record = json.load(f)
                channels = record["channels"]
                if not channels or not isinstance(channels, list):
                    raise ValueError("no channels")
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError):
                self._drop_entry(path, "unreadable or malformed")
                continue

            if record.get("enqueued_at", 0) < cutoff:
                self._drop_entry(path, "stale")
                continue
            if metered and result["drained"] and sent_bytes + size > CELLULAR_DRAIN_MAX_BYTES:
                result["limited"] = True
                break

            send_result = self.send_uplink(channels, device_ts=record.get("device_ts"))
            if send_result["success"]:
                try:
                    os.unlink(path)
                except OSError:
                    pass
                result["drained"] += 1
                sent_bytes += size
                if record.get("device_ts") is not None:
                    result["drained_ts"].append(record["device_ts"])
                continue
            status = send_result.get("status_code")
            if isinstance(status, int) and 400 <= status < 500 and status not in _RETRY_STATUSES:
                # The server won't take this entry whatever we do: don't let it block the rest
                self._drop_entry(path, f"rejected by the server (HTTP {status})")
                continue
            logger.warning("Drain failed on %s: %s", fname, send_result.get("error"))
            result["failed"] = True
            result["failed_file"] = fname
            break

        return result

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

        if not self.trusted:
            # Plain http to an address that isn't the tailnet: whoever answers could
            # read the reading (with its position) and, by answering 2xx, make the
            # node drop it from its queue. Keep it queued until the server is trusted.
            if not self._untrusted_logged:
                logger.warning("Not uploading to %s: not https and not a tailnet address; "
                               "readings stay queued", self.server_url)
                self._untrusted_logged = True
            return {"success": False, "status_code": None, "response": None, "untrusted": True,
                    "error": "untrusted server (not https, not a tailnet address)", "attempts": 0}

        for attempt in range(1, self.retry_attempts + 1):
            try:
                sent_at = time.monotonic()
                resp = self._session.post(
                    url, json=payload, timeout=self.timeout_s, headers=self._auth_headers()
                )
                rtt_s = time.monotonic() - sent_at
                last_status = resp.status_code

                if resp.status_code in (200, 201):
                    try:
                        body = resp.json()
                    except ValueError:
                        body = None
                    if not _is_api_reply(body, self.device_id):
                        # A captive portal or another server answering 200 with its own
                        # page: not delivered, so the reading stays queued
                        logger.warning("IP uplink: HTTP %s but not the API's reply (%s); not delivered",
                                       resp.status_code, resp.text[:80].replace("\n", " "))
                        return _err_result("reply is not the API's (captive portal?)",
                                           status_code=resp.status_code, attempts=attempt)
                    logger.info(
                        "IP uplink OK (attempt %d/%d): %s",
                        attempt, self.retry_attempts, resp.status_code,
                    )
                    return {
                        "success": True,
                        "status_code": resp.status_code,
                        "response": body,
                        "server_date": resp.headers.get("Date") if self.trusted else None,
                        "rtt_s": rtt_s,
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
    """Module-level wrapper: create a transmitter, poll for the pending commands, then close."""
    tx = IPTransmitter(config_path=config_path)
    try:
        return tx.poll_downlink()
    finally:
        tx.close()


def _is_api_reply(body: Any, device_id: str) -> bool:
    """True for the API's /ip/uplink reply about this device (app/models.IPUplinkResponse)."""
    return isinstance(body, dict) and body.get("device_id") == device_id and "decoded" in body


def post_acks(acks: List[Dict[str, Any]], config_path: str = _DEFAULT_CONFIG_PATH) -> bool:
    """Module-level wrapper for IPTransmitter.post_acks."""
    tx = IPTransmitter(config_path=config_path)
    try:
        return tx.post_acks(acks)
    finally:
        tx.close()


def _pending_acks() -> List[Dict[str, Any]]:
    try:
        from tools import command_log
    except ImportError:
        import command_log
    return command_log.pending_acks()


def _clear_pending_acks(ids) -> None:
    try:
        from tools import command_log
    except ImportError:
        import command_log
    command_log.clear_pending_acks(ids)


# Downlink command codes and their value widths, as the server encodes them
# (API app/encoders.py). A command frame is [seq][code: 2 bytes][value]...,
# with no length byte; the same frames arrive over IP and LoRa, so this is the
# one table for both (tools/command_log.py: seq, acks).
DOWNLINK_CODE_LENGTHS: Dict[str, int] = {
    "10 90": 1,    # area threshold %
    "11 91": 2,    # stage threshold, cm (u16)
    "15 95": 7,    # wake schedule: start HH MM, end HH MM, interval u16 min, awake u8 min (tools/schedule_control.py)
    "13 93": 1,    # emergency capture interval, minutes (1-240)
    "14 94": 1,    # flood code interval, minutes (1-240)
    "16 96": 1,    # audio_recording_enabled
    "17 97": 1,    # IP upload in each of the next NN wakes, 00 cancels (tools/ip_fallback.py)
    "18 98": 1,    # remote debug session: NN x 10 min, 0 = end (docs/REMOTE_DEBUG_SESSION.md)
    "18 99": 1,    # the same session over WiFi only, never cellular
    "18 9A": 1,    # the same session over WiFi if a known network answers, else cellular
    "21 91": 1,    # emergency on for HH hours (00 = emergency_max_hours)
    "50 01": 0,    # status request: the ack is the node's status reply
    "19 99": 0,    # take a live GPS fix at the next wake (tools/gps_window.py)
    "19 9A": 0,    # accept the current orientation as the reference (tools/pose_monitor.py)
    "99 99": 0,    # emergency off: the same 0x99 0x99 as over LoRa
}

#: Accepted values of the interval commands, minutes
EMERGENCY_MINUTES = (1, 240)
FLOOD_CODE_MINUTES = (1, 240)


def parse_downlink_frame(data: bytes) -> List[Dict[str, str]] | None:
    """Split a raw server frame into parts for apply_downlink_command().

    Returns None unless the whole frame is a sequence of known two-byte codes,
    each followed by exactly its value width, so other payload formats are left
    to their own parsers.
    """
    parts: List[Dict[str, str]] = []
    i = 0
    while i < len(data):
        if i + 2 > len(data):
            return None
        code = f"{data[i]:02X} {data[i + 1]:02X}"   # upper case, as the tables (18 9A)
        width = DOWNLINK_CODE_LENGTHS.get(code)
        if width is None:
            return None
        value = data[i + 2:i + 2 + width]
        if len(value) != width:
            return None
        parts.append({"code": code, "payload_hex": value.hex()})
        i += 2 + width
    return parts or None


def parse_command_frame(frame: bytes) -> Optional[Tuple[int, Optional[List[Dict[str, str]]]]]:
    """(seq, parts) of a server command frame; parts None if the rest isn't a valid frame.

    None for an empty frame.
    """
    if not frame:
        return None
    return frame[0], parse_downlink_frame(bytes(frame[1:]))


def apply_command_frame(frame: bytes, apply_parts: Callable[[List[Dict[str, str]]], Dict[str, Any]]) -> Dict[str, Any]:
    """Apply one server command frame once, through apply_parts; what to ack.

    A frame applied before (tools/command_log.py) isn't applied again: the
    server resends until it has the ack, so it gets the same result again.
    apply_parts(parts) returns apply_downlink_command()'s dict.
    Returns {"seq", "result" (0 applied, 1 partly, 2 rejected), "duplicate",
    "applied", "skipped"}.
    """
    try:
        from tools import command_log
    except ImportError:
        import command_log
    frame = bytes(frame)
    prior = command_log.seen(frame)
    if prior is not None:
        print(f"📥 Command seq {frame[0]} already applied (result {prior}): acking again")
        return {"seq": frame[0], "result": prior, "duplicate": True, "applied": [], "skipped": []}
    parsed = parse_command_frame(frame)
    if parsed is None:
        return {"seq": 0, "result": command_log.REJECTED, "duplicate": False, "applied": [], "skipped": ["<empty>"]}
    seq, parts = parsed
    if not parts:
        print(f"⚠️ Command seq {seq}: not a valid frame ({frame.hex()}); rejected")
        result = {"applied": [], "skipped": ["<frame>"]}
    else:
        result = apply_parts(parts)
    code = command_log.APPLIED if not result["skipped"] else (
        command_log.PARTLY if result["applied"] else command_log.REJECTED)
    command_log.record(frame, code)
    return {"seq": seq, "result": code, "duplicate": False,
            "applied": result["applied"], "skipped": result["skipped"]}


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
        The frame's blocks as ``{"parts": [...]}`` (see ``parse_command_frame``).  Must have a ``"parts"``
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
    """
    if set_param_fn is None:
        try:
            from tools.lora_runtime_integration import set_parameter as set_param_fn
        except ImportError as exc:
            raise RuntimeError(
                "apply_downlink_command: set_param_fn not provided and "
                "tools.lora_runtime_integration is unavailable. "
                "Pass set_param_fn explicitly when calling outside TickTalkPython."
            ) from exc

    _expected_len = DOWNLINK_CODE_LENGTHS

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
        code = code_raw.strip().upper()   # codes with hex letters (18 9A) match either case
        if not code:
            logger.warning(
                "apply_downlink_command: missing or empty code in part %s — skipping", part
            )
            skipped.append("<missing_code>")
            continue

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

        elif code == "15 95":  # wake schedule: writes schedule.wpi and arms the next wake
            try:
                from tools import schedule_control
            except ImportError:
                import schedule_control
            try:
                ok, msg = schedule_control.apply(schedule_control.from_bytes(payload_bytes),
                                                 set_param=set_param_fn)
            except ValueError as e:
                ok, msg = False, f"schedule rejected: {e}"
            logger.info("%s", msg)
            (applied if ok else skipped).append(f"schedule={payload_bytes.hex()}" if ok else code)

        elif code in ("13 93", "14 94"):  # intervals, plain minutes
            name, (lo, hi) = {"13 93": ("emergency_frequency", EMERGENCY_MINUTES),
                              "14 94": ("neighborhood_emergency_frequency", FLOOD_CODE_MINUTES)}[code]
            mins = int.from_bytes(payload_bytes, "big")
            if lo <= mins <= hi and set_param_fn(name, mins) is not False:
                logger.info("Applied: %s = %dmin", name, mins)
                applied.append(f"{name}={mins}min")
            else:
                logger.warning("apply_downlink_command: %s %d min out of range (%d-%d)", name, mins, lo, hi)
                skipped.append(code)

        elif code == "16 96":  # audio_recording_enabled — direct u8 bool (0/1)
            val = bool(int.from_bytes(payload_bytes, "big"))
            set_param_fn("audio_recording_enabled", val)
            logger.info("Applied: audio_recording_enabled = %s", val)
            applied.append(f"audio_recording_enabled={val}")

        elif code == "17 97":  # IP upload for the next NN wakes, even with ip_upload off
            try:
                from tools import ip_fallback
            except ImportError:
                import ip_fallback
            done = ip_fallback.request(payload_bytes[0], "downlink")
            logger.info("Applied: IP upload request %d: %s", payload_bytes[0], done)
            applied.append(f"ip_upload_wakes={payload_bytes[0]}")

        elif code in ("18 98", "18 99", "18 9A"):  # remote access: cellular, WiFi only, WiFi then cellular
            # Run by remote-debug.timer, not here: starting a session stops
            # ticktalk, which is the process running this.
            try:
                from tools import remote_debug
            except ImportError:
                import remote_debug
            minutes = remote_debug.minutes_from_code(f"{payload_bytes[0]:02x}")
            connection = remote_debug.CODE_CONNECTION[code]
            remote_debug.request(minutes, "ip", connection=connection)
            logger.info("Queued: remote session (%s) %s", connection, f"{minutes} min" if minutes else "end")
            applied.append(f"remote_debug={minutes}" + ("" if connection == "cellular" else f":{connection}"))

        elif code == "21 91":  # emergency mode on for HH hours
            hours = payload_bytes[0]
            if hours:
                set_param_fn("emergency_max_hours", min(hours, 168))
            set_param_fn("emergency_mode", True)
            set_param_fn("emergency_since", time.time())   # the duration starts now
            logger.info("Applied: emergency_mode = True for %s h", hours or "default")
            applied.append(f"emergency_mode=True({hours or 'default'}h)")

        elif code == "50 01":  # status request: answered by the ack (its status reply)
            applied.append("status_request")

        elif code == "19 99":  # a live GPS fix at the next wake
            try:
                from tools import gps_window
            except ImportError:
                import gps_window
            gps_window.request()
            applied.append("gps_fix_requested")

        elif code == "19 9A":  # orientation reference reset, after re-georeferencing
            try:
                from tools import pose_monitor
            except ImportError:
                import pose_monitor
            pose_monitor.reset()
            applied.append("pose_reference_reset")

        elif code == "99 99":  # emergency mode off
            set_param_fn("emergency_mode", False)
            logger.info("Applied: emergency_mode = False")
            applied.append("emergency_mode=False")

        else:
            logger.warning("apply_downlink_command: unrecognised code '%s' — ignoring", code)
            skipped.append(code)

    return {
        "applied": applied,
        "skipped": skipped,
    }


def reply_commands(uplink_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The commands an /ip/uplink reply carried, as a poll_downlink()-style result.

    None when the reply has no `commands` key (the uplink didn't ask, or failed),
    so the caller polls instead.
    """
    body = uplink_result.get("response") if uplink_result.get("success") else None
    if not isinstance(body, dict) or not isinstance(body.get("commands"), list):
        return None
    return {"success": True, "commands": body["commands"]}


def handle_downlink_reply(
    poll_result: Dict[str, Any],
    set_param_fn: Callable[[str, Any], None] | None = None,
    label: str = "IP downlink",
    ack_fn: Callable[[List[Dict[str, Any]]], bool] | None = None,
) -> Dict[str, Any]:
    """Apply the commands in a poll_downlink() reply, ack them, and print what happened.

    Shared by the wake-start poll and the reply to each IP uplink in
    ``ticktalk_main.py``. Each frame is applied once (tools/command_log.py) and
    acked every time it is offered, so the server stops offering it. Acks go to
    ack_fn (default: POST /ip/ack, kept for the next uplink if that fails).
    """
    if not poll_result.get("success"):
        print(f"⚠️ {label} poll failed: {poll_result.get('error')}")
        return {"status": "poll_failed", "error": poll_result.get("error")}

    commands = [c for c in poll_result.get("commands") or [] if isinstance(c, dict)]
    if not commands:
        print(f"📬 {label}: no pending commands")
        return {"status": "no_command"}

    acks: List[Dict[str, Any]] = []
    applied_all: List[str] = []
    for cmd in commands:
        try:
            cid = int(cmd["id"])
            frame = bytes.fromhex(str(cmd["frame"]))
        except (KeyError, TypeError, ValueError):
            print(f"⚠️ {label}: malformed command {cmd!r}: ignored")
            continue
        r = apply_command_frame(frame, lambda parts: apply_downlink_command({"parts": parts},
                                                                            set_param_fn=set_param_fn))
        acks.append({"id": cid, "result": r["result"]})
        applied_all.extend(r["applied"])
        what = "already applied" if r["duplicate"] else (", ".join(r["applied"]) or "nothing applied")
        print(f"📬 {label}: command {cid} (seq {r['seq']}): {what}"
              + (f"; skipped {', '.join(r['skipped'])}" if r["skipped"] else ""))

    if acks:
        (ack_fn or post_acks)(acks)
    return {
        "status": "applied" if applied_all else "no_match",
        "acks": acks,
        "applied_params": applied_all,
    }


# ------------------------------------------------------------------ #
# Internal util                                                        #
# ------------------------------------------------------------------ #

def cellular_status(connection: str) -> Dict[str, bool]:
    """Whether the NetworkManager connection exists, is active, and autoconnects.

    Only a connection with autoconnect off is managed on demand; one that
    autoconnects is meant to stay up and is left alone.
    """
    status = {"exists": False, "active": False, "autoconnect": False}
    try:
        r = subprocess.run(
            ["nmcli", "-g", "connection.autoconnect,GENERAL.STATE", "connection", "show", connection],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("nmcli connection show %s failed: %s", connection, exc)
        return status
    if r.returncode != 0:
        return status
    lines = r.stdout.splitlines()
    status["exists"] = True
    status["autoconnect"] = bool(lines) and lines[0].strip() == "yes"
    status["active"] = len(lines) > 1 and lines[1].strip() == "activated"
    return status


def wifi_connected() -> bool:
    """True if a WiFi device is connected (NetworkManager prefers its route)."""
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "TYPE,STATE", "device"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(line == "wifi:connected" for line in r.stdout.splitlines())


def _nm_unescape(value: str) -> str:
    """Undo nmcli's terse-mode escaping of ':' and '\\'."""
    return value.replace("\\:", ":").replace("\\\\", "\\")


def wifi_in_range() -> bool:
    """True if a saved WiFi network shows up in NetworkManager's last scan.

    Reads the cached scan (no rescan), so it is quick and doesn't touch the radio.
    Out of range of every saved network, or with none saved, it is False, so a
    cellular-only unit never waits for WiFi.
    """
    def nm(*args):
        return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=10).stdout
    try:
        ssids = set()
        for line in nm("-t", "-f", "UUID,TYPE", "connection", "show").splitlines():
            uuid, _, kind = line.partition(":")
            if kind == "802-11-wireless":
                ssid = _nm_unescape(nm("-g", "802-11-wireless.ssid", "connection", "show", uuid).strip())
                if ssid:
                    ssids.add(ssid)
        if not ssids:
            return False
        seen = {_nm_unescape(x) for x in nm("-t", "-f", "SSID", "device", "wifi", "list", "--rescan", "no").splitlines()}
    except (OSError, subprocess.TimeoutExpired):
        return False
    return bool(ssids & seen)


def tailscale_online(host: str) -> Optional[bool]:
    """Whether the local Tailscale daemon is online and sees ``host`` online.

    Reads ``tailscale status --json``, which sends nothing over the network.
    None if the status can't be read or ``host`` isn't a peer.
    """
    try:
        r = subprocess.run(["tailscale", "status", "--json"],
                           capture_output=True, text=True, timeout=10)
        status = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(status, dict):
        return None
    for peer in (status.get("Peer") or {}).values():
        if (peer.get("DNSName") or "").rstrip(".").lower() == host:
            return bool((status.get("Self") or {}).get("Online")) and bool(peer.get("Online"))
    return None


SET_CLOCK = "/usr/local/sbin/watercam-set-clock"
CLOCK_TOLERANCE_S = 5
# A larger correction is only applied when the RTC is known to be bad
# (rtc_early_clock.py leaves RTC_UNTRUSTED) or the clock is before 2026: a
# server time a day or more off is more likely wrong than the RTC (#203).
MAX_CLOCK_STEP_S = 24 * 3600
RTC_UNTRUSTED = "/run/watercam-rtc-untrusted"
CLOCK_FLOOR = 1767225600              # 2026-01-01, as watercam-set-clock
# Set by syncTime.sh (network or GPS time at boot) or by SET_CLOCK; /run is cleared at boot
TIME_SOURCE = "/run/watercam-time-source"
# This boot's check, so it runs once: holds the boot id, so a /tmp that survives reboots is fine
CLOCK_CHECKED = "/tmp/watercam-clock-checked"


def _boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def correct_clock(uplink_result: Dict[str, Any]) -> Optional[float]:
    """Set the clock and RTC from the server's reply when they're off.

    A unit with no WiFi at boot gets no network time (cellular isn't up then)
    and, without a GPS fix, no GPS time either, so its RTC drifts and misses
    daylight-saving changes. The uplink reply's HTTP Date header (1 s
    resolution) fixes that once the upload has reached the server. Only an
    offset over CLOCK_TOLERANCE_S is corrected, through the root-owned
    SET_CLOCK helper and its doas rule. Returns the offset applied, or None.

    Runs at most once per boot, and not at all when the clock was already set
    from the network or GPS this boot (TIME_SOURCE). The Date header comes with
    the upload's own reply, so the check sends nothing extra either way.
    """
    from email.utils import parsedate_to_datetime
    date = (uplink_result or {}).get("server_date")
    if not date or not os.path.exists(SET_CLOCK) or os.path.exists(TIME_SOURCE):
        return None
    boot = _boot_id()
    try:
        with open(CLOCK_CHECKED) as f:
            if boot and f.read().strip() == boot:
                return None
    except OSError:
        pass
    try:
        with open(CLOCK_CHECKED, "w") as f:
            f.write(boot)
    except OSError:
        pass
    try:
        server = parsedate_to_datetime(date).timestamp() + (uplink_result.get("rtt_s") or 0) / 2
    except (TypeError, ValueError):
        return None
    offset = server - time.time()
    if abs(offset) > MAX_CLOCK_STEP_S and not os.path.exists(RTC_UNTRUSTED) and time.time() >= CLOCK_FLOOR:
        logger.warning("server time is %.1f h %s the clock: more than %d h, not applied "
                       "(the RTC looks valid; #203)", abs(offset) / 3600,
                       "ahead of" if offset > 0 else "behind", MAX_CLOCK_STEP_S // 3600)
        return None
    if abs(offset) <= CLOCK_TOLERANCE_S:
        from tools.capture_time import mark_clock_verified
        mark_clock_verified()      # the clock agrees with the server: later captures are fine
        return None
    try:
        r = subprocess.run(["doas", SET_CLOCK, str(int(round(server)))],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("clock correction failed: %s", exc)
        return None
    if r.returncode != 0:
        logger.warning("clock correction failed: %s", (r.stderr or r.stdout).strip())
        return None
    logger.warning("clock was %.0f s %s the server's; corrected (system and RTC)",
                   abs(offset), "behind" if offset > 0 else "ahead of")
    return offset


def cellular_up(connection: str, timeout_s: int = 90) -> bool:
    """Activate a NetworkManager connection (the LTE modem's); True once it is up.

    The connection should have autoconnect off, so the modem stays registered
    but carries no data until a wake has no other route to the server.

    USB may still be off (tools/usb_power.py). A modem that has just been
    powered takes 20-40 s to show up in NetworkManager; until then nmcli
    finds no device for the connection, so it is retried within timeout_s.
    """
    from tools.usb_power import acquire, wait_for
    end = time.monotonic() + timeout_s
    if acquire("cellular"):
        wait_for("/dev/cdc-wdm*", timeout_s)
    while True:
        wait = max(round(end - time.monotonic()), 5)
        try:
            r = subprocess.run(
                ["nmcli", "--wait", str(wait), "connection", "up", connection],
                capture_output=True, text=True, timeout=wait + 10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("nmcli connection up %s failed: %s", connection, exc)
            return False
        if r.returncode == 0:
            return True
        msg = (r.stderr or r.stdout).strip()
        if "no suitable device" in msg.lower() and time.monotonic() + 5 < end:
            time.sleep(5)
            continue
        logger.warning("nmcli connection up %s: %s", connection, msg)
        return False


def cellular_down(connection: str) -> None:
    """Deactivate the connection again and give USB back; never raises."""
    try:
        subprocess.run(["nmcli", "connection", "down", connection],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("nmcli connection down %s failed: %s", connection, exc)
    try:
        from tools.usb_power import release
        release("cellular")
    except Exception as exc:   # noqa: BLE001
        logger.warning("USB release after cellular failed: %s", exc)


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
