#!/usr/bin/env python3
"""Each footprint's lines and zones side by side, with how often each step acted::

    python tools/corner_perimeter/overview.py [sample_id ...] [--out DIR]

Without ids, the three test footprints. Writes ``<sample_id>.png`` per footprint and ``overview.png`` for all of
them into ``--out`` (default ``temp/corner_perimeter/overview``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drawing import LEGEND, ROOT, SAMPLE, TEST_IDS, ZONE_KEYS, draw_lines, draw_zones, sample, whole  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402

from convexdecomp.corner_perimeter import corner_ray_perimeter  # noqa: E402

KEYS = ["band", "band_line", "band0", "ray", "ray_r", "apex", "merged", "wing_end", "diagonal", *ZONE_KEYS[1:],
        "group", "cut"]


def lines_title(sid: str, r) -> str:
    c = r.counts()
    return (f"{sid}: {len(r.corners)} corners cast rays, {c['wing_end_corners']} wing-end corners do not\n"
            f"rule 1: {c['merged_rays']} merged rays, rule 2: {c['corner_lines']} corner-to-corner lines, "
            f"rule 3: {c['rays_discarded']} rays discarded,\n"
            f"rule 4: {c['legs']} legs, {c['notches']} notches, {c['narrow_parts']} narrow parts to lines, "
            f"rule 5: {c['points_merged']} + {c['corner_groups']} groups of close points,\n"
            f"rule 6: {c['diagonals']} diagonals, rule 7: {c['tiny_merged']} groups of tiny zones merged, "
            f"rule 8: {c['lone_merged']} lone tiny zones merged,\nrule 9: {c['sharp_cuts']} sharp corners cut")


def panels(axes, sid: str, r) -> None:
    w = whole(r.poly)
    draw_lines(axes[0], r, w, old_band=bool(r.legs), diagonals=True)
    axes[0].set_title(lines_title(sid, r), fontsize=10)
    draw_zones(axes[1], r, w, groups=r.corner_groups, cuts=True)
    c = r.counts()
    axes[1].set_title(f"{c['zones']} perimeter zones + {c['core_parts']} core part(s)\n"
                      f"{c['thin']} thinner than {r.config.min_zone_width_m:g} m, {c['not_convex']} not convex",
                      fontsize=10)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ids", nargs="*", default=list(TEST_IDS))
    ap.add_argument("--sample", type=Path, default=SAMPLE, help="the footprint sample (pickle or GeoPackage)")
    ap.add_argument("--out", type=Path, default=ROOT / "temp" / "corner_perimeter" / "overview")
    a = ap.parse_args()
    g = sample(a.sample)
    a.out.mkdir(parents=True, exist_ok=True)
    results = {sid: corner_ray_perimeter(g.loc[sid, "geometry"]) for sid in a.ids}
    handles = [LEGEND[k] for k in ["outline", *KEYS]]
    for sid, r in results.items():
        fig, axes = plt.subplots(1, 2, figsize=(20, 9))
        panels(axes, sid, r)
        fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=9)
        fig.tight_layout(rect=(0, 0.08, 1, 0.97))
        fig.savefig(a.out / f"{sid}.png", dpi=150)
        plt.close(fig)
    fig, axes = plt.subplots(2, len(results), figsize=(7 * len(results), 17), squeeze=False)
    for col, (sid, r) in enumerate(results.items()):
        panels(axes[:, col], sid, r)
    fig.legend(handles=handles, loc="lower center", ncol=6, frameon=False, fontsize=9)
    fig.tight_layout(rect=(0, 0.05, 1, 0.98))
    fig.savefig(a.out / "overview.png", dpi=130)
    plt.close(fig)
    for sid, r in results.items():
        c = r.counts()
        print(f"{sid}: {c['zones']} zones, {c['thin']} thin, {c['not_convex']} not convex, {c['core_parts']} core parts")


if __name__ == "__main__":
    main()
