#!/usr/bin/env python3
"""
Summarise a power CSV from tools/power_load_test.sh or tools/powerlog.py.

For each phase (or the whole file, for a powerlog CSV) it prints current,
the lowest WittyPi input and output voltage, the median CPU clock, and how
many samples had under-voltage at that moment. Then it fits voltage against
current over all samples: the intercept is the supply's voltage with no
load, and the slope (mV per amp) is how stiff the whole path is, from the
supply through cables and connectors to the WittyPi. On 006, a healthy
supply measured -2 to -71 mV/A at the WittyPi input; a faulty charger
-260 mV/A, and a V50 charging through its top port -338 mV/A
(docs/UNIT006_POWER_FAILURE.md).

The WittyPi's readings are averaged and slightly offset, so compare runs
with each other rather than reading them as absolute volts.

    python3 tools/power_test_report.py ~/powertest_20261003-104500.csv
    python3 tools/power_test_report.py ~/powerlog/powerlog.csv
"""

import argparse
import csv
import statistics
import sys
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

UNDERVOLTAGE_NOW = 1 << 0


def read_rows(path: str) -> List[dict]:
    """Rows with numeric vin/vout/iout; '#' marker lines and blank readings are skipped."""
    with open(path, newline="") as f:
        lines = [line for line in f if not line.startswith("#")]
    rows = []
    for r in csv.DictReader(lines):
        try:
            r["vin"], r["vout"], r["iout"] = float(r["vin"]), float(r["vout"]), float(r["iout"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(r)
    return rows


def fit(xs: List[float], ys: List[float]) -> Optional[Tuple[float, float]]:
    """Least-squares (intercept, slope), or None if x doesn't vary."""
    if len(xs) < 2:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - slope * mx, slope


def _undervoltage(row: dict) -> bool:
    try:
        return bool(int(row.get("throttled") or "0", 16) & UNDERVOLTAGE_NOW)
    except ValueError:
        return False


def _mhz(row: dict) -> Optional[float]:
    for key, scale in (("arm_hz", 1e6), ("arm_mhz", 1.0)):
        try:
            return float(row[key]) / scale
        except (KeyError, TypeError, ValueError):
            continue
    return None


def summarise(rows: List[dict]) -> Dict[str, dict]:
    groups: "OrderedDict[str, List[dict]]" = OrderedDict()
    for r in rows:
        groups.setdefault(r.get("phase") or "all", []).append(r)
    out = OrderedDict()
    for phase, rs in groups.items():
        current = [r["iout"] for r in rs]
        mhz = [m for m in (_mhz(r) for r in rs) if m is not None]
        out[phase] = {
            "n": len(rs),
            "i_med": statistics.median(current),
            "i_max": max(current),
            "vin_min": min(r["vin"] for r in rs),
            "vout_min": min(r["vout"] for r in rs),
            "mhz_med": statistics.median(mhz) if mhz else None,
            "uv": sum(_undervoltage(r) for r in rs),
        }
    return out


def report(rows: List[dict]) -> str:
    lines = [f"{'phase':8} {'n':>4} {'I med':>6} {'I max':>6} {'Vin min':>8} {'Vout min':>9} "
             f"{'MHz med':>8} {'UV now':>8}"]
    for phase, s in summarise(rows).items():
        mhz = f"{s['mhz_med']:.0f}" if s["mhz_med"] is not None else "-"
        lines.append(f"{phase:8} {s['n']:>4} {s['i_med']:>6.2f} {s['i_max']:>6.2f} "
                     f"{s['vin_min']:>8.2f} {s['vout_min']:>9.2f} {mhz:>8} "
                     f"{s['uv']:>4}/{s['n']:<3}")
    if not any(r.get("phase") for r in rows):
        lines.append("note: no phases (a powerlog CSV). This fit spans every supply, charge level "
                     "and boot in the file, so it isn't comparable to a load-test run.")
    current = [r["iout"] for r in rows]
    for key in ("vin", "vout"):
        f = fit(current, [r[key] for r in rows])
        if f is None:
            lines.append(f"{key}: current never varied, no fit")
            continue
        intercept, slope = f
        lines.append(f"{key}: {intercept:.2f} V at 0 A, {slope * 1000:+.0f} mV/A, "
                     f"{intercept + 1.2 * slope:.2f} V at 1.2 A")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Summarise a power CSV.")
    p.add_argument("csv", help="CSV from power_load_test.sh or powerlog.py")
    args = p.parse_args(argv)
    rows = read_rows(args.csv)
    if not rows:
        print(f"No readings in {args.csv}", file=sys.stderr)
        return 1
    print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
