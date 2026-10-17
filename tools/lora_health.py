"""Is LoRa actually working here, not just joined?

The mDot's joined flag (AT+NJS) is its saved session: it stays 1 with no
gateway in range, so a unit trusting it would skip IP and lose every reading.
assess() asks the network instead, once per boot: a link check (AT+NLC) the
network server must answer, reporting the signal margin and how many gateways
heard the node.

LoRa counts as healthy when:
- the mDot is joined,
- a link check this boot got an answer (two tries, 5 s apart) from at least one
  gateway with lora_min_margin_db (default 5 dB) above the demodulation floor,
- and at most one of the last LINK_HISTORY checks, across wakes, failed, so a
  link that comes and goes doesn't count as reliable.

The result decides whether WiFi stays off (tools/wifi_policy.py) and whether the
IP uplink is needed (ip_uplink_transmit). It's kept for the rest of the boot in
/tmp and the check history in data/lora_health.json.
"""
import json
import os
import time
from typing import Any, Dict, Optional

try:
    from tools import config_io
except ImportError:     # run from tools/ as a script
    import config_io

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_PATH = os.path.join(REPO, "data", "lora_health.json")
CACHE_PATH = "/tmp/watercam-lora-health.json"
LINK_HISTORY = 3
MAX_FAILURES_IN_HISTORY = 1
DEFAULT_MIN_MARGIN_DB = 5
ATTEMPTS = 2
RETRY_GAP_S = 5


def _boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return ""


def _read(path: str) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path: str, data: Any) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        config_io.write_json(path, data, mode=0o644)    # atomic and fsynced
    except (OSError, TypeError, ValueError) as e:
        print(f"⚠️ LoRa health: couldn't save {path}: {e}")


def _min_margin() -> float:
    try:
        from tools.lora_runtime_integration import get_parameter
        return float(get_parameter('lora_min_margin_db', DEFAULT_MIN_MARGIN_DB))
    except Exception:
        return DEFAULT_MIN_MARGIN_DB


def cached() -> Optional[Dict[str, Any]]:
    """This boot's assessment, if one was made."""
    c = _read(CACHE_PATH)
    if isinstance(c, dict) and c.get("boot_id") == _boot_id():
        return c.get("result")
    return None


def assess(handler=None, force: bool = False) -> Dict[str, Any]:
    """{'healthy': bool, 'reason': str, 'joined', 'margin_db', 'gateways'}; once per boot."""
    if not force:
        c = cached()
        if c is not None:
            return c
    result = {"healthy": False, "reason": "", "joined": False, "margin_db": None, "gateways": 0,
              "checked_at": time.time()}
    try:
        if handler is None:
            from tools.lora_handler_concurrent import get_lora_handler
            handler = get_lora_handler()
        if handler is None:
            result["reason"] = "LoRa daemon not running"
        elif not handler.is_joined():
            result["reason"] = "not joined"
        else:
            result["joined"] = True
            lc = {"ok": False, "error": "not run"}
            for attempt in range(ATTEMPTS):
                lc = handler.link_check()
                if lc.get("ok"):
                    break
                if attempt < ATTEMPTS - 1:
                    time.sleep(RETRY_GAP_S)
            result.update(margin_db=lc.get("margin_db"), gateways=lc.get("gateways") or 0)
            ok_now = bool(lc.get("ok")) and (lc.get("margin_db") or 0) >= _min_margin()
            hist = _read(HISTORY_PATH)
            hist = hist if isinstance(hist, list) else []
            hist.append({"t": int(time.time()), "ok": ok_now, "margin_db": lc.get("margin_db"),
                         "gateways": lc.get("gateways") or 0})
            hist = hist[-20:]
            _write(HISTORY_PATH, hist)
            recent_failures = sum(1 for h in hist[-LINK_HISTORY:] if not h.get("ok"))
            if not lc.get("ok"):
                result["reason"] = f"link check failed: {lc.get('error') or 'no gateway answered'}"
            elif not ok_now:
                result["reason"] = f"weak link: {lc.get('margin_db')} dB margin (< {_min_margin():g})"
            elif recent_failures > MAX_FAILURES_IN_HISTORY:
                result["reason"] = f"unreliable: {recent_failures} of the last {LINK_HISTORY} link checks failed"
            else:
                result["healthy"] = True
                result["reason"] = f"{lc.get('margin_db')} dB margin, {lc.get('gateways')} gateway(s)"
    except Exception as e:
        result["reason"] = f"check failed: {e}"
    print(f"📡 LoRa {'healthy' if result['healthy'] else 'NOT healthy'}: {result['reason']}")
    _write(CACHE_PATH, {"boot_id": _boot_id(), "result": result})
    return result
