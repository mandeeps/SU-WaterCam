#!/usr/bin/env python
"""
LoRa handler daemon.

Owns the physical LoRa serial port (/dev/ttyAMA5) for its entire lifetime and
accepts requests over a Unix domain socket, so every other process talks to
the mDot through this one daemon instead of constructing its own LoRaHandler.

Why this exists
----------------
TickTalk's own runtime spawns multiple separate OS processes to execute graph
nodes (confirmed via `ps` PPID chains on a real device, 2026-07-22) --
tools/lora_handler_concurrent.py's old per-process `_lora_handler` singleton
could not provide real cross-process exclusivity over the one physical serial
port, causing recurring "LoRa serial port already owned by another process"
conflicts. See docs/LORA_HANDLER_MULTIPROCESS_ISSUE.md for the full
investigation and the design this daemon implements.

This daemon is also the single place that handles *incoming* LoRa messages
(LoRaHandler.decode(), via start_listening()'s background thread), including
the safety-critical emergency-mode fast path: on emergency_mode changing, it
calls tools.wittypi_control.apply_emergency_schedule() directly and
in-process (no IPC round-trip) to clear/restore the WittyPi's hardware
shutdown schedule.

Protocol
--------
Client -> server (newline-terminated JSON):
    {"action": "is_joined", "params": {}}
    {"action": "queue_transmit", "params": {"data": {...}}}
    {"action": "queue_binary_transmit", "params": {"binary_data": "<hex or b64-tagged bytes>"}}
    {"action": "process_transmit_queue", "params": {}}
    {"action": "transmit", "params": {"content": ..., "max_retries": 2}}
    {"action": "get_queue_depth", "params": {}}
    {"action": "get_size_limit", "params": {}}

Server -> client (newline-terminated JSON):
    {"status": "ok", "result": ...}
    {"status": "error", "message": "..."}

Raw bytes values (e.g. a compressed flood bitmap inside a queue_transmit
data dict, or `content` for transmit()) are wrapped as {"__bytes_b64__": ...}
by tools.lora_handler_concurrent._jsonify_bytes()/_unjsonify_bytes() on both
ends, since JSON has no native bytes type.

Usage
-----
Run directly for testing (pass an explicit socket path under /tmp, because
/run/lora/ is created by systemd RuntimeDirectory and will not exist for a
non-root user running outside systemd):

    /home/pi/SU-WaterCam/venv/bin/python tools/lora_daemon.py \
        --socket /tmp/lora_test.sock

Or via systemd (see config/lora_daemon.service), which creates /run/lora/
automatically at daemon startup. The production socket path is
/run/lora/lora.sock (systemd RuntimeDirectory=lora creates and owns this
directory). tools/lora_handler_concurrent.py's get_lora_handler() connects to
this socket if it exists, and returns None otherwise.
"""

import argparse
import json
import logging
import os
import signal
import socket
import stat
import sys
import threading

# Running this script directly (`python tools/lora_daemon.py`, as systemd
# does) puts only this file's own directory (tools/) on sys.path, not the
# repo root -- so plain "import tools.xxx" would fail with ModuleNotFoundError
# even though it works fine from ticktalk_main.py (run from the repo root).
# Insert the repo root explicitly so the "tools." qualified imports below
# resolve to the SAME module instances (and singletons) as every other
# process, rather than accidentally installing bare/duplicate copies -- the
# exact bare-vs-qualified import bug already fixed twice elsewhere in this
# codebase (tools/lora_runtime_integration.py, tools/debug_status_command.py).
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from tools.lora_handler_concurrent import (
    LORA_DAEMON_SOCKET_PATH,
    LoRaSerialPortConflict,
    _jsonify_bytes,
    _unjsonify_bytes,
    create_lora_handler_with_retry,
)

LOG_FORMAT = "%(asctime)s [lora_daemon] %(levelname)s: %(message)s"
MAX_REQUEST_BYTES = 16384

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RPC dispatch
# ---------------------------------------------------------------------------

def _do_is_joined(handler, params):
    return handler.is_joined()


def _do_queue_transmit(handler, params):
    return handler.queue_transmit(params["data"])


def _do_queue_binary_transmit(handler, params):
    return handler.queue_binary_transmit(params["binary_data"])


def _do_process_transmit_queue(handler, params):
    handler.process_transmit_queue()
    return None


def _do_transmit(handler, params):
    return handler.transmit(params["content"], params.get("max_retries", 2))


def _do_get_queue_depth(handler, params):
    return handler.get_queue_depth()


def _do_get_size_limit(handler, params):
    return handler.get_size_limit()


ACTIONS = {
    "is_joined": _do_is_joined,
    "queue_transmit": _do_queue_transmit,
    "queue_binary_transmit": _do_queue_binary_transmit,
    "process_transmit_queue": _do_process_transmit_queue,
    "transmit": _do_transmit,
    "get_queue_depth": _do_get_queue_depth,
    "get_size_limit": _do_get_size_limit,
}


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

