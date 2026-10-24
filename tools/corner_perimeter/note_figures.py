#!/usr/bin/env python3
"""The figures of the Obsidian note "Convex Decomposition Perimeter": one per step, before and after, zoomed on
examples of the footprint sample (the three test footprints, Angers_0018, Derby_0029, Aldershot_0005, Exeter_0008,
Alicante_0030, Wroclaw_0046, Riga_0034, La_Linea_de_la_Concepcion_0016, Bucharest_0003, Almere_0021, Caen_0016,
Liverpool_0042, Strasbourg_0020, Athens_0005, Brussels_0025 and Bari_0003)::

    python tools/corner_perimeter/note_figures.py --out /path/to/attachments [--png <preview dir>]

Writes one SVG per figure (``perimeter_<step>.svg``) and, with ``--png``, a small PNG of each.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drawing import (BAND_FILL, BAND_LINE, KINDS, PINK, POINT_STYLE, RAY_CONVEX, RAY_REFLEX, SAMPLE,  # noqa: E402
                     TEST_IDS, ZONE_KEYS, construction, corner_dots, draw_lines, draw_zones, legend, letter, sample,
                     save, whole, window)

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from shapely.plotting import plot_line, plot_polygon  # noqa: E402

from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter  # noqa: E402

plt.rcParams.update({"svg.fonttype": "none", "font.size": 9})


def run(poly, upto=None, cfg=None):
    return corner_ray_perimeter(poly, cfg, upto=upto)


def pair(axes, before, after, win, letters=(0, 1), lines=False, old_band=False, groups=None, cuts=False):
    """A before/after pair of panels."""
    a0, a1 = axes
    if lines:
        draw_lines(a0, before, win)
        draw_lines(a1, after, win, old_band=old_band)
    else:
        draw_zones(a0, before, win)
        draw_zones(a1, after, win, groups=groups, cuts=cuts)
    letter(a0, letters[0])
    letter(a1, letters[1])


def main(out: Path, png: Path | None, source: Path) -> None:
    g = sample(source)
    V, K, B = (g.loc[s, "geometry"] for s in TEST_IDS)
    E, A, C = (g.loc[s, "geometry"] for s in ("Exeter_0008", "Alicante_0030", "Caen_0016"))
    W, M = (g.loc[s, "geometry"] for s in ("Angers_0018", "Almere_0021"))
    Wr, Ri, L = (g.loc[s, "geometry"] for s in ("Wroclaw_0046", "Riga_0034", "La_Linea_de_la_Concepcion_0016"))

    # band and corner rays: every ray to the facade, rays stop at the first ray, rays cut at the band line
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.4))
    for i, mode in enumerate(("full", "cut", "band")):
        cs, rays, band, band_line, pts = construction(K, mode)
        ax = axes[i]
        plot_polygon(band, ax=ax, add_points=False, facecolor=BAND_FILL, edgecolor="none")
        plot_polygon(K, ax=ax, add_points=False, facecolor="none", edgecolor="k", linewidth=1.3)
        plot_line(band_line, ax=ax, add_points=False, color=BAND_LINE, linewidth=1.0)
        for (p, d, ang, _), ray in zip(cs, rays):
            plot_line(ray, ax=ax, add_points=False, color=RAY_REFLEX if ang > 180 else RAY_CONVEX, linewidth=0.7)
        for kind in KINDS:
            xy = np.array([[q.x, q.y] for q, k in pts if k == kind]).reshape(-1, 2)
            ax.scatter(xy[:, 0], xy[:, 1], **{**POINT_STYLE[kind], "s": 10})
        ax.set_aspect("equal")
        ax.axis("off")
        letter(ax, i)
    legend(fig, ["outline", "band", "band_line", "ray", "ray_r", "x_ray", "x_band", "x_end"], ncol=8)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, out, "perimeter_rays_and_band", png)

    # wing ends: Angers' leg, 2.8 m wide, with and without rays from the two corners of its end wall
    w = whole(W)
    every = CornerRayConfig(wing_end_max_m=0.0)
    fig, axes = plt.subplots(1, 4, figsize=(16, 4.6))
    draw_lines(axes[0], run(W, "discard", every), w)
    draw_zones(axes[1], run(W, cfg=every), w)
    draw_lines(axes[2], run(W, "discard"), w)
    draw_zones(axes[3], run(W), w)
    for i, ax in enumerate(axes):
        letter(ax, i)
    legend(fig, ["outline", "band", "band_line", "ray", "ray_r", "apex", "wing_end", "zone", "thin"], ncol=9)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save(fig, out, "perimeter_wing_ends", png)

    # the wing-end check: stepped blocks whose narrow end keeps its rays (Derby, Aldershot)
    no_check = CornerRayConfig(wing_end_check=False)
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.6))
    for row, p in enumerate(g.loc[s, "geometry"] for s in ("Derby_0029", "Aldershot_0005")):
        before, after = run(p, cfg=no_check), run(p)
        pair(axes[row], before, after, whole(p), letters=(2 * row, 2 * row + 1))
        for ax, r in zip(axes[row], (before, after)):
            xy = np.array(r.wing_ends).reshape(-1, 2)
            ax.scatter(xy[:, 0], xy[:, 1], marker="x", s=45, color="#252525", linewidths=1.4, zorder=8)
    legend(fig, ZONE_KEYS[:3] + ["wing_end"], ncol=4)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save(fig, out, "perimeter_wing_end_check", png)

    # rule 1: rays that meet merge (Krakow's lower wing, two bays)
    w = ((-4.5, 18.0), (-17.0, -6.5))
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.4))
    pair(axes, run(K, "rays"), run(K, "merge_rays"), w, lines=True)
    legend(fig, ["outline", "band", "band_line", "ray", "ray_r", "apex", "wing_end"], ncol=7)
    fig.tight_layout(rect=(0, 0.12, 1, 1))
    save(fig, out, "perimeter_merged_rays", png)

    # rule 2: close opposite rays become one line (Valdemoro's connector)
    w = window((-7.5, 15.0), 7.5)
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.9))
    pair(axes, run(V, "merge_rays"), run(V, "opposite"), w, lines=True)
    legend(fig, ["outline", "band", "band_line", "ray", "ray_r", "apex", "merged"], ncol=4)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    save(fig, out, "perimeter_opposite_rays", png)

    # rule 3: rays that reach nothing are discarded (Bognor's left end)
    w = ((-47.5, -29.5), (3.5, 19.5))
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.9))
    pair(axes, run(B, "opposite"), run(B, "discard"), w, lines=True)
    legend(fig, ["outline", "band", "band_line", "ray", "ray_r", "merged", "apex", "wing_end"], ncol=4)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    save(fig, out, "perimeter_discarded_rays", png)

    # rule 4, legs: the shorter side onto the longer (Krakow's lower wing, Bognor's left core arm)
    examples = [(K, ((-24.0, 18.0), (-23.5, -4.5))), (B, window((-31.0, 9.0), 8.0, 6.0))]
    fig, axes = plt.subplots(2, 2, figsize=(14, 7.6), gridspec_kw=dict(height_ratios=(1, 1.2)))
    for row, (p, w) in enumerate(examples):
        pair(axes[row], run(p, "discard"), run(p, "narrow_core"), w, letters=(2 * row, 2 * row + 1),
             lines=True, old_band=True)
    legend(fig, ["outline", "band", "band_line", "band0", "ray", "ray_r", "apex", "wing_end"], ncol=8)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, out, "perimeter_narrow_core_legs", png)

    # rule 4, narrow parts: Exeter's left wing, Alicante's core, Valdemoro's lower right wing; "before" leaves
    # narrow parts as they are (no whole parts, tips or passages to lines)
    without = CornerRayConfig(collapse_narrow_parts=False, narrow_core_m=0.0)
    fig, axes = plt.subplots(3, 3, figsize=(15, 13.5))
    for row, p in enumerate((E, A, V)):
        w = whole(p) if p is not V else ((5.0, 27.0), (-31.0, -11.0))
        after_lines = run(p, "narrow_core")
        draw_zones(axes[row, 0], run(p, cfg=without), w)
        draw_lines(axes[row, 1], after_lines, w)
        for t in after_lines.narrow_parts:
            plot_line(t["line"], ax=axes[row, 1], add_points=False, color=PINK, linewidth=2.0)
        draw_zones(axes[row, 2], run(p), w)
    for i, ax in enumerate(axes.flat):
        letter(ax, i)
    legend(fig, ["outline", "band", "band_line", "ray", "ray_r", "apex", "wing_end", "zone", "thin", "nonconvex", "core"],
           ncol=6)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, "perimeter_narrow_core_parts", png)

    # rule 5: close zone corners become one (Krakow's bottom-left)
    w = ((-25.5, -19.0), (-21.0, -15.5))
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.0))
    before, after = run(K, "narrow_core"), run(K, "close_points")
    pair(axes, before, after, w, groups=after.points_merged)
    corner_dots(axes[0], before)
    legend(fig, ZONE_KEYS + ["group"], ncol=6)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    save(fig, out, "perimeter_close_points", png)

    # rule 6: narrow zones give way to the shorter diagonal (Krakow's lower wing, Valdemoro's left wing)
    examples = [(K, window((-11.0, -13.5), 6.5, 4.5)), (V, window((-20.5, 12.0), 7.0, 5.0))]
    fig, axes = plt.subplots(2, 2, figsize=(9, 7.0))
    for row, (p, w) in enumerate(examples):
        pair(axes[row], run(p, "close_points"), run(p, "diagonals"), w, letters=(2 * row, 2 * row + 1))
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, "perimeter_diagonals", png)

    # rule 5 again: corner groups with a longer reach (Valdemoro's upper core, Bognor's upper wing)
    examples = [(V, window((8.4, 21.7), 5.0, 3.6)), (B, window((-15.4, 15.0), 5.0, 3.6))]
    fig, axes = plt.subplots(2, 2, figsize=(9, 6.8))
    for row, (p, w) in enumerate(examples):
        before, after = run(p, "diagonals"), run(p, "corner_groups")
        pair(axes[row], before, after, w, letters=(2 * row, 2 * row + 1), groups=after.corner_groups)
        corner_dots(axes[row, 0], before)
    legend(fig, ZONE_KEYS + ["group"], ncol=6)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, "perimeter_corner_groups", png)

    # rule 7: touching tiny zones become one convex zone (Wroclaw's wing end, Riga's rounded bay, La Linea)
    examples = [(Wr, window((-2.0, -10.5), 6.0, 4.3)), (Ri, window((1.3, -13.65), 5.7, 4.0)),
                (L, window((6.6, 7.25), 4.5, 3.2))]
    fig, axes = plt.subplots(3, 2, figsize=(9, 9.6))
    for row, (p, w) in enumerate(examples):
        pair(axes[row], run(p, "corner_groups"), run(p, "tiny_touching"), w, letters=(2 * row, 2 * row + 1))
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    save(fig, out, "perimeter_tiny_touching", png)

    # rule 7's width check: Bucharest's rounded bay, before rule 7, with the hull, merged without it
    Bu = g.loc["Bucharest_0003", "geometry"]
    w = window((4.6, 23.0), 6.5, 4.6)
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 3.9))
    draw_zones(axes[0], run(Bu, "corner_groups"), w)
    draw_zones(axes[1], run(Bu, "tiny_touching", CornerRayConfig(hull_keeps_width=False)), w)
    draw_zones(axes[2], run(Bu, "tiny_touching"), w)
    for i, ax in enumerate(axes):
        letter(ax, i)
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    save(fig, out, "perimeter_tiny_touching_width", png)

    # rule 8: lone tiny zones merge into a neighbour, after a relink if needed (Almere, Bognor's lower wing)
    examples = [(M, window((30.7, -2.9), 4.2, 3.4)), (B, window((-6.5, -12.2), 4.5, 3.4))]
    fig, axes = plt.subplots(2, 2, figsize=(9, 6.8))
    for row, (p, w) in enumerate(examples):
        pair(axes[row], run(p, "tiny_touching"), run(p, "tiny_lone"), w, letters=(2 * row, 2 * row + 1))
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, "perimeter_tiny_lone", png)

    # rule 9: sharp corners are cut off (Krakow's bottom-left end, Caen)
    examples = [(K, window((-23.0, -16.0), 7.0, 5.0)), (C, window((3.0, 15.0), 5.0, 3.6))]
    fig, axes = plt.subplots(2, 2, figsize=(9, 7.0))
    for row, (p, w) in enumerate(examples):
        pair(axes[row], run(p, "tiny_lone"), run(p, "sharp"), w, letters=(2 * row, 2 * row + 1), cuts=True)
    legend(fig, ZONE_KEYS + ["cut"], ncol=6)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, "perimeter_sharp_corners", png)

    # rule 9, who takes the tip: a triangle handed over whole (Liverpool, its outline dashed after), the shorter
    # edge on the outline (Strasbourg)
    examples = [("Liverpool_0042", window((12.7, 7.8), 10.5, 4.6)), ("Strasbourg_0020", window((0.4, -3.3), 2.8, 1.5))]
    fig, axes = plt.subplots(2, 2, figsize=(11, 5.6))
    for row, (sid, w) in enumerate(examples):
        p = g.loc[sid, "geometry"]
        after = run(p, "sharp")
        pair(axes[row], run(p, "tiny_lone"), after, w, letters=(2 * row, 2 * row + 1), cuts=True)
        for z in after.sharp_whole:
            plot_line(z.exterior, ax=axes[row, 1], add_points=False, color=PINK, linewidth=1.8, linestyle=(0, (2, 1)))
    legend(fig, ZONE_KEYS + ["cut"], ncol=6)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    save(fig, out, "perimeter_sharp_takers", png)

    # rule 10: specks join a neighbour (Brussels' chamfer, Bari's step)
    examples = [("Brussels_0025", window((8.9, 8.2), 3.0, 2.1)), ("Bari_0003", window((2.8, -14.3), 3.0, 2.1))]
    fig, axes = plt.subplots(2, 2, figsize=(9, 6.4))
    for row, (sid, w) in enumerate(examples):
        p = g.loc[sid, "geometry"]
        before = run(p, "sharp")
        pair(axes[row], before, run(p), w, letters=(2 * row, 2 * row + 1))
        corner_dots(axes[row, 0], before)
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, out, "perimeter_specks", png)

    # the result on all three test footprints
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.6))
    for i, p in enumerate((V, K, B)):
        draw_zones(axes[i], run(p), whole(p))
        letter(axes[i], i)
    legend(fig, ZONE_KEYS, ncol=5)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    save(fig, out, "perimeter_final_zones", png)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True, help="where the SVGs go (the note's attachments)")
    ap.add_argument("--png", type=Path, help="also a small PNG of each figure, here")
    ap.add_argument("--sample", type=Path, default=SAMPLE, help="the footprint sample (pickle or GeoPackage)")
    a = ap.parse_args()
    main(a.out, a.png, a.sample)
