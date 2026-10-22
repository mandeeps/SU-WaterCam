"""One last look for server commands before the cycle shuts the node down.

The wake-start poll often finds the server unreachable (the network isn't up
yet) and the poll after an IP uplink only runs when a reading went out over IP.
By the end of the monitoring work the connection is up, so a short check here
picks up a command sent during the cycle (from the dashboard, or queued by the
gauge triggers) instead of leaving it until the next wake, hours away on a
daylight schedule. It also shows the node still had a path to the server at
the end of its wake.

Only IP needs polling: LoRa downlinks reach the mDot in Class C as soon as they
are sent and the LoRa daemon applies them while the node is up. This never
brings a connection up: an unreachable server is a skip, not a wait.

A command can change what the shutdown should do: emergency mode on (21 91)
keeps the unit capturing, and a remote access session (18 98/99/9A) keeps it
up. check() reports that, so call_shutdown() doesn't power off under them.
"""
import json
import os
from typing import Any, Callable, Dict, Optional

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTE_STATE = os.path.join(REPO, "data", "remote_debug.json")
MAX_COMMANDS = 2        # polls: one gets them all; a second catches one sent meanwhile
REACH_TIMEOUT_S = 2


def _remote_session_active() -> bool:
    try:
        with open(REMOTE_STATE) as f:
            return bool(json.load(f).get("active"))
    except (OSError, ValueError):
        return False


def _emergency_on(get_param: Callable[[str, Any], Any]) -> bool:
    try:
        return bool(get_param("emergency_mode", False))
    except Exception:
        return False


def check(set_param_fn: Optional[Callable[[str, Any], None]] = None,
          get_param_fn: Optional[Callable[[str, Any], Any]] = None,
          transmitter=None) -> Dict[str, Any]:
    """Poll (up to MAX_COMMANDS times) and apply the pending commands; never raises.

    Returns {"status", "applied": [...], "keep_awake": bool, "why": str}.
    status: "disabled" (IP upload off), "unreachable", "no_command", "applied",
    "error".
    """
    out: Dict[str, Any] = {"status": "no_command", "applied": [], "keep_awake": False, "why": ""}
    try:
        from tools.transmit_ip import IPTransmitter, handle_downlink_reply
        if set_param_fn is None or get_param_fn is None:
            from tools.lora_runtime_integration import get_parameter, set_parameter
            set_param_fn = set_param_fn or set_parameter
            get_param_fn = get_param_fn or get_parameter
        tx = transmitter or IPTransmitter()
        try:
            if not tx.enabled:
                out["status"] = "disabled"
            elif not tx.is_reachable(timeout_s=REACH_TIMEOUT_S):
                out["status"] = "unreachable"
                print("📭 Last downlink check: server unreachable, skipped")
            else:
                for _ in range(MAX_COMMANDS):
                    reply = handle_downlink_reply(tx.poll_downlink(), set_param_fn=set_param_fn,
                                                  label="Last downlink check", ack_fn=tx.post_acks)
                    if reply.get("status") in ("no_command", "poll_failed"):
                        if reply.get("status") == "poll_failed" and not out["applied"]:
                            out["status"] = "error"
                        break
                    out["status"] = "applied"
                    out["applied"].extend(reply.get("applied_params") or [])
        finally:
            if transmitter is None:
                tx.close()
    except Exception as e:
        print(f"⚠️ Last downlink check failed: {e}")
        out["status"] = "error"

    if _emergency_on(get_param_fn or (lambda k, d=None: d)):
        out.update(keep_awake=True, why="emergency mode is on")
    elif _remote_session_active():
        out.update(keep_awake=True, why="a remote access session is active")
    return out