def handle_connection(conn: socket.socket, handler) -> None:
    try:
        # transmit()/process_transmit_queue() can legitimately block on real
        # mDot retries for tens of seconds; give the connection a generous
        # budget rather than timing out mid-transmission.
        conn.settimeout(120)
        data = bytearray()
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_REQUEST_BYTES:
                raise ValueError(f"Request exceeded {MAX_REQUEST_BYTES} bytes")
            if b"\n" in data:
                break

        req = json.loads(data.split(b"\n", 1)[0].strip())
        action = req.get("action")
        params = _unjsonify_bytes(req.get("params", {}))

        fn = ACTIONS.get(action)
        if fn is None:
            raise ValueError(f"Unknown action: {action!r}")

        result = fn(handler, params)
        resp = json.dumps({"status": "ok", "result": _jsonify_bytes(result)}) + "\n"
        conn.sendall(resp.encode())

    except Exception as exc:
        log.exception("Request failed: %s", exc)
        try:
            resp = json.dumps({"status": "error", "message": str(exc)}) + "\n"
            conn.sendall(resp.encode())
        except Exception:
            pass
    finally:
        conn.close()


def serve(handler, socket_path: str) -> None:
    if os.path.exists(socket_path):
        # lstat avoids following a symlink planted in /tmp by another user.
        if not stat.S_ISSOCK(os.lstat(socket_path).st_mode):
            log.error("Path %s exists but is not a socket — refusing to unlink", socket_path)
            sys.exit(1)
        os.unlink(socket_path)

    parent = os.path.dirname(socket_path)
    if parent:
        try:
            os.makedirs(parent, mode=0o750, exist_ok=True)
        except PermissionError:
            log.error(
                "Cannot create socket directory %s — likely running outside systemd "
                "as a non-root user. Pass --socket /tmp/lora_test.sock for manual testing.",
                parent,
            )
            sys.exit(1)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # Unix sockets use 0o777 as base mode; umask 0o177 -> 0o777 & ~0o177 = 0o600
    # (owner-only), since only the ticktalk process (same uid) should connect.
    old_umask = os.umask(0o177)
    try:
        server.bind(socket_path)
    finally:
        os.umask(old_umask)
    server.listen(8)
    log.info("Listening on %s", socket_path)

    def _shutdown(sig, frame):
        log.info("Received signal %s, shutting down", sig)
        server.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        try:
            handler.stop_listening()
            handler.close()
        except Exception:
            log.exception("Error during handler shutdown")
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT, _shutdown)
    except ValueError:
        # Signal handlers can only be registered in the main thread of the
        # main interpreter -- true for the real daemon (serve() runs at
        # top level), but not when a test harness runs serve() in a
        # background thread. Tests own process/thread lifecycle themselves
        # in that case, so skipping registration here is safe.
        pass

    # Thread-per-connection: LoRaHandler's own transmit_lock + inter-process
    # fcntl lock already serialize the real hardware operations correctly
    # (unchanged from before this daemon existed); concurrency here only
    # keeps fast, lock-free RPCs (queue_transmit, get_queue_depth, ...) from
    # queuing behind a slow in-flight transmit()/process_transmit_queue()
    # purely due to a serial accept loop, not real hardware contention.
    while True:
        try:
            conn, _ = server.accept()
        except OSError:
            break
        threading.Thread(
            target=handle_connection, args=(conn, handler), daemon=True
        ).start()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="LoRa handler daemon")
    p.add_argument(
        "--socket",
        default=LORA_DAEMON_SOCKET_PATH,
        help=f"Unix socket path (default: {LORA_DAEMON_SOCKET_PATH})",
    )
    return p.parse_args()


def main():
    args = parse_args()

    handler = create_lora_handler_with_retry()
    if handler is None:
        log.error(
            "Could not acquire the LoRa serial port after retrying "
            "(LoRaSerialPortConflict) — is another process still holding it? Exiting."
        )
        sys.exit(1)

    # Construct this process's LoRaRuntimeManager around the real handler and
    # publish it as the process-wide singleton, so _listen_loop()'s fast-path
    # set_parameter() calls (module-level, always routed through
    # get_runtime_manager()) reach this exact instance -- required for the
    # emergency-mode callback registered below to actually fire.
    import tools.lora_runtime_integration as lri

    manager = lri.LoRaRuntimeManager(lora_handler=handler)
    lri.set_runtime_manager(manager)

    from tools.wittypi_control import apply_emergency_schedule

    def _on_emergency_mode_changed(value, old_value):
        try:
            result = apply_emergency_schedule(value)
            log.info("Emergency schedule updated: %s", result.get("message"))
        except Exception:
            log.exception("Failed to apply emergency schedule")

    manager.register_update_callback("emergency_mode", _on_emergency_mode_changed)

    serve(handler, args.socket)


if __name__ == "__main__":
    main()
