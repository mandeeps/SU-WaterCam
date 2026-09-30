#!/usr/bin/env python
"""Seed the shared optical-to-thermal transform cache from repeated solves.

Why this exists
---------------
Registration is stochastic. Mattes mutual information samples the images, so
solving the same scene twice gives different answers: measured on node
ufo-01-01-005, three solves of one scene returned tx of -33.22, -29.96 and
-22.65, and across all observations that scene ranged over about 101 px.

`coreg()` caches the first transform it solves and reuses it from then on, which
removes that variance. But it does not remove the *bias*: whatever the first
solve happened to draw becomes the transform every later capture inherits,
outliers included. This tool chooses that transform deliberately instead.

The cameras are bolted to the node, so the true transform is a constant and
repeated solves are samples of one value. Pick the middle one and freeze it.

Why the medoid rather than a component-wise median
--------------------------------------------------
SimpleITK returns a `CompositeTransform`, and a composite's `GetParameters()`
exposes only its active component, so a parameter-wise median cannot be
serialised back into an equivalent transform. Instead this compares transforms
by what they *do* — where they send a grid of image points — and keeps the
actual solve that sits closest to all the others. That is the medoid, the
multivariate analogue of a median, and it is a real transform that round-trips.

Usage
-----
    python tools/seed_registration_cache.py --scene IMAGES/20260414-212600 --runs 5
    python tools/seed_registration_cache.py --scene A --scene B --runs 3 --out IMAGES
    python tools/seed_registration_cache.py --scene A --runs 5 --dry-run

Pick a scene with strong thermal contrast. A thermally flat view, an indoor room
for instance, gives mutual information a shallow optimum and a wider spread.
"""
from __future__ import annotations

import argparse
import contextlib
import glob
import io
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.coreg_multiple import config, coreg, save_transform_parameters  # noqa: E402

INPUT_GLOBS = ("*-NIR-OFF.jpg", "*-NIR-ON.jpg", "*.pgm")


def solve_once(scene: str, workdir: str, quiet: bool = True):
    """Run one registration in isolation and return the transform it produced."""
    import SimpleITK as sitk

    parent = os.path.join(workdir, "p")
    dst = os.path.join(parent, "s")
    os.makedirs(dst)
    for pat in INPUT_GLOBS:
        for f in glob.glob(os.path.join(scene, pat)):
            shutil.copy2(f, dst)

    buf = io.StringIO()
    ctx = contextlib.redirect_stdout(buf) if quiet else contextlib.nullcontext()
    with ctx:
        coreg(dst)

    tfm = os.path.join(parent, config.TRANSFORM_FILE_FILENAME)
    if not os.path.exists(tfm):
        raise RuntimeError(f"no {config.TRANSFORM_FILE_FILENAME} written; "
                           f"is this coreg_multiple.py current?\n{buf.getvalue()[-800:]}")
    return sitk.ReadTransform(tfm)


def sample_grid(w: int = 1296, h: int = 972, n: int = 8) -> np.ndarray:
    xs = np.linspace(0, w, n)
    ys = np.linspace(0, h, n)
    return np.array([(x, y) for y in ys for x in xs], dtype=float)


def mapped(transform, pts: np.ndarray) -> np.ndarray:
    return np.array([transform.TransformPoint((float(x), float(y))) for x, y in pts])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scene", action="append", required=True,
                    help="scene directory holding NIR-OFF/NIR-ON/pgm; repeatable")
    ap.add_argument("--runs", type=int, default=5,
                    help="solves per scene (default 5)")
    ap.add_argument("--out", default=None,
                    help="directory to write the cache into; default is the parent "
                         "of the first --scene, which is where coreg() looks")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the spread, write nothing")
    a = ap.parse_args()

    for s in a.scene:
        if not os.path.isdir(s):
            ap.error(f"not a directory: {s}")

    forced = config.FORCE_RECALCULATE_TRANSFORM
    config.FORCE_RECALCULATE_TRANSFORM = True      # never reuse while sampling
    transforms, labels = [], []
    try:
        for scene in a.scene:
            for i in range(a.runs):
                with tempfile.TemporaryDirectory() as wd:
                    t = solve_once(scene, wd)
                transforms.append(t)
                labels.append(f"{os.path.basename(scene.rstrip('/'))}#{i + 1}")
                print(f"  solved {labels[-1]}")
    finally:
        config.FORCE_RECALCULATE_TRANSFORM = forced

    if len(transforms) < 2:
        print("need at least 2 solves to choose a middle one")
        return 1

    pts = sample_grid()
    maps = np.array([mapped(t, pts) for t in transforms])        # (n_runs, n_pts, 2)

    # pairwise RMS displacement between what each pair of transforms does
    n = len(transforms)
    d = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            d[i, j] = d[j, i] = float(np.sqrt(((maps[i] - maps[j]) ** 2).sum(1).mean()))

    total = d.sum(1)
    k = int(total.argmin())
    off = d[np.triu_indices(n, 1)]

    print(f"\n{n} solves, pairwise disagreement in mapped pixel position:")
    print(f"  median {np.median(off):6.2f} px   mean {off.mean():6.2f}   max {off.max():6.2f}")
    print(f"  spread of each solve from the chosen one: "
          f"median {np.median(d[k]):.2f} px, max {d[k].max():.2f} px")
    print(f"\nchosen: {labels[k]} (closest to all others)")

    if np.median(off) > 10:
        print("\nWARNING: solves disagree by more than 10 px at the median. The metric is "
              "poorly conditioned on this scene — prefer one with stronger thermal "
              "contrast before freezing a transform from it.")

    if a.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    out = a.out or os.path.dirname(os.path.normpath(a.scene[0]))
    # save_transform_parameters writes to the PARENT of what it is given, which is
    # where coreg() then looks, so hand it a child path of the target directory.
    save_transform_parameters(
        transforms[k], os.path.join(out, "_seed"),
        metadata={"transform_type": config.TRANSFORM_TYPE,
                  "metric": config.REGISTRATION_METRIC,
                  "seeded_from": labels[k],
                  "seed_runs": n,
                  "seed_median_disagreement_px": round(float(np.median(off)), 3)})
    print(f"wrote {os.path.join(out, config.TRANSFORM_CACHE_FILENAME)} "
          f"and {config.TRANSFORM_FILE_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
