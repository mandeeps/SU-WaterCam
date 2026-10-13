#!/home/pi/SU-WaterCam/venv/bin/python
"""The unit's fixed (mount) position, and whether it has moved away from it.

A unit is installed in one place, so a live GPS fix well away from it means the
unit was taken or came off its mount. The position is learned, not configured:
after installation, the first HOME_FIXES live fixes that agree within
HOME_SPREAD_M become the home position (gps_home.json, mode 600).

check_movement() is called with every GPS read. A live fix more than
gps_move_alert_m (runtime parameter, default 50 m) from home, and beyond the
fix's own error, counts as away; two in a row set the unit to moved, and two
in a row back within the limit clear it. The state persists across wakes, so
while a unit is moved every uplink carries the alert (01 06) with the best
position available: the live fix, or the cached one when there is none.

At a new installation site, reset it so the new position is learned:
    python tools/gps_home.py --reset
    python tools/gps_home.py --show
"""
import json
import math
import os
import sys
import tempfile
import time
from typing import Optional

HOME_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gps_home.json")
DEFAULT_ALERT_M = 50.0
HOME_FIXES = 3          # live fixes that must agree before they become home
HOME_SPREAD_M = 25.0    # ... within this distance of their mean
CONFIRM = 2             # consecutive live fixes to set or clear "moved"
CONFIRM_GAP_S = 30      # ... at least this far apart, so a glitch lasting seconds doesn't count


def distance_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _alert_m() -> float:
    try:
        from tools.lora_runtime_integration import get_parameter
        return max(1.0, float(get_parameter('gps_move_alert_m', DEFAULT_ALERT_M)))
    except Exception:
        return DEFAULT_ALERT_M


def load() -> dict:
    try:
        with open(HOME_PATH) as f:
            state = json.load(f)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def _save(state: dict) -> None:
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(HOME_PATH), prefix=".gps_home.")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.chmod(tmp, 0o600)   # it says where the unit is
        os.replace(tmp, HOME_PATH)
    except OSError as e:
        print(f"⚠️ GPS home: couldn't save: {e}")


def _learn(state: dict, lat: float, lon: float) -> None:
    cands = state.setdefault("candidates", [])
    cands.append([lat, lon])
    del cands[:-HOME_FIXES]
    if len(cands) < HOME_FIXES:
        return
    mlat = sum(c[0] for c in cands) / len(cands)
    mlon = sum(c[1] for c in cands) / len(cands)
    if all(distance_m(mlat, mlon, c[0], c[1]) <= HOME_SPREAD_M for c in cands):
        state.update(home=[mlat, mlon], home_set=time.time(), moved=False, away=0, back=0)
        state.pop("candidates", None)
        print(f"📍 GPS home position learned from {HOME_FIXES} fixes")


def check_movement(location: dict, live: bool, error_m: Optional[float] = None) -> dict:
    """Update the moved state with this fix; return
    {'moved': bool, 'distance_m': float or None, 'home_known': bool}."""
    state = load()
    lat, lon = (location or {}).get("gps_lat"), (location or {}).get("gps_lon")
    have = isinstance(lat, (int, float)) and isinstance(lon, (int, float))
    home = state.get("home")
    dist = distance_m(home[0], home[1], lat, lon) if (home and have) else None
    if live and have:
        if not home:
            _learn(state, lat, lon)
        else:
            limit = max(_alert_m(), 3 * (error_m or 0))
            now = time.time()
            if dist > limit:
                if now - state.get("away_t", 0) >= CONFIRM_GAP_S:
                    state["away"], state["away_t"] = state.get("away", 0) + 1, now
                state["back"] = 0
                if not state.get("moved") and state["away"] >= CONFIRM:
                    state.update(moved=True, moved_since=time.time())
                    print(f"🚨 GPS: unit moved {dist:.0f} m from its mount position")
            else:
                if now - state.get("back_t", 0) >= CONFIRM_GAP_S:
                    state["back"], state["back_t"] = state.get("back", 0) + 1, now
                state["away"] = 0
                if state.get("moved") and state["back"] >= CONFIRM:
                    state["moved"] = False
                    state.pop("moved_since", None)
                    print("📍 GPS: unit back at its mount position")
        _save(state)
    return {"moved": bool(state.get("moved")), "distance_m": dist, "home_known": bool(state.get("home"))}


def movement_fields(location: dict) -> tuple:
    """Check this GPS read against home; return (LoRa fields, info).

    LoRa fields: movement_threshold (01 06, 1 while moved) and, while moved,
    lat_lon_z (04 01) with the best position available. info adds distance_m
    for the IP uplink."""
    from tools import get_gps
    lf = get_gps.last_fix
    info = check_movement(location, live=lf.get("source") == "live", error_m=lf.get("error_m"))
    fields = {"movement_threshold": 1 if info["moved"] else 0}
    if info["moved"] and location and location.get("gps_lat") is not None:
        fields["lat_lon_z"] = [location["gps_lat"], location["gps_lon"], location.get("gps_alt") or 0.0]
    return fields, info


def main(argv) -> int:
    if "--reset" in argv:
        try:
            os.remove(HOME_PATH)
        except FileNotFoundError:
            pass
        print("GPS home cleared: the next consistent live fixes become the new mount position")
        return 0
    state = load()
    if not state.get("home"):
        print(f"No home position yet ({len(state.get('candidates', []))}/{HOME_FIXES} fixes collected)")
        return 0
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(state.get("home_set", 0)))
    print(f"Home position set {when}; moved: {bool(state.get('moved'))}")
    if "--show" in argv:
        print(f"Home: {state['home'][0]:.6f}, {state['home'][1]:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
