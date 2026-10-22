"""The wake schedule, set from the dashboard (downlink 15 95).

A schedule is a daily window, an interval and an awake time, in the site's
local time:

    15 95  [start HH][start MM][end HH][end MM][interval u16 min][awake u8 min]

Wakes are start, start + interval, ... while a wake begins before end;
start == end means all day, and end < start means the window crosses midnight.
`awake` is the most the Pi stays up per wake (it shuts down earlier when its
cycle ends).

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

INTERVAL_MIN = (15, 1440)
AWAKE_MIN = (5, 60)
DAY = 1440
END_YEARS = 10                 # END of the generated script, years from now
#: Don't arm a wake sooner than this from now: the Pi must be down first
ARM_MARGIN_S = 120


@dataclass(frozen=True)
class Schedule:
    start: int        # minutes after midnight, local time
    end: int
    interval: int     # minutes
    awake: int        # minutes

    def to_bytes(self) -> bytes:
        return bytes([self.start // 60, self.start % 60, self.end // 60, self.end % 60]) + \
            struct.pack(">HB", self.interval, self.awake)

    def label(self) -> str:
        """"HHMM-HHMM/interval/awake", as the status reply carries it (`sc`)."""
        return f"{self.start // 60:02d}{self.start % 60:02d}-{self.end // 60:02d}{self.end % 60:02d}" \
               f"/{self.interval}/{self.awake}"

    def as_dict(self) -> Dict[str, int]:
        return {"start": self.start, "end": self.end, "interval": self.interval, "awake": self.awake}


def from_bytes(value: bytes) -> Schedule:
    if len(value) != 7:
        raise ValueError(f"a schedule is 7 bytes, got {len(value)}")
    sh, sm, eh, em = value[:4]
    interval, awake = struct.unpack(">HB", value[4:])
    if sh > 23 or eh > 23 or sm > 59 or em > 59:
        raise ValueError("start and end must be times of day (00:00-23:59)")
    return Schedule(sh * 60 + sm, eh * 60 + em, interval, awake)


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


def generate(s: Schedule, now: datetime) -> str:
    """schedule.wpi text: BEGIN at today's first wake, one day of ON/OFF states."""
    offsets = wakes(s)
    begin = now.replace(hour=s.start // 60, minute=s.start % 60, second=0, microsecond=0)
    lines = [f"# WaterCam schedule {s.label()} (start-end/interval/awake, local time),",
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
          arm=None, set_param=None, clock=time.time) -> Tuple[bool, str]:
    """Write schedule.wpi for `s` and arm the next wake; (applied, message).

    The previous schedule.wpi is kept as schedule.wpi.prev and put back if
    anything fails, so a bad command never leaves the unit without a schedule.
    """
    now = now or datetime.now()
    try:
        text = generate(s, now)
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
    set_param("schedule", {**s.as_dict(), "utc_offset_min": _offset_min(now)})
    set_param("monitoring_frequency", s.interval)
    try:
        rb.log_to_wittypi(f"Schedule {s.label()} written (downlink 15 95)", clock())
    except OSError:
        pass
    return True, f"schedule {s.label()} applied; next wake {datetime.fromtimestamp(wake):%Y-%m-%d %H:%M}"


def current(get_param=None) -> Optional[Schedule]:
    """The schedule set by 15 95, if any."""
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    d = get_param("schedule", None)
    if not isinstance(d, dict):
        return None
    try:
        return Schedule(int(d["start"]), int(d["end"]), int(d["interval"]), int(d["awake"]))
    except (KeyError, TypeError, ValueError):
        return None


def refresh(now: Optional[datetime] = None, get_param=None, **apply_kw) -> str:
    """Rewrite schedule.wpi if the local UTC offset changed since it was written (DST)."""
    if get_param is None:
        from tools.lora_runtime_integration import get_parameter as get_param
    s = current(get_param)
    if s is None:
        return "no schedule set by 15 95"
    now = now or datetime.now()
    d = get_param("schedule", {}) or {}
    if d.get("utc_offset_min") == _offset_min(now):
        return f"schedule {s.label()} up to date"
    ok, msg = apply(s, now=now, **apply_kw)
    return ("rewritten for the new UTC offset: " if ok else "") + msg


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
