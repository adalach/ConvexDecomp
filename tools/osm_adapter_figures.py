#!/usr/bin/env python3
"""Figures and numbers of the OSM adapter note: the BEM4AI footprint sample through every step of the adapter.

Runs the adapter (`convexdecomp.osm.preprocess.run_adapter`, level L2, the notebook's settings) on
the BEM4AI sample, and writes one SVG per figure plus ``osm_adapter_numbers.json`` with the counts
the note quotes::

    python tools/osm_adapter_figures.py --out /path/to/attachments --types building_types.csv

``--types`` is a CSV of ``sample_id`` and ``building_type`` (from BEM4AI's ``buildings_gdf.pkl``)
for the composition figure.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib as mpl  # noqa: E402

mpl.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from convexdecomp.osm.axes import AxesSettings, residual_steps, snap_to_axes  # noqa: E402
from convexdecomp.osm.footprints import footprint_shapes  # noqa: E402
from convexdecomp.osm.normalize import count_exterior_vertices  # noqa: E402
from convexdecomp.osm.preprocess import AdapterSettings, reflex_vertex_counts, run_adapter  # noqa: E402
from convexdecomp.osm.rules import load_rules  # noqa: E402

SAMPLE = ROOT / "data" / "bem4ai" / "osm_footprints_bem4ai_sample.gpkg"

TUM_BLUE, LIGHT_BLUE, PALE_BLUE = "#0065BD", "#98C6EA", "#EAF4FB"
GREY, MID_GREY, ORANGE, AXES_GREY = "#D9DADB", "#9C9E9F", "#E37222", "#5C6770"
CYCLE = ["#1F4E79", "#C17C00", "#2A9D8F", "#8E44AD", "#00B4D8"]

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["TUM Neue Helvetica", "Helvetica Neue", "Helvetica", "Arial", "TeX Gyre Heros"],
    "font.size": 9, "axes.titlesize": 8.5, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": AXES_GREY, "axes.linewidth": 0.8,
    "axes.grid": True, "grid.color": GREY, "grid.linestyle": ":", "grid.linewidth": 0.6,
    "axes.prop_cycle": mpl.cycler(color=CYCLE),
    "lines.linewidth": 1.6, "legend.frameon": False,
    "svg.fonttype": "none", "savefig.bbox": "tight",
})


# ------------------------------------------------------------------------------------------------ drawing
def draw(ax, geom, *, face="none", edge=TUM_BLUE, lw=1.0, alpha=1.0, dots=False, z=2):
    for poly in shapely.get_parts(geom) if geom is not None else []:
        if poly.geom_type != "Polygon" or poly.is_empty:
            continue
        x, y = poly.exterior.xy
        ax.fill(x, y, facecolor=face, edgecolor=edge, linewidth=lw, alpha=alpha, zorder=z)
        for ring in poly.interiors:
            hx, hy = ring.xy
            ax.fill(hx, hy, facecolor="white", edgecolor=edge, linewidth=lw, zorder=z + 0.1)
        if dots:
            for ring in [poly.exterior, *poly.interiors]:
                pts = np.asarray(ring.coords[:-1])
                ax.plot(pts[:, 0], pts[:, 1], "o", ms=2.2, color=edge, zorder=z + 0.2)


def frame(ax, geoms, pad=0.06, half_extent=None):
    ax.set_aspect("equal")
    ax.axis("off")
    if half_extent is not None:
        ax.set_xlim(-half_extent, half_extent)
        ax.set_ylim(-half_extent, half_extent)
        return
    minx, miny, maxx, maxy = unary_union([g for g in geoms if g is not None]).bounds
    span = max(maxx - minx, maxy - miny, 1.0)
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    ax.set_xlim(cx - span / 2 * (1 + pad), cx + span / 2 * (1 + pad))
    ax.set_ylim(cy - span / 2 * (1 + pad), cy + span / 2 * (1 + pad))


def label(ax, text):
    ax.set_title(text, fontsize=7.5, loc="left", pad=2)


def panels(n, ncols, size=1.9):
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(size * ncols, size * nrows + 0.25 * nrows), squeeze=False)
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    return fig, axes.ravel()


def save(fig, out: Path, stem: str):
    fig.savefig(out / f"{stem}.svg")
    plt.close(fig)
    print("wrote", stem)


def removed_part(before, after):
    try:
        return shapely.difference(before, after, grid_size=1e-6)
    except shapely.errors.GEOSException:
        return None


def fig_before_after(rows, before_col, after_col, out, stem, titles, *, ncols=4, dots=True, removed=True, size=1.9):
    fig, axes = panels(len(rows), ncols, size=size)
    for ax, (sid, row) in zip(axes, rows.iterrows()):
        before, after = row[before_col], row[after_col]
        draw(ax, before, face=GREY, edge=MID_GREY, lw=0.6, dots=dots, z=1)
        if removed:
            draw(ax, removed_part(before, after), face=ORANGE, edge="none", alpha=0.85, z=1.5)
        draw(ax, after, face="none", edge=TUM_BLUE, lw=1.1, dots=dots, z=3)
        frame(ax, [before, after])
        label(ax, titles(sid, row))
    fig.tight_layout()
    save(fig, out, stem)


# ------------------------------------------------------------------------------------------------ figures
def fig_sample_overview(raw, types, out):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.6))
    counts = raw["country"].value_counts().sort_values()
    ax = axes[0, 0]
    ax.barh(counts.index, counts.to_numpy(), color=TUM_BLUE, height=0.7)
    ax.set_xlabel("Footprints")
    ax.tick_params(axis="y", labelsize=6.5)
    ax.grid(axis="y", visible=False)
    ax = axes[0, 1]
    if types is not None:
        t = types.value_counts().sort_values()
        ax.barh(t.index, t.to_numpy(), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6, height=0.6)
        ax.set_xlabel("Footprints")
        ax.grid(axis="y", visible=False)
    ax = axes[1, 0]
    area = raw["geom_local_raw"].map(lambda g: g.area)
    ax.hist(area, bins=np.logspace(np.log10(20), np.log10(2100), 30), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    ax.set_xscale("log")
    ax.set_xlabel("Footprint area (m²)")
    ax.set_ylabel("Footprints")
    ax = axes[1, 1]
    v, r = np.minimum(raw["n_vertices_raw"], 50), np.minimum(raw["n_reflex_raw"], 50)
    bins = np.arange(-0.5, 51.5, 1)
    ax.hist(v, bins=bins, histtype="step", color=CYCLE[0], label=f"exterior vertices (median {int(np.median(raw['n_vertices_raw']))})")
    ax.hist(r, bins=bins, histtype="step", color=CYCLE[1], label=f"reflex corners (median {int(np.median(raw['n_reflex_raw']))})")
    ax.set_yscale("log")
    ax.set_xlabel("Per footprint (values above 50 counted at 50)")
    ax.set_ylabel("Footprints (log)")
    ax.legend(loc="upper right")
    fig.tight_layout()
    save(fig, out, "osm_sample_overview")


def fig_gallery(raw, out, n=30, seed=7, half=50.0):
    pick = raw.sample(n=n, random_state=seed)
    pick = pick.iloc[np.argsort(pick["geom_local_raw"].map(lambda g: g.area).to_numpy())]
    fig, axes = panels(n, 6, size=1.15)
    for ax, (_, row) in zip(axes, pick.iterrows()):
        draw(ax, row["geom_local_raw"], face=LIGHT_BLUE, edge=TUM_BLUE, lw=0.7)
        frame(ax, None, half_extent=half)
        ax.text(-half, -half, str(row["sample_id"]).rsplit("_", 1)[0][:14], fontsize=5.5, color=AXES_GREY, va="bottom")
    ax = axes[0]
    ax.plot([-half + 2, -half + 22], [half - 5, half - 5], color="black", lw=1.2)
    ax.text(-half + 12, half - 3, "20 m", ha="center", va="bottom", fontsize=6)
    fig.subplots_adjust(wspace=0.05, hspace=0.05)
    save(fig, out, "osm_sample_gallery")


def fig_axes(rows, out, settings):
    fig, axes = panels(len(rows), 4, size=1.9)
    for ax, (sid, row) in zip(axes, rows.iterrows()):
        geom, axes_ = row["geom_local_thin"], row["axes"]
        draw(ax, geom, face=PALE_BLUE, edge=TUM_BLUE, lw=0.8, z=2)
        for k, line in axes_.line_geometries(geom.bounds, pad=1.5):
            x, y = line.xy
            ax.plot(x, y, ls="--", lw=0.7, color=CYCLE[k % len(CYCLE)], zorder=3)
        frame(ax, [geom.buffer(1.5)])
        label(ax, f"{sid}\n{len(axes_.directions)} directions, {len(axes_.lines)} wall lines")
    fig.tight_layout()
    save(fig, out, "osm_axes_examples")


def fig_stage_sequence(m, ids, out):
    stages = [("geom_local_raw", "OSM outline"), ("geom_local_vertices", "vertices"),
              ("geom_local_thin", "thin parts"), ("geom_local", "normalized")]
    fig, axes = plt.subplots(len(ids), len(stages), figsize=(1.6 * len(stages), 1.6 * len(ids)), squeeze=False)
    for i, (axes_row, sid) in enumerate(zip(axes, ids)):
        row = m.loc[sid]
        geoms = [row[c] for c, _ in stages]
        for ax, (column, title) in zip(axes_row, stages):
            draw(ax, row["geom_local_raw"], face="none", edge=MID_GREY, lw=0.5, z=1)
            draw(ax, row[column], face=PALE_BLUE, edge=TUM_BLUE, lw=0.8, dots=True, z=2)
            frame(ax, geoms)
            n = count_exterior_vertices(row[column])
            ax.set_title(f"{title}\n{n} vertices" if i == 0 else f"{n} vertices", fontsize=6.5, pad=2)
        axes_row[0].text(-0.05, 0.5, sid, transform=axes_row[0].transAxes, rotation=90, ha="right", va="center", fontsize=6.5)
    fig.tight_layout()
    save(fig, out, "osm_stage_sequence")


def fig_parameter_study(study: pd.DataFrame, out):
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.4))
    for ortho, style in ((False, "-"), (True, "--")):
        d = study.loc[study["ortho"].eq(ortho)].sort_values("offset_m")
        lab = "right angles snapped" if ortho else "directions as found"
        axes[0].plot(d["offset_m"], d["steps"], style, marker="o", ms=3, color=CYCLE[0], label=lab)
        axes[1].plot(d["offset_m"], d["move_p99"], style, marker="o", ms=3, color=CYCLE[1], label=lab)
        axes[2].plot(d["offset_m"], d["kept"], style, marker="o", ms=3, color=CYCLE[2], label=lab)
    for ax, ylabel in zip(axes, ["Facade steps up to 0.5 m left", "Largest move, 99th percentile (m)",
                                 "Normalizations refused"]):
        ax.set_xlabel("Wall-line offset tolerance (m)")
        ax.set_ylabel(ylabel)
    axes[0].legend()
    fig.tight_layout()
    save(fig, out, "osm_axes_parameter_study")


def fig_fidelity(final, out):
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.3))
    h = final["fidelity_hausdorff_m"].clip(lower=1e-3)
    axes[0].hist(h, bins=np.logspace(-3, 1, 32), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    axes[0].set_xscale("log")
    axes[0].set_xlabel("Largest move from the OSM outline (m)")
    axes[0].set_ylabel("Footprints")
    axes[1].hist(final["fidelity_area_change_pct"], bins=np.linspace(-4, 4, 33), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    axes[1].set_xlabel("Area change (%)")
    axes[1].set_yscale("log")
    axes[2].hist(final["fidelity_iou"], bins=np.linspace(0.93, 1.0, 29), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    axes[2].set_xlabel("Intersection over union")
    axes[2].set_yscale("log")
    fig.tight_layout()
    save(fig, out, "osm_fidelity")


def fig_working_set_complexity(final, out):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5))
    final = final.assign(n_vertices_final=final["geom_local"].map(count_exterior_vertices),
                         n_vertices_raw=final["geom_local_raw"].map(count_exterior_vertices))
    for ax, (before, after, xlabel) in zip(axes, [("n_vertices_raw", "n_vertices_final", "Exterior vertices"),
                                                 ("n_reflex_raw", "n_reflex", "Reflex corners")]):
        cap = 40
        bins = np.arange(-0.5, cap + 1.5, 1)
        ax.hist(np.minimum(final[before], cap), bins=bins, histtype="step", color=CYCLE[1],
                label=f"OSM outline (median {int(final[before].median())})")
        ax.hist(np.minimum(final[after], cap), bins=bins, histtype="step", color=CYCLE[0],
                label=f"forwarded (median {int(final[after].median())})")
        ax.set_yscale("log")
        ax.set_xlabel(f"{xlabel} (values above {cap} counted at {cap})")
        ax.set_ylabel("Footprints (log)")
        ax.legend()
    fig.tight_layout()
    save(fig, out, "osm_working_set_complexity")


# ------------------------------------------------------------------------------------------- pipeline diagram
FONT = "'TUM Neue Helvetica', 'TUMNeueHelvetica', 'Helvetica Neue', HelveticaNeue, Helvetica, Arial, sans-serif"


def pipeline_svg(numbers: dict) -> str:
    """The adapter as nine boxes in three rows (mm units, 190 mm wide)."""
    steps = [
        ("BEM4AI STAGE 3", "SOURCE", ["20,000 OSM footprints from 745 cities,", "drawn under the geometry checks", "(one polygon, area, holes,", "vertices, corners)"], "source"),
        ("1 IMPORT", "ALL LEVELS", [f"{numbers['footprints']:,} footprints at random; the OSM", "outline read again and checked", "against BEM4AI's fingerprint;", "compared with BEM4AI's version"], "step"),
        ("2 LOCAL FRAME", "L0", ["UTM zone of the city, the centre", "of the bounding box at (0, 0);", "nothing else changes", "geom_local_raw"], "step"),
        ("3 VERTICES", "L1", ["duplicates within 1 cm merged,", "vertices within 5 cm of the", "outline without them dropped", "geom_local_vertices"], "step"),
        ("4 THIN PARTS", "L1", ["parts under 2 m cut off; more", "than 2 % off, a split or an opened", "courtyard removes the footprint", f"{numbers['removed_thin_parts']} removed"], "check"),
        ("5 ARCHITECTURAL AXES", "L2", ["wall directions within 3°,", "wall lines within 0.5 m,", "per facing side; the longest", "wall sets the line"], "step"),
        ("6 NORMALIZATION", "L2", ["every edge onto its wall line,", "corners where the lines cross;", "kept only within 0.7 m, 5 % area,", "no new corner under 30°"], "step"),
        ("7 CHECKS", "ALL LEVELS", ["every check of config/checks.json", "on the normalized footprint", "", f"{numbers['removed_checks']} removed"], "check"),
        ("8 WORKING SET", "TO OSM_2", ["subset_buildings_gdf.pkl", f"{numbers['working_set']} footprints in local metres,", "to perimeter zoning and", "the convex decomposition"], "output"),
    ]
    W, margin, cols, gap_x = 190.0, 1.0, 3, 8.0
    bw = (W - 2 * margin - (cols - 1) * gap_x) / cols
    th, ph, gap_y = 10.0, 15.0, 9.0
    bh = th + ph
    rows = int(math.ceil(len(steps) / cols))
    H = margin * 2 + rows * bh + (rows - 1) * gap_y
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H:.1f}mm" viewBox="0 0 {W} {H:.1f}" '
           f'font-family="{FONT}">',
           '<defs><marker id="chev" markerUnits="userSpaceOnUse" markerWidth="2.8" markerHeight="2.8" viewBox="0 0 10 10" '
           'refX="9.2" refY="5" orient="auto"><path d="M1.2,1 L9.2,5 L1.2,9" fill="none" stroke="#0065BD" '
           'stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></marker></defs>',
           '<rect width="100%" height="100%" fill="white"/>']
    pos = []
    for k, (title, tag, lines, kind) in enumerate(steps):
        r, c = divmod(k, cols)
        x, y = margin + c * (bw + gap_x), margin + r * (bh + gap_y)
        pos.append((x, y))
        outline = {"source": GREY, "output": ORANGE}.get(kind, TUM_BLUE)
        panel = {"source": "#F2F2F2", "output": "#FBE3D3"}.get(kind, LIGHT_BLUE)
        tag_colour = {"output": ORANGE, "source": MID_GREY}.get(kind, TUM_BLUE)
        out.append(f'<path d="M{x},{y + th - 2} H{x + bw} V{y + bh - 2.4} A2.4,2.4 0 0 1 {x + bw - 2.4},{y + bh} '
                   f'H{x + 2.4} A2.4,2.4 0 0 1 {x},{y + bh - 2.4} Z" fill="{panel}"/>')
        out.append(f'<rect x="{x}" y="{y}" width="{bw}" height="{th}" rx="2.4" fill="white" stroke="{outline}" stroke-width="0.45"/>')
        out.append(f'<text x="{x + 2.2}" y="{y + 4.6}" font-size="3.0" fill="black">{title}</text>')
        out.append(f'<text x="{x + 2.2}" y="{y + 8.0}" font-size="2.47" font-weight="bold" letter-spacing="0.12" '
                   f'fill="{tag_colour}">{tag}</text>')
        for i, line in enumerate(lines):
            bold = kind == "check" and i == len(lines) - 1
            weight = ' font-weight="bold" fill="#0065BD"' if bold else ' fill="black"'
            style = ' font-style="italic"' if line.startswith(("geom_", "subset_")) else ""
            out.append(f'<text x="{x + 2.2}" y="{y + th + 3.4 + i * 3.05:.2f}" font-size="2.47"{weight}{style}>{line}</text>')

    def connector(points):
        (x0, y0) = points[0]
        d = "M" + " L".join(f"{px:.2f},{py:.2f}" for px, py in points)
        return (f'<path d="{d}" fill="none" stroke="#0065BD" stroke-width="0.35" marker-end="url(#chev)"/>'
                f'<circle cx="{x0:.2f}" cy="{y0:.2f}" r="0.8" fill="white" stroke="#0065BD" stroke-width="0.3"/>')
    for k in range(len(steps) - 1):
        (x0, y0), (x1, y1) = pos[k], pos[k + 1]
        if k % cols != cols - 1:
            out.append(connector([(x0 + bw, y0 + th / 2), (x1, y1 + th / 2)]))
        else:
            ymid = y0 + bh + gap_y / 2
            out.append(connector([(x0 + bw / 2, y0 + bh), (x0 + bw / 2, ymid), (x1 + bw / 2, ymid), (x1 + bw / 2, y1)]))
    out.append("</svg>")
    return "\n".join(out)


# --------------------------------------------------------------------------------------------- parameter study
def parameter_study(inputs: np.ndarray) -> pd.DataFrame:
    """Normalization of the footprints after step 4 for wall-line offset tolerances 0.2 to 0.6 m."""
    rows = []
    for ortho in (None, 3.0):
        for offset in (0.2, 0.3, 0.4, 0.5, 0.6):
            st = replace(AxesSettings(), offset_tolerance_m=offset, orthogonal_tolerance_deg=ortho,
                         max_move_m=max(0.7, offset + 0.2))
            res = [snap_to_axes(p, st) for p in inputs]
            out = np.asarray([r[0] for r in res], dtype=object)
            steps = np.array([residual_steps(p) for p in out])
            moved = shapely.hausdorff_distance(out, inputs)
            rows.append({"offset_m": offset, "ortho": ortho is not None, "steps": int(steps.sum()),
                         "move_p99": float(np.quantile(moved, 0.99)),
                         "kept": int(sum(r[1].startswith("kept") for r in res))})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------------ main
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--types", type=Path, help="CSV with sample_id and building_type (from BEM4AI)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    warnings.filterwarnings("ignore")

    rules = load_rules()
    run = run_adapter(SAMPLE, rules, AdapterSettings())
    gdf = run.footprints.set_index("sample_id", drop=False)
    final = run.final.set_index("sample_id", drop=False)
    funnel = run.funnel.set_index("sample_id", drop=False)

    # The whole sample in its local frame, for the input figures.
    from convexdecomp.osm.preprocess import bem4ai_comparison, load_sample, local_frame_step
    raw = local_frame_step(load_sample(SAMPLE))
    raw = raw.join(bem4ai_comparison(raw)).set_index("sample_id", drop=False)
    raw["n_vertices_raw"] = raw["geom_local_raw"].map(count_exterior_vertices)
    raw["n_reflex_raw"] = reflex_vertex_counts(raw["geom_local_raw"].to_numpy())

    # Per step: what changed, from one geometry to the next, on the footprints that passed step 4.
    passed = gdf
    steps = [("geom_local_raw", "geom_local_vertices", "3 vertices"), ("geom_local_vertices", "geom_local_thin", "4 thin parts"),
             ("geom_local_thin", "geom_local", "6 normalization")]
    step_rows = []
    for a, b, name in steps:
        A, B = passed[a].to_numpy(), passed[b].to_numpy()
        sa, sb = footprint_shapes(A), footprint_shapes(B)
        moved = shapely.hausdorff_distance(A, B)
        area = 100 * np.abs(shapely.area(B) - shapely.area(A)) / shapely.area(A)
        dv = sb["n_footprint_vertices"].to_numpy() - sa["n_footprint_vertices"].to_numpy()
        ra, rb = reflex_vertex_counts(A), reflex_vertex_counts(B)
        sharper = sb["min_corner_angle_deg"].to_numpy() < sa["min_corner_angle_deg"].to_numpy() - 5
        step_rows.append({"step": name, "changed": int((moved > 1e-6).sum()), "vertices_removed": int(-dv[dv < 0].sum()),
                          "vertices_added": int(dv[dv > 0].sum()), "reflex_removed": int((ra - rb)[ra > rb].sum()),
                          "moved_over_0_3": int((moved > 0.3).sum()), "max_moved_m": round(float(moved.max()), 2),
                          "area_over_1pct": int((area > 1).sum()), "corner_sharper_5deg": int(sharper.sum())})

    steps_before = np.array([residual_steps(p) for p in passed["geom_local_thin"]])
    edges = [np.hypot(*np.diff(np.asarray(p.exterior.coords), axis=0).T).min() for p in final["geom_local"]]
    numbers = {
        "footprints": len(raw), "cities": int(raw["city_key"].nunique()), "countries": int(raw["country"].nunique()),
        "holes": raw["geom_local_raw"].map(lambda p: len(p.interiors)).value_counts().sort_index().to_dict(),
        "area_m2": raw["geom_local_raw"].map(lambda p: p.area).describe().round(1).to_dict(),
        "vertices_raw": raw["n_vertices_raw"].describe().round(2).to_dict(),
        "reflex_raw": raw["n_reflex_raw"].describe().round(2).to_dict(),
        "convex_raw": int((raw["n_reflex_raw"].eq(0) & raw["geom_local_raw"].map(lambda p: len(p.interiors) == 0)).sum()),
        "outlines_invalid": int((~raw["outline_valid"]).sum()),
        "bem4ai_changed": int((raw["bem4ai_hausdorff_m"] > 1e-6).sum()),
        "bem4ai_vertices": [int(raw["vertices_osm"].sum()), int(raw["vertices_bem4ai"].sum())],
        "bem4ai_hausdorff_q": raw["bem4ai_hausdorff_m"].quantile([0.5, 0.9, 0.99, 1.0]).round(3).tolist(),
        "bem4ai_area_abs_max_pct": round(float(raw["bem4ai_area_change_pct"].abs().max()), 3),
        "vertices_removed": int(gdf["vertices_removed"].sum()) if "vertices_removed" in gdf else 0,
        "vertices_removed_footprints": int((gdf["vertices_removed"] > 0).sum()),
        "thin_cut": int((gdf["thin_part_share"] > 0).sum()),
        "thin_cut_m2": float((gdf["geom_local_vertices"].map(lambda p: p.area) - gdf["geom_local_thin"].map(lambda p: p.area)).sum()),
        "removed_thin_parts": int(funnel["left_at"].eq("thin parts").sum()),
        "removed_thin_parts_ids": funnel.loc[funnel["left_at"].eq("thin parts"), "sample_id"].tolist(),
        "removed_checks": int(funnel["left_at"].eq("checks").sum()),
        "axes_status": gdf["axes_status"].value_counts().to_dict(),
        "axes_kept_ids": gdf.loc[gdf["axes_status"].str.startswith("kept"), "sample_id"].tolist(),
        "directions_mean": round(float(gdf["n_directions"].mean()), 2), "lines_mean": round(float(gdf["n_wall_lines"].mean()), 2),
        "steps_before_axes": [int(steps_before.sum()), int((steps_before > 0).sum())],
        "steps_after_axes": int(final["residual_steps"].sum()),
        "working_set": len(run.working_set),
        "convex_final": int((final["n_reflex"].eq(0) & final["geom_local"].map(lambda p: len(p.interiors) == 0)).sum()),
        "reflex_mean": [round(float(final["n_reflex_raw"].mean()), 2), round(float(final["n_reflex"].mean()), 2)],
        "vertices_median": [int(final["geom_local_raw"].map(count_exterior_vertices).median()),
                            int(final["geom_local"].map(count_exterior_vertices).median())],
        "hausdorff_q": final["fidelity_hausdorff_m"].quantile([0.5, 0.9, 0.99, 1.0]).round(3).tolist(),
        "area_change_abs_q": final["fidelity_area_change_pct"].abs().quantile([0.5, 0.9, 0.99, 1.0]).round(2).tolist(),
        "iou_min": round(float(final["fidelity_iou"].min()), 4),
        "moved_over_0_5": int((final["fidelity_hausdorff_m"] > 0.5).sum()),
        "moved_over_1": int((final["fidelity_hausdorff_m"] > 1.0).sum()),
        "shortest_edge_m": round(float(min(edges)), 3),
        "steps": step_rows,
    }
    study = parameter_study(passed["geom_local_thin"].to_numpy())
    numbers["parameter_study"] = study.round(3).to_dict(orient="records")
    (args.out / "osm_adapter_numbers.json").write_text(json.dumps(numbers, indent=1, default=str))
    print(json.dumps({k: v for k, v in numbers.items() if k not in ("steps", "parameter_study")}, indent=1, default=str))
    print(pd.DataFrame(step_rows).to_string(index=False))
    print(study.round(3).to_string(index=False))

    # ----- figures
    (args.out / "osm_adapter_pipeline.svg").write_text(pipeline_svg(numbers))
    print("wrote osm_adapter_pipeline")
    types = pd.read_csv(args.types).set_index("sample_id")["building_type"].reindex(raw.index).dropna() if args.types else None
    fig_sample_overview(raw, types, args.out)
    fig_gallery(raw, args.out)

    changed = raw.loc[raw["bem4ai_hausdorff_m"] > 1e-6]
    bem_ids = changed.sort_values("bem4ai_hausdorff_m", ascending=False).index[:4].tolist() + \
        changed.assign(dv=changed["vertices_osm"] - changed["vertices_bem4ai"]).sort_values("dv", ascending=False).index[:4].tolist()
    bem_ids = list(dict.fromkeys(bem_ids))[:8]
    fig_before_after(raw.loc[bem_ids], "geom_local_raw", "geom_local_bem4ai", args.out, "osm_bem4ai_comparison",
                     lambda sid, r: f"{sid}\n{r['vertices_osm']} → {r['vertices_bem4ai']} vertices, moved {r['bem4ai_hausdorff_m']:.2f} m")

    v_ids = gdf.sort_values("vertices_removed", ascending=False).index[:8].tolist()
    fig_before_after(gdf.loc[v_ids], "geom_local_raw", "geom_local_vertices", args.out, "osm_vertices_examples",
                     lambda sid, r: f"{sid}\n{count_exterior_vertices(r['geom_local_raw'])} → "
                                    f"{count_exterior_vertices(r['geom_local_vertices'])} vertices")

    # The footprints the thin-part check removes: their state before step 4 and the cut.
    from convexdecomp.osm.preprocess import thin_parts_step, vertices_step
    removed = raw.loc[numbers["removed_thin_parts_ids"]]
    removed = thin_parts_step(vertices_step(removed, AdapterSettings()), rules, AdapterSettings()).set_index("sample_id", drop=False)
    removed = removed.sort_values("thin_part_share", ascending=False)
    fig_before_after(removed, "geom_local_vertices", "geom_local_thin", args.out, "osm_thin_part_check", dots=False, ncols=4,
                     titles=lambda sid, r: f"{sid}\n{r['thin_part_share']:.1%} off" + (", split or courtyard opened"
                                                                                      if r["thin_part_topology_changed"] else ""))
    cut = gdf.assign(cut_m2=gdf["geom_local_vertices"].map(lambda p: p.area) - gdf["geom_local_thin"].map(lambda p: p.area))
    t_ids = cut.loc[cut["cut_m2"] > 0].sort_values("cut_m2", ascending=False).index[:8].tolist()
    fig_before_after(cut.loc[t_ids], "geom_local_vertices", "geom_local_thin", args.out, "osm_thin_parts_examples",
                     lambda sid, r: f"{sid}\n{r['cut_m2']:.1f} m² cut ({r['thin_part_share']:.1%})")

    moved = gdf.assign(axes_moved=shapely.hausdorff_distance(gdf["geom_local_thin"].to_numpy(), gdf["geom_local"].to_numpy()))
    a_pool = moved.loc[moved["n_directions"].between(2, 4) & moved["geom_local_thin"].map(count_exterior_vertices).between(8, 20)]
    a_ids = a_pool.sort_values("axes_moved", ascending=False).index[:4].tolist()
    fig_axes(moved.loc[a_ids], args.out, AdapterSettings().axes)
    n_ids = moved.sort_values("axes_moved", ascending=False).index[:8].tolist()
    fig_before_after(moved.loc[n_ids], "geom_local_thin", "geom_local", args.out, "osm_normalization_examples",
                     lambda sid, r: f"{sid}\n{count_exterior_vertices(r['geom_local_thin'])} → "
                                    f"{count_exterior_vertices(r['geom_local'])} vertices, moved {r['axes_moved']:.2f} m")
    fig_parameter_study(study, args.out)
    fig_fidelity(final, args.out)
    seq = list(dict.fromkeys([
        final.sort_values("fidelity_hausdorff_m", ascending=False).index[0],
        moved.loc[final.index].sort_values("axes_moved", ascending=False).index[0],
        final.loc[final["geom_local"].map(lambda p: len(p.interiors) > 0)].index[0],
        final.iloc[(final["n_reflex"] - 4).abs().argsort()].index[0],
    ]))
    fig_stage_sequence(gdf, seq, args.out)
    fig_working_set_complexity(final, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
