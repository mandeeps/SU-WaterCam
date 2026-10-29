"""The wake schedule, set from the dashboard (downlink 15 95).

A schedule is a daily window, an interval and an awake time, in the site's
local time:

    15 95  [start HH][start MM][end HH][end MM][interval u16 min][awake u8 min]

Wakes are start, start + interval, ... while a wake begins before end;
start == end means all day, and end < start means the window crosses midnight.
`awake` is the most the Pi stays up per wake (it shuts down earlier when its
cycle ends).

Start and end can follow the sun instead: an hour byte of 0xF0 means sunrise
and 0xF1 sunset, and the minute byte is then a signed offset in minutes
(-120..120), e.g. sunrise+30 to sunset-15. The node computes the day's
sunrise and sunset itself (tools/sun.py) from its learned home position, else
its last GPS fix; with neither it uses FALLBACK_WINDOW until it has one. The
window is checked at every start of ticktalk and the schedule rewritten once it
has moved SUN_REWRITE_MIN or more. A sun-following start is rounded to the
nearest point of the interval's clock grid (on_grid): SR+30 at a 07:25 sunrise
with hourly wakes starts at 08:00, not 07:55, and at a 07:32 sunrise also at
08:00 (28 min after sunrise). A sunrise start is never earlier than
MIN_AFTER_SUNRISE (20 min) after sunrise, so it doesn't wake at first light:
SR+30 at a 07:50 sunrise would round to 08:00, only 10 min after sunrise, so it
starts at 09:00.

The node turns it into ~/wittypi/schedule.wpi: one day of ON/OFF durations from
BEGIN, as config/wittypi/*.wpi do, and arms the Witty Pi's startup alarm for the
next wake with the same code the shutdown path uses (tools/recovery_boot.py),
as `pi`: no root needed. The Witty Pi daemon reads the new file at the next
boot. If the file can't be written or the alarm armed, the previous schedule
is put back and the command is rejected.

The Witty Pi counts seconds from BEGIN, so a schedule written in winter wakes
an hour late in summer. The schedule therefore records the UTC offset it was
written with, and refresh() (run at every start of ticktalk) rewrites it when
the offset has changed, so wakes stay at local times all year.

The active schedule is in runtime_config.json (`schedule`), reported in sensor
packets (09 79, same 7 bytes) and in the status reply (`sc`).
"""
import math
import os
import struct
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from tools import recovery_boot as rb   # noqa: E402
from tools import sun                   # noqa: E402

INTERVAL_MIN = (15, 1440)
AWAKE_MIN = (5, 60)
DAY = 1440
END_YEARS = 10                 # END of the generated script, years from now
#: Don't arm a wake sooner than this from now: the Pi must be down first
ARM_MARGIN_S = 120
SUNRISE, SUNSET = 0xF0, 0xF1
OFFSET_MIN = (-120, 120)
#: A sun-relative window with no location known yet: 08:00-16:15
FALLBACK_WINDOW = (8 * 60, 16 * 60 + 15)
#: Rewrite the schedule when the sun window has moved this much since it was written.
#: Sunrise moves at most about 2 min a day, so this is a rewrite every few days
#: around the equinoxes and rarely near the solstices.
SUN_REWRITE_MIN = 10
#: A sunrise start is never earlier than this after sunrise (the rounding to the hour
#: could otherwise put it at first light). With SR+30 the first wake falls 20-80 min
#: after sunrise, about 30 on average. It doesn't protect a drained pack: on an overcast
#: winter morning one wake's charge takes 1-2 h; the recovery boot covers that
#: (tools/recovery_boot.py: after power returns, the next slot at least 2 h away)
MIN_AFTER_SUNRISE = 20
_REFS = {"clock": None, "sunrise": SUNRISE, "sunset": SUNSET}


