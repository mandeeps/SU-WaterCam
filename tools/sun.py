"""Sunrise and sunset, computed on the node with no network (NOAA solar equations).

Accurate to about a minute at mid latitudes, which is plenty for a wake schedule.
Times are returned as local datetimes for the given date, using the system
timezone (the node's local time, as the Witty Pi schedule uses).
"""
import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Tuple

#: Sun's centre 0.833 deg below the horizon: refraction and the sun's radius
ZENITH_DEG = 90.833


def _utc_minutes(day: date, lat: float, lon: float, rising: bool) -> Optional[float]:
    """Minutes after 00:00 UTC on `day` of sunrise (or sunset); None in polar day/night."""
    n = day.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (n - 1)          # fractional year, at noon is close enough
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(ZENITH_DEG)) / (math.cos(phi) * math.cos(decl))
              - math.tan(phi) * math.tan(decl))
    if not -1.0 <= cos_ha <= 1.0:
        return None
    ha = math.degrees(math.acos(cos_ha))
    return 720 - 4 * (lon + (ha if rising else -ha)) - eqtime


def sun_times(day: date, lat: float, lon: float) -> Optional[Tuple[datetime, datetime]]:
    """(sunrise, sunset) as naive local datetimes for `day`; None if the sun doesn't rise or set."""
    out = []
    for rising in (True, False):
        m = _utc_minutes(day, lat, lon, rising)
        if m is None:
            return None
        utc = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(minutes=m)
        out.append(utc.astimezone().replace(tzinfo=None, second=0, microsecond=0))
    return out[0], out[1]
