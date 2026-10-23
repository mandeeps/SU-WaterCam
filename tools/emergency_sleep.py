"""Emergency mode: sleep between captures instead of staying up.

Staying up between emergency captures costs 1.9-2.6 W; a boot and a shutdown
cost about 35-50 mWh together (docs/POWER_ANALYSIS.md). So any gap longer than
a minute or two is cheaper with the Pi off: at the default 10-min interval,
about 14 Wh a day instead of about 72, on a 72 Wh battery.

An emergency wake takes one capture and sends it (call_shutdown runs after the
uploads), picks up server commands, then arms the Witty Pi's startup alarm for
the next capture and shuts down. The next capture is this boot plus
emergency_frequency, so captures stay about one interval apart however long the
wake took; a wake that ran long still sleeps at least SLEEP_MIN_OFF_S.

The unit stays up and captures on the loop's period, as before, when:
- the interval is under MIN_SLEEP_INTERVAL_MIN (a boot costs more than it saves);
- a remote access session holds it up;
- emergency_sleep is false in runtime_config.json;
- the alarm can't be armed or doesn't read back (staying up is safe; a missed
  wake is not).

While the Pi is off the mDot keeps listening (Class C): a command sent then is
delivered at the next wake (API resends pending commands when the node is next
heard), at most one interval later. The emergency backstop
(tools/wittypi_control.py) still covers a hung run, and at shutdown
tools/ensure_next_wake.py keeps this earlier alarm rather than the schedule's
next slot.
"""
import os
import time
from typing import Any, Callable, Dict, Optional, Tuple

#: Off for less than this is not worth it, and the alarm must stay clear of
#: ensure_next_wake.MARGIN_S (120 s) after the shutdown (about 10 s)
SLEEP_MIN_OFF_S = 180
#: Below this interval a boot (about 45 s, 25-35 mWh) costs more than it saves
MIN_SLEEP_INTERVAL_MIN = 3


def boot_time(now: Optional[float] = None) -> Optional[float]:
    """When this boot started (from /proc/uptime), or None."""
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return (time.time() if now is None else now) - up


def plan(now: float, boot: Optional[float], interval_min: Any, enabled: bool = True,
         hold: bool = False) -> Tuple[Optional[float], str]:
    """(when to wake, or None to stay up; why). Pure."""
    if not enabled:
        return None, "emergency_sleep is off"
    if hold:
        return None, "a remote access session is active"
    try:
        interval_s = float(interval_min) * 60
    except (TypeError, ValueError):
        return None, f"emergency_frequency {interval_min!r} is not a number"
    if interval_s < MIN_SLEEP_INTERVAL_MIN * 60:
        return None, f"the {interval_s / 60:g} min interval is too short to sleep through"
    wake = (boot if boot is not None else now) + interval_s
    if wake < now + SLEEP_MIN_OFF_S:
        wake = now + SLEEP_MIN_OFF_S
    wake = float(int(wake) + 1)
    return wake, f"next capture at {time.strftime('%H:%M:%S', time.localtime(wake))}"


def _armed_startup(now: float) -> Optional[float]:
    """The startup alarm as the Witty Pi holds it."""
    from tools import ensure_next_wake as enw
    rb = enw.rb
    return enw.armed_startup([rb.i2c_get(r) for r in rb.ALARM1_REGS], now)


def arm(wake: float, set_startup: Optional[Callable[[float], bool]] = None,
        read_back: Optional[Callable[[float], Optional[float]]] = None) -> bool:
    """Arm the startup alarm for wake and check it reads back within a second."""
    if set_startup is None:
        from tools.wittypi_control import set_startup_at as set_startup
    read_back = read_back or _armed_startup
    try:
        if not set_startup(wake):
            return False
        armed = read_back(time.time())
    except Exception as e:   # noqa: BLE001 - any failure means stay up
        print(f"⚠️ Emergency sleep: could not arm the wake ({e})")
        return False
    return armed is not None and abs(armed - wake) < 1.5


def prepare(get_param: Optional[Callable[[str, Any], Any]] = None,
            downlink_check: Optional[Callable[[], Dict[str, Any]]] = None,
            remote_active: Optional[Callable[[], bool]] = None,
            arm_fn: Callable[[float], bool] = arm, clock: Callable[[], float] = time.time,
            boot: Optional[float] = None) -> Dict[str, Any]:
    """Decide what an emergency-mode iteration does next; arm the wake if sleeping.

    Returns {"action": "sleep" | "stay" | "continue", "why", "wake"}:
    sleep: the alarm is armed, shut down now; stay: keep capturing on the loop's
    period; continue: a command turned emergency mode off, so the normal
    iteration count decides.
    """
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    if downlink_check is None:
        from tools.last_downlink_check import check as downlink_check
    if remote_active is None:
        from tools.last_downlink_check import _remote_session_active as remote_active
    now = clock()
    boot = boot_time(now) if boot is None else boot

    wake, why = plan(now, boot, get_param('emergency_frequency', 10),
                     enabled=bool(get_param('emergency_sleep', True)), hold=remote_active())
    if wake is None:
        return {"action": "stay", "why": why, "wake": None}
    # Commands sent during this wake, before going dark for an interval. One can
    # end emergency mode or start a remote session
    try:
        downlink_check()
    except Exception as e:   # noqa: BLE001
        print(f"⚠️ Last downlink check failed: {e}")
    if not get_param('emergency_mode', False):
        return {"action": "continue", "why": "emergency mode was turned off", "wake": None}
    if remote_active():
        return {"action": "stay", "why": "a remote access session started", "wake": None}
    now = clock()                                  # the check can take a while
    if wake < now + SLEEP_MIN_OFF_S:
        wake = float(int(now + SLEEP_MIN_OFF_S) + 1)
    if not arm_fn(wake):
        return {"action": "stay", "why": "the wake alarm could not be armed", "wake": None}
    return {"action": "sleep", "why": why, "wake": wake}


def rearm_backstop_at_boot(get_param: Optional[Callable[[str, Any], Any]] = None,
                           arm_backstop: Optional[Callable[[Any, Any], Any]] = None) -> Optional[str]:
    """At runtime start in emergency mode, put the emergency backstop back.

    Each emergency wake is a boot between schedule slots, and runScript.sh has
    just armed the schedule's shutdown for the end of the current ON window,
    which can be a minute away. Before this, the backstop replaced it only
    after the first capture. Returns what it did, or None outside emergency mode.
    """
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    if not get_param('emergency_mode', False):
        return None
    if arm_backstop is None:
        from tools.wittypi_control import arm_emergency_backstop as arm_backstop
    deadline = arm_backstop(get_param('emergency_since', None), get_param('emergency_max_hours', 24))
    if deadline is None:
        return "Emergency mode: no time limit, shutdown alarm cleared"
    return f"Emergency mode: shutdown backstop set for {time.strftime('%Y-%m-%d %H:%M', time.localtime(deadline))}"


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tools.lora_runtime_integration import get_parameter
    w, reason = plan(time.time(), boot_time(), get_parameter('emergency_frequency', 10),
                     enabled=bool(get_parameter('emergency_sleep', True)))
    print("would sleep:" if w else "would stay up:", reason)