@dataclass(frozen=True)
class Schedule:
    start: int        # minutes after midnight, local time; or the offset from start_ref
    end: int
    interval: int     # minutes
    awake: int        # minutes
    start_ref: str = "clock"     # clock, sunrise, sunset
    end_ref: str = "clock"

    @property
    def follows_sun(self) -> bool:
        return self.start_ref != "clock" or self.end_ref != "clock"

    def to_bytes(self) -> bytes:
        def point(ref, v):
            if ref == "clock":
                return bytes([v // 60, v % 60])
            return bytes([_REFS[ref], v & 0xFF])
        return point(self.start_ref, self.start) + point(self.end_ref, self.end) + \
            struct.pack(">HB", self.interval, self.awake)

    def label(self) -> str:
        """"HHMM-HHMM/interval/awake" ("SR+30-SS-15/..." when it follows the sun), as `sc` carries it."""
        def point(ref, v):
            if ref == "clock":
                return f"{v // 60:02d}{v % 60:02d}"
            return f"{'SR' if ref == 'sunrise' else 'SS'}{v:+d}"
        return f"{point(self.start_ref, self.start)}-{point(self.end_ref, self.end)}/{self.interval}/{self.awake}"

    def as_dict(self) -> Dict[str, int]:
        return {"start": self.start, "end": self.end, "interval": self.interval, "awake": self.awake,
                "start_ref": self.start_ref, "end_ref": self.end_ref}


def _point(hh: int, mm: int, what: str) -> Tuple[str, int]:
    if hh in (SUNRISE, SUNSET):
        off = mm - 256 if mm > 127 else mm
        if not OFFSET_MIN[0] <= off <= OFFSET_MIN[1]:
            raise ValueError(f"{what} offset must be {OFFSET_MIN[0]}..{OFFSET_MIN[1]} min")
        return ("sunrise" if hh == SUNRISE else "sunset"), off
    if hh > 23 or mm > 59:
        raise ValueError(f"{what} must be a time of day (00:00-23:59), sunrise or sunset")
    return "clock", hh * 60 + mm


def from_bytes(value: bytes) -> Schedule:
    if len(value) != 7:
        raise ValueError(f"a schedule is 7 bytes, got {len(value)}")
    start_ref, start = _point(value[0], value[1], "start")
    end_ref, end = _point(value[2], value[3], "end")
    interval, awake = struct.unpack(">HB", value[4:])
    return Schedule(start, end, interval, awake, start_ref, end_ref)


def location() -> Optional[Tuple[float, float]]:
    """(lat, lon) for the sun times: the learned home, else the last GPS fix."""
    try:
        from tools import gps_home
        home = gps_home.load().get("home")
        if home and len(home) >= 2:
            return float(home[0]), float(home[1])
    except Exception:
        pass
    try:
        from tools import get_gps
        import json
        with open(get_gps.CACHE_PATH) as f:
            c = json.load(f)
        return float(c["gps_lat"]), float(c["gps_lon"])
    except Exception:
        return None


def on_grid(minute: int, interval: int, not_before: Optional[int] = None) -> int:
    """minute rounded to the nearest point of the clock grid the interval runs on:
    the hour for hourly wakes, :00 and :30 for 30 or 90 minutes (the gcd with 60).
    A sun-following start is rounded this way, so the wakes fall on round times
    (08:00, 09:00, ...) instead of sunrise + offset (07:55, 08:55, ...), within
    half a grid step of it. not_before (minutes after midnight) moves it on by
    whole steps until it is at or after that time: sunrise + MIN_AFTER_SUNRISE."""
    g = math.gcd(int(interval), 60) or 60
    t = (int(minute) + g // 2) // g * g          # halves round up
    if not_before is not None:
        # signed minutes from not_before, across midnight
        d = (t - int(not_before) + DAY // 2) % DAY - DAY // 2
        if d < 0:
            t += -(-(-d) // g) * g
    return t % DAY


def resolve(s: Schedule, day, where: Optional[Tuple[float, float]]) -> Tuple[Schedule, str]:
    """(the schedule in clock times for `day`, how the window was found)."""
    if not s.follows_sun:
        return s, "clock times"
    times = sun.sun_times(day, *where) if where else None
    if times is None:
        start, end = FALLBACK_WINDOW
        why = "no location yet" if not where else "no sunrise or sunset that day"
        return Schedule(start, end, s.interval, s.awake), f"{why}: fallback window"
    rise, sset = (t.hour * 60 + t.minute for t in times)

    def at(ref, v):
        if ref == "clock":
            return v
        return ((rise if ref == "sunrise" else sset) + v) % DAY
    start = at(s.start_ref, s.start)
    if s.start_ref != "clock":
        # a sunrise start leaves the battery some charging time first
        start = on_grid(start, s.interval,
                        not_before=(rise + MIN_AFTER_SUNRISE) % DAY if s.start_ref == "sunrise" else None)
    return Schedule(start, at(s.end_ref, s.end), s.interval, s.awake), \
        f"sunrise {rise // 60:02d}:{rise % 60:02d}, sunset {sset // 60:02d}:{sset % 60:02d}"


def wakes(s: Schedule) -> List[int]:
    """Wake times as minutes after `start` (0, interval, ...), validated.

    Raises ValueError for a schedule the node won't run.
    """
    if not INTERVAL_MIN[0] <= s.interval <= INTERVAL_MIN[1]:
        raise ValueError(f"interval must be {INTERVAL_MIN[0]}-{INTERVAL_MIN[1]} min")
    if not AWAKE_MIN[0] <= s.awake <= AWAKE_MIN[1] or s.awake >= s.interval:
        raise ValueError(f"awake must be {AWAKE_MIN[0]}-{AWAKE_MIN[1]} min and shorter than the interval")
    window = (s.end - s.start) % DAY or DAY          # start == end: all day
    offsets = list(range(0, window, s.interval))
    # The last wake's off time runs to the next day's first wake; it must be real.
    # All day, the last wake is dropped instead (as the API does, app/schedule.py)
    if DAY - offsets[-1] - s.awake <= 0:
        if window == DAY and len(offsets) > 1:
            offsets.pop()
        else:
            raise ValueError("the last wake would run into the next day's first wake")
    return offsets


def _duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return " ".join(p for p in (f"H{h}" if h else "", f"M{m}" if m else "") if p)


def generate(s: Schedule, now: datetime, label: Optional[str] = None) -> str:
    """schedule.wpi text: BEGIN at today's first wake, one day of ON/OFF states."""
    offsets = wakes(s)
    begin = now.replace(hour=s.start // 60, minute=s.start % 60, second=0, microsecond=0)
    lines = [f"# WaterCam schedule {label or s.label()} (start-end/interval/awake, local time)"
             + (f", today {s.label()}" if label and label != s.label() else "") + ",",
             f"# written {now:%Y-%m-%d %H:%M} by tools/schedule_control.py from downlink 15 95.",
             f"# UTC offset {_offset_min(now)} min; rewritten when it changes (DST).",
             f"BEGIN {begin:%Y-%m-%d %H:%M:%S}",
             f"END   {begin + timedelta(days=365 * END_YEARS):%Y-%m-%d %H:%M:%S}"]
    for i, o in enumerate(offsets):
        nxt = offsets[i + 1] if i + 1 < len(offsets) else DAY
        lines.append(f"ON    {_duration(s.awake)}")
        lines.append(f"OFF   {_duration(nxt - o - s.awake)}")
    return "\n".join(lines) + "\n"


def _offset_min(now: datetime) -> int:
    off = now.astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off is not None else 0


def _write_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def apply(s: Schedule, wittypi_dir: str = rb.WITTYPI_DIR, now: Optional[datetime] = None,
          arm=None, set_param=None, clock=time.time,
          where: Optional[Tuple[float, float]] = None) -> Tuple[bool, str]:
    """Write schedule.wpi for `s` and arm the next wake; (applied, message).

    The previous schedule.wpi is kept as schedule.wpi.prev and put back if
    anything fails, so a bad command never leaves the unit without a schedule.
    """
    now = now or datetime.now()
    try:
        if s.follows_sun:
            for ref, v in ((s.start_ref, s.start), (s.end_ref, s.end)):
                if ref != "clock" and not OFFSET_MIN[0] <= v <= OFFSET_MIN[1]:
                    raise ValueError(f"offset must be {OFFSET_MIN[0]}..{OFFSET_MIN[1]} min")
        clock_s, how = resolve(s, now.date(), where if where is not None else location())
        text = generate(clock_s, now, label=s.label())
    except ValueError as e:
        return False, f"schedule rejected: {e}"
    parsed = rb.parse_schedule(text)
    if parsed is None:
        return False, "schedule rejected: generated script didn't parse"
    path = os.path.join(wittypi_dir, "schedule.wpi")
    prev = path + ".prev"
    had_prev = os.path.exists(path)
    try:
        if had_prev:
            os.replace(path, prev)
        _write_atomic(path, text)
        wake = rb.next_on_start(parsed, clock() + ARM_MARGIN_S)
        if wake is None:
            raise RuntimeError("no wake in the schedule")
        (arm or rb.arm_startup)(wake)
    except (OSError, RuntimeError, ValueError) as e:
        try:
            if had_prev:
                os.replace(prev, path)
            elif os.path.exists(path):
                os.unlink(path)
        except OSError:
            pass
        return False, f"schedule not applied ({e}); previous schedule kept"
    if set_param is None:
        from tools.lora_runtime_integration import set_parameter as set_param
    set_param("schedule", {**s.as_dict(), "utc_offset_min": _offset_min(now),
                           "window": [clock_s.start, clock_s.end], "window_note": how})
    set_param("monitoring_frequency", s.interval)
    shown = s.label() + (f" ({how})" if s.follows_sun else "")
    try:
        rb.log_to_wittypi(f"Schedule {shown} written", clock())
    except OSError:
        pass
    return True, f"schedule {shown} applied; next wake {datetime.fromtimestamp(wake):%Y-%m-%d %H:%M}"


def current(get_param=None) -> Optional[Schedule]:
    """The schedule set by 15 95, if any."""
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    d = get_param("schedule", None)
    if not isinstance(d, dict):
        return None
    try:
        return Schedule(int(d["start"]), int(d["end"]), int(d["interval"]), int(d["awake"]),
                        d.get("start_ref", "clock"), d.get("end_ref", "clock"))
    except (KeyError, TypeError, ValueError):
        return None


def status_label(get_param=None) -> Optional[str]:
    """`sc` in the status reply: the schedule, plus today's window when it follows the sun."""
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    s = current(get_param)
    if s is None:
        return None
    window = (get_param("schedule", {}) or {}).get("window")
    if s.follows_sun and isinstance(window, list) and len(window) == 2:
        a, b = window
        return f"{s.label()} {a // 60:02d}{a % 60:02d}-{b // 60:02d}{b % 60:02d}"
    return s.label()


def refresh(now: Optional[datetime] = None, get_param=None, **apply_kw) -> str:
    """Rewrite schedule.wpi if the local UTC offset changed since it was written (DST)."""
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    s = current(get_param)
    if s is None:
        return "no schedule set by 15 95"
    now = now or datetime.now()
    d = get_param("schedule", {}) or {}
    reasons = []
    if d.get("utc_offset_min") != _offset_min(now):
        reasons.append("the UTC offset changed")
    if s.follows_sun:
        today, _ = resolve(s, now.date(), apply_kw.get("where") or location())
        old_window = d.get("window") or [None, None]
        moved = [abs(((a - b + DAY // 2) % DAY) - DAY // 2) if isinstance(b, int) else DAY
                 for a, b in zip((today.start, today.end), old_window)]
        if max(moved) >= SUN_REWRITE_MIN:
            reasons.append(f"the sun window moved {max(moved)} min")
    if not reasons:
        return f"schedule {s.label()} up to date"
    ok, msg = apply(s, now=now, **apply_kw)
    return (f"rewritten ({', '.join(reasons)}): " if ok else "") + msg


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Wake schedule (downlink 15 95)")
    p.add_argument("--refresh", action="store_true", help="rewrite schedule.wpi after a DST change")
    p.add_argument("--show", action="store_true", help="print the active schedule")
    args = p.parse_args()
    if args.refresh:
        print(refresh())
    else:
        s = current()
        print(s.label() if s else "no schedule set by 15 95")
