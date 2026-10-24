#!/usr/bin/env python3
"""The chosen settings against variants with one choice changed, on the footprints with the most reflex corners::

    python tools/corner_perimeter/compare_variants.py [--n 23] [--draw "wing ends: rays from every corner"]

``--n 0`` takes every footprint of the sample. Writes ``variants.csv`` (one row per footprint and variant) and
``variants_summary.csv`` into ``--out`` (default ``temp/corner_perimeter/variants``) and prints the totals; with
``--draw`` also ``variants_<k>.png``: the chosen settings beside that variant for the footprints where they differ.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drawing import ROOT, SAMPLE, ZONE_KEYS, draw_zones, legend, sample, whole, zones_title  # noqa: E402
from run_sample import TimeOut, time_limit  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from convexdecomp.core.scores import minimum_width  # noqa: E402
from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter  # noqa: E402
from convexdecomp.corner_perimeter.rays import corners  # noqa: E402

#: settings overrides on top of the chosen defaults
VARIANTS = {
    "chosen": {},
    "wing ends: rays from every corner": {"wing_end_max_m": 0.0},
    "wing ends: any wall under 4 m, curves too": {"wing_end_parallel_deg": 180.0},
    "wing ends: only long wings": {"wing_end_long_sides": True},
    "wing ends: no check": {"wing_end_check": False},
    "short walls: two rays each": {"short_wall_one_ray": False},
    "bays: off": {"bays": False},
    "rule 3: discard every ray that reaches nothing": {"keep_reflex_rays": False},
    "rule 3: discard none (cast them again)": {"recast_discarded": True},
    "rule 4: narrow core always half way": {"similar_sides_ratio": 0.0},
    "rule 4: narrowest core 1 m": {"narrow_core_m": 1.0},
    "rule 7: no width check": {"hull_keeps_width": False},
    "rule 9: skip cuts that make a convex zone non-convex": {"sharp_keep_convex": True},
    "no rules 7 and 8 after rule 9": {"repeat_thin_after_sharp": False},
    "no rule 10 (specks stay)": {"join_specks": False},
    "rules 9, 7, 8 and 10 once, no repeat": {"repeat_until_stable": False},
    "no tidy step": {"snap_to_outline": False, "merge_close_again": False, "straighten_corners": False,
                     "drop_collinear": False, "split_pinched": False},
}
TIME_LIMIT_S = 60


def metrics(r) -> dict:
    c = r.counts()
    pz = r.perimeter_zones
    return dict(zones=c["zones"], core_parts=c["core_parts"], thin=c["thin"], not_convex=c["not_convex"],
                sharp_20=c["sharp_20"], specks=c["specks"], notches=c["notches"], discarded=c["rays_discarded"],
                wing_end_corners=c["wing_end_corners"],
                min_width_m=round(min(minimum_width(z) for z in pz), 2) if pz else None)


def same(a, b) -> bool:
    if a is None or b is None or len(a.zones) != len(b.zones):
        return False
    return all(any(z.equals_exact(o, 1e-6) or z.equals(o) for o, _ in b.zones) for z, _ in a.zones)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", type=Path, default=SAMPLE, help="the footprint sample (pickle or GeoPackage)")
    ap.add_argument("--out", type=Path, default=ROOT / "temp" / "corner_perimeter" / "variants")
    ap.add_argument("--n", type=int, default=23, help="the footprints with the most reflex corners (0: all)")
    ap.add_argument("--draw", choices=list(VARIANTS)[1:], help="draw the chosen settings beside this variant")
    a = ap.parse_args()
    g = sample(a.sample)
    g["reflex"] = [sum(c[2] > 180.0 for c in corners(p)) for p in g.geometry]
    pick = g.sort_values("reflex", ascending=False)
    pick = pick.head(a.n) if a.n else pick
    a.out.mkdir(parents=True, exist_ok=True)

    rows, kept = [], {}
    for sid, poly, nref in zip(pick.index, pick.geometry, pick.reflex):
        for name, flags in VARIANTS.items():
            t = time.time()
            try:
                with time_limit(TIME_LIMIT_S):
                    r = corner_ray_perimeter(poly, CornerRayConfig(**flags))
                error = ""
            except TimeOut:
                r, error = None, f"over {TIME_LIMIT_S} s"
            except Exception as exc:  # noqa: BLE001  (recorded per footprint)
                r, error = None, f"{type(exc).__name__}: {exc}"[:120]
            row = dict(sample_id=sid, reflex=nref, variant=name, error=error, seconds=round(time.time() - t, 2))
            if r is not None:
                row.update(metrics(r))
            rows.append(row)
            if a.draw and name in ("chosen", a.draw):
                kept[(sid, name)] = r
    df = pd.DataFrame(rows)
    df.to_csv(a.out / "variants.csv", index=False)
    ok = df[df.error == ""]
    cols = ["zones", "thin", "not_convex", "sharp_20", "specks", "notches", "discarded", "wing_end_corners"]
    summary = ok.groupby("variant", sort=False)[cols].sum()
    summary["errors"] = df.groupby("variant", sort=False).error.apply(lambda e: (e != "").sum())
    summary["footprints with thin zones"] = ok.groupby("variant", sort=False).thin.apply(lambda t: (t > 0).sum())
    summary.to_csv(a.out / "variants_summary.csv")
    print(f"{len(pick)} footprints")
    print(summary.to_string())

    if a.draw:
        differ = [sid for sid in pick.index if not same(kept[(sid, "chosen")], kept[(sid, a.draw)])]
        print(f"{len(differ)} footprints differ from '{a.draw}':", differ)
        for k in range(0, len(differ), 4):
            chunk = differ[k:k + 4]
            fig, axes = plt.subplots(len(chunk), 2, figsize=(10.4, 5.0 * len(chunk)), squeeze=False)
            for row, sid in enumerate(chunk):
                for col, name in enumerate((a.draw, "chosen")):
                    r = kept[(sid, name)]
                    ax = axes[row, col]
                    if r is None:
                        ax.axis("off")
                        ax.set_title(f"{sid}\n{name}: failed", fontsize=9)
                        continue
                    draw_zones(ax, r, whole(r.poly))
                    ax.set_title(f"{sid} - {name}\n{zones_title(r)}", fontsize=9)
            legend(fig, ZONE_KEYS, ncol=5)
            fig.tight_layout(rect=(0, 0.02, 1, 1))
            fig.savefig(a.out / f"variants_{k // 4 + 1}.png", dpi=55)
            plt.close(fig)


if __name__ == "__main__":
    main()
