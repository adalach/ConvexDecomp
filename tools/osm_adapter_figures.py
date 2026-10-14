#!/usr/bin/env python3
"""Figures and numbers of the OSM adapter note: the BEM4AI footprint sample through every preprocessing step.

Runs the preprocessing of ``OSM_1_Preprocessing.ipynb`` (level L2, the notebook's settings) on the
BEM4AI sample, keeps every intermediate geometry, and writes one SVG per figure plus
``osm_adapter_numbers.json`` with the counts the note quotes::

    python tools/osm_adapter_figures.py --out /path/to/attachments
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import geopandas as gpd  # noqa: E402
import matplotlib as mpl  # noqa: E402

mpl.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from convexdecomp.osm.axis_alignment import EdgeAlignmentConfig, alignment_axis_records_for_polygon  # noqa: E402
from convexdecomp.osm.footprints import footprint_shapes  # noqa: E402
from convexdecomp.osm.local_frames import FacadeOffsetConfig, clean_polygonal, clean_polygonal_in_one_piece  # noqa: E402
from convexdecomp.osm.normalize import FootprintNormalizationConfig, count_exterior_vertices  # noqa: E402
from convexdecomp.osm.preprocess import (GeometrySettings, check_footprints, clean_mapping_artefacts,  # noqa: E402
                                         fidelity, load_footprints, reflex_vertex_counts, regularize_geometry)
from convexdecomp.osm.rules import FOOTPRINT_STAGE, PREPROCESSED_STAGE, load_rules  # noqa: E402

SAMPLE = ROOT / "data" / "bem4ai" / "osm_footprints_bem4ai_sample.gpkg"

TUM_BLUE, LIGHT_BLUE, PALE_BLUE = "#0065BD", "#98C6EA", "#EAF4FB"
GREY, MID_GREY, ORANGE, AXES = "#D9DADB", "#9C9E9F", "#E37222", "#5C6770"
CYCLE = ["#1F4E79", "#C17C00", "#2A9D8F", "#8E44AD", "#00B4D8"]

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["TUM Neue Helvetica", "Helvetica Neue", "Helvetica", "Arial", "TeX Gyre Heros"],
    "font.size": 9, "axes.titlesize": 8.5, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": AXES, "axes.linewidth": 0.8,
    "axes.grid": True, "grid.color": GREY, "grid.linestyle": ":", "grid.linewidth": 0.6,
    "axes.prop_cycle": mpl.cycler(color=CYCLE),
    "lines.linewidth": 1.6, "legend.frameon": False,
    "svg.fonttype": "none", "savefig.bbox": "tight",
})


def geometry_settings(rules) -> GeometrySettings:
    """The geometry settings of ``OSM_1_Preprocessing.ipynb``."""
    return GeometrySettings(
        normalization=FootprintNormalizationConfig(
            simplify_tolerance_m=0.2, snap_tolerance_m=0.05, min_edge_length_m=0.75, collinear_distance_tol_m=0.3,
            collinear_angle_tol_deg=10.0, area_change_ratio_tol=0.03, area_change_m2_tol=3.0, max_hausdorff_m=0.5,
            max_passes=8),
        alignment=EdgeAlignmentConfig(
            use_angle_families=True, family_angle_tol_deg=3.0, line_angle_tol_deg=2.0, line_offset_tol_m=0.2,
            collinear_tol_deg=1.0, min_seg_len_m=0.05, max_vertex_snap_shift_m=0.4, align_min_area_ratio=0.97,
            align_max_area_ratio=1.03, align_max_edge_stretch=2.0, align_max_hausdorff_m=0.5),
        thin_part_shrink_m=float(rules.thresholds["footprint"]["thin_part_width_m"]) / 2.0,
        facade_offsets=FacadeOffsetConfig(max_offset_m=0.5, angle_tol_deg=0.5, max_vertex_shift_m=1.0,
                                          max_area_change_ratio=0.05),
        min_piece_area_m2=2.0,
    )


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
            pts = np.asarray(poly.exterior.coords[:-1])
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


# --------------------------------------------------------------------------------------------------- figures
def fig_sample_overview(raw, bem_types, out):
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.6))
    counts = raw["country"].value_counts().sort_values()
    ax = axes[0, 0]
    ax.barh(counts.index, counts.to_numpy(), color=TUM_BLUE, height=0.7)
    ax.set_xlabel("Footprints")
    ax.tick_params(axis="y", labelsize=6.5)
    ax.grid(axis="y", visible=False)
    ax = axes[0, 1]
    types = bem_types.value_counts().sort_values()
    ax.barh(types.index, types.to_numpy(), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6, height=0.6)
    ax.set_xlabel("Footprints")
    ax.grid(axis="y", visible=False)
    ax = axes[1, 0]
    area = raw["geom_local_raw"].map(lambda g: g.area)
    ax.hist(area, bins=np.logspace(np.log10(20), np.log10(2100), 30), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    ax.set_xscale("log")
    ax.set_xlabel("Footprint area (m²)")
    ax.set_ylabel("Footprints")
    ax = axes[1, 1]
    v = np.minimum(raw["n_vertices_raw"], 50)
    r = np.minimum(raw["n_reflex_raw"], 50)
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
    pick = raw.sample(n=n, random_state=seed).sort_values("geom_local_raw", key=lambda s: s.map(lambda g: g.area))
    fig, axes = panels(n, 6, size=1.15)
    for ax, (_, row) in zip(axes, pick.iterrows()):
        draw(ax, row["geom_local_raw"], face=LIGHT_BLUE, edge=TUM_BLUE, lw=0.7)
        frame(ax, None, half_extent=half)
        ax.text(-half, -half, row["sample_id"].rsplit("_", 1)[0][:14], fontsize=5.5, color=AXES, va="bottom")
    ax = axes[0]
    ax.plot([-half + 2, -half + 22], [half - 5, half - 5], color="black", lw=1.2)
    ax.text(-half + 12, half - 3, "20 m", ha="center", va="bottom", fontsize=6)
    fig.subplots_adjust(wspace=0.05, hspace=0.05)
    save(fig, out, "osm_sample_gallery")


def fig_before_after(rows, before_col, after_col, out, stem, titles, *, ncols=4, dots=True, removed=True,
                     marks=None, size=1.9):
    fig, axes = panels(len(rows), ncols, size=size)
    for ax, (sid, row) in zip(axes, rows.iterrows()):
        before, after = row[before_col], row[after_col]
        draw(ax, before, face=GREY, edge=MID_GREY, lw=0.6, dots=dots, z=1)
        if removed:
            draw(ax, removed_part(before, after), face=ORANGE, edge="none", alpha=0.85, z=1.5)
        draw(ax, after, face="none", edge=TUM_BLUE, lw=1.1, dots=dots, z=3)
        if marks is not None and sid in marks:
            x, y = marks[sid]
            ax.plot([x], [y], "o", ms=9, mfc="none", mec=ORANGE, mew=1.4, zorder=5)
        frame(ax, [before, after])
        label(ax, titles(sid, row))
    fig.tight_layout()
    save(fig, out, stem)


def sharpest_corner_point(geom):
    pts = np.asarray(shapely.geometry.polygon.orient(geom, 1.0).exterior.coords[:-1])
    v1 = pts - np.roll(pts, 1, axis=0)
    v2 = np.roll(pts, -1, axis=0) - pts
    turn = np.degrees(np.arctan2(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0], (v1 * v2).sum(axis=1)))
    inside = 180.0 - turn
    k = int(np.argmin(inside))
    return tuple(pts[k]), float(inside[k])


def shortest_edge(geom):
    pts = np.asarray(geom.exterior.coords)
    return float(np.hypot(*np.diff(pts, axis=0).T).min())


def fig_alignment_axes(m, ids, cfg, out):
    fig, axes = panels(len(ids), 4, size=1.9)
    for ax, sid in zip(axes, ids):
        row = m.loc[sid]
        geom = row["geometry_before_align"]
        records, diag = alignment_axis_records_for_polygon(geom, cfg)
        families = sorted({round(r["family_angle_deg"], 1) for r in records})
        colours = {f: CYCLE[k % len(CYCLE)] for k, f in enumerate(families)}
        draw(ax, row["geom_local_preray"], face=PALE_BLUE, edge=TUM_BLUE, lw=0.9, z=2)
        for r in records:
            x, y = r["geometry"].xy
            ax.plot(x, y, ls="--", lw=0.7, color=colours[round(r["family_angle_deg"], 1)], zorder=3)
        frame(ax, [geom])
        label(ax, f"{sid}\n{diag['n_angle_families']} families, {diag['n_axes']} lines, moved {row['align_moved']:.2f} m")
    fig.tight_layout()
    save(fig, out, "osm_alignment_axes")


def fig_stage_sequence(m, ids, out):
    stages = [("geom_local_raw", "OSM outline"), ("geom_local_clean", "mapping cleanup"),
              ("geometry_before_align", "normalization"), ("geom_local_preray", "alignment"),
              ("geom_thin", "thin parts"), ("geom_local", "facade offsets")]
    fig, axes = plt.subplots(len(ids), len(stages), figsize=(1.35 * len(stages), 1.45 * len(ids)), squeeze=False)
    for i, (axes_row, sid) in enumerate(zip(axes, ids)):
        row = m.loc[sid]
        geoms = [row[c] for c, _ in stages]
        for k, (ax, (column, title)) in enumerate(zip(axes_row, stages)):
            draw(ax, row["geom_local_raw"], face="none", edge=MID_GREY, lw=0.5, z=1)
            draw(ax, row[column], face=PALE_BLUE, edge=TUM_BLUE, lw=0.8, dots=True, z=2)
            frame(ax, geoms)
            n = count_exterior_vertices(row[column])
            ax.set_title(f"{title}\n{n} vertices" if i == 0 else f"{n} vertices", fontsize=6.5, pad=2)
        axes_row[0].text(-0.05, 0.5, sid, transform=axes_row[0].transAxes, rotation=90, ha="right", va="center",
                         fontsize=6.5)
    fig.tight_layout()
    save(fig, out, "osm_stage_sequence")


def fig_fidelity(final, out):
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.3))
    h = final["fidelity_hausdorff_m"].clip(lower=1e-3)
    axes[0].hist(h, bins=np.logspace(-3, 1, 32), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    axes[0].set_xscale("log")
    axes[0].set_xlabel("Largest move from the OSM outline (m)")
    axes[0].set_ylabel("Footprints")
    a = final["fidelity_area_change_pct"]
    axes[1].hist(a, bins=np.linspace(-4, 4, 33), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    axes[1].set_xlabel("Area change (%)")
    axes[1].set_yscale("log")
    i = final["fidelity_iou"]
    axes[2].hist(i, bins=np.linspace(0.93, 1.0, 29), color=LIGHT_BLUE, edgecolor=TUM_BLUE, linewidth=0.6)
    axes[2].set_xlabel("Intersection over union")
    axes[2].set_yscale("log")
    fig.tight_layout()
    save(fig, out, "osm_fidelity")


def fig_working_set_complexity(final, out):
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.5))
    for ax, (before, after, xlabel) in zip(axes, [("n_vertices_raw", "n_vertices_final", "Exterior vertices"),
                                                 ("n_reflex_raw", "n_reflex_final", "Reflex corners")]):
        cap = 40
        bins = np.arange(-0.5, cap + 1.5, 1)
        b, a = np.minimum(final[before], cap), np.minimum(final[after], cap)
        ax.hist(b, bins=bins, histtype="step", color=CYCLE[1], label=f"OSM outline (median {int(final[before].median())})")
        ax.hist(a, bins=bins, histtype="step", color=CYCLE[0], label=f"forwarded (median {int(final[after].median())})")
        ax.set_yscale("log")
        ax.set_xlabel(f"{xlabel} (values above {cap} counted at {cap})")
        ax.set_ylabel("Footprints (log)")
        ax.legend()
    fig.tight_layout()
    save(fig, out, "osm_working_set_complexity")


# ------------------------------------------------------------------------------------------- pipeline diagram
FONT = "'TUM Neue Helvetica', 'TUMNeueHelvetica', 'Helvetica Neue', HelveticaNeue, Helvetica, Arial, sans-serif"


def pipeline_svg(numbers: dict) -> str:
    """The OSM adapter as a pipeline of 12 steps in three rows (mm units, 190 mm wide)."""
    removed3 = sum(numbers["removed_footprint_checks"].values())
    removed4 = sum(numbers["removed_preprocessed_checks"].values())
    steps = [
        ("BEM4AI STAGE 3", "SOURCE", ["20,000 OSM footprints,", "745 cities, 27 countries,", "geometry rules applied", "at the draw"], "source"),
        ("IMPORT", "TOOL", [f"{numbers['footprints']:,} drawn at random,", "OSM outline read again", "and checked against", "its fingerprint"], "step"),
        ("LOCAL FRAME", "L0", ["UTM zone of the city,", "bounding-box centre at 0,", "outline repaired", "geom_local_raw"], "step"),
        ("MAPPING CLEANUP", "L1", ["parts and holes under 1 m", "cut or filled, vertices", "within 5 cm dropped", "geom_local_clean"], "step"),
        ("FOOTPRINT CHECKS", "ALL LEVELS", ["one polygon, sliver, area,", "holes, vertices, corners,", "thin parts at most 2 %", f"{removed3} removed"], "check"),
        ("REVIEW LOG", "ALL LEVELS", ["manual remove or keep,", "keyed by fingerprint,", "overrides the checks", "no decisions yet"], "step"),
        ("NORMALIZATION", "L1", ["simplify 0.2 m, short", "edges, near-collinear runs;", "area 3 %, move 0.5 m", "at most"], "step"),
        ("ALIGNMENT", "L2", ["angle families 3°,", "wall lines 2° and 0.2 m,", "vertices snap 0.4 m", "geom_local_preray"], "step"),
        ("THIN PARTS", "L2", ["shrink and expand 1 m:", "parts under 2 m erased,", "footprint stays one", "polygon"], "step"),
        ("FACADE OFFSETS", "L2", ["runs at most 0.5 m apart", "and within 0.5° become", "one wall", "geom_local"], "step"),
        ("CHECKS AGAIN", "ALL LEVELS", ["the same checks on the", "final footprint; how far", "it moved is measured", f"{removed4} removed"], "check"),
        ("WORKING SET", "TO OSM_2", ["subset_buildings_gdf.pkl", f"{numbers['working_set']} footprints in local", "metres, to perimeter", "zoning and the engine"], "output"),
    ]
    W, margin, cols, gap_x = 190.0, 1.0, 4, 8.0
    bw = (W - 2 * margin - (cols - 1) * gap_x) / cols
    th, ph, gap_y = 10.0, 15.0, 9.0
    bh = th + ph
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{margin * 2 + 3 * bh + 2 * gap_y:.1f}mm" '
           f'viewBox="0 0 {W} {margin * 2 + 3 * bh + 2 * gap_y:.1f}" font-family="{FONT}">',
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
            mono = line.startswith(("geom_", "subset_"))
            style = ' font-style="italic"' if mono else ""
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


# ------------------------------------------------------------------------------------------------------ main
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--types", type=Path, help="CSV with sample_id and building_type (from BEM4AI)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    warnings.filterwarnings("ignore")

    rules = load_rules()
    settings = geometry_settings(rules)
    raw = load_footprints(SAMPLE)
    raw["n_vertices_raw"] = raw["geom_local_raw"].map(count_exterior_vertices)
    raw["n_reflex_raw"] = reflex_vertex_counts(raw["geom_local_raw"].to_numpy())
    bem = gpd.read_file(SAMPLE, layer="footprints_bem4ai").set_index("sample_id").geometry

    g = clean_mapping_artefacts(raw, rules, "L2")
    c3 = check_footprints(g, "geom_local_clean", rules, FOOTPRINT_STAGE)
    g = g.join(c3[["hard_failures", "review_flags", "thin_part_share", "min_corner_angle_deg"]])
    removed3 = g.loc[g["hard_failures"].ne("")]
    m = regularize_geometry(g.loc[g["hard_failures"].eq("")], "L2", settings)
    m["geom_thin"] = [clean_polygonal_in_one_piece(p, settings.thin_part_shrink_m, settings.thin_part_shrink_m,
                                                   settings.min_piece_area_m2)[0] for p in m["geom_local_preray"]]
    c4 = check_footprints(m, "geom_local", rules, PREPROCESSED_STAGE)
    m["hard_failures_final"] = c4["hard_failures"]
    m["min_corner_final"] = c4["min_corner_angle_deg"]
    m = m.join(fidelity(m["geom_local_raw"], m["geom_local"]).set_axis(m.index))
    m["n_vertices_final"] = m["geom_local"].map(count_exterior_vertices)
    m["n_reflex_final"] = reflex_vertex_counts(m["geom_local"].to_numpy())
    m = m.set_index("sample_id", drop=False)
    removed4 = m.loc[m["hard_failures_final"].ne("")]
    final = m.loc[m["hard_failures_final"].eq("")]

    # The ported mapping cleanup against BEM4AI's own cleaned footprints.
    bem_clean = bem.reindex(g["sample_id"]).to_numpy()
    parity = shapely.hausdorff_distance(g["geom_local_clean"].to_numpy(), bem_clean)

    numbers = {
        "footprints": len(raw), "cities": int(raw["city_key"].nunique()), "countries": int(raw["country"].nunique()),
        "holes": raw["geom_local_raw"].map(lambda p: len(p.interiors)).value_counts().sort_index().to_dict(),
        "area_m2": raw["geom_local_raw"].map(lambda p: p.area).describe().round(1).to_dict(),
        "vertices_raw": raw["n_vertices_raw"].describe().round(2).to_dict(),
        "reflex_raw": raw["n_reflex_raw"].describe().round(2).to_dict(),
        "convex_raw": int((raw["n_reflex_raw"].eq(0) & raw["geom_local_raw"].map(lambda p: len(p.interiors) == 0)).sum()),
        "parity_max_m": float(parity.max()),
        "cleanup_cut": int((g["cleanup_cut_m2"] > 0).sum()), "cleanup_cut_m2": float(g["cleanup_cut_m2"].sum()),
        "cleanup_filled": int((g["cleanup_filled_m2"] > 0).sum()),
        "cleanup_vertices_dropped": int((g["n_osm_vertices"] - g["geom_local_clean"].map(count_exterior_vertices)).sum()),
        "cleanup_changed": int((g["n_osm_vertices"] - g["geom_local_clean"].map(count_exterior_vertices)).gt(0).sum()),
        "removed_footprint_checks": removed3["hard_failures"].value_counts().to_dict(),
        "review_flags_footprint_checks": g["review_flags"].ne("").sum().item(),
        "normalized": int((m["vertex_delta_norm"] != 0).sum()),
        "aligned": int(m["alignment_changed"].sum()),
        "cleanup_mode": m["cleanup_mode"].value_counts().to_dict(),
        "facade_status": m["facade_status"].value_counts().to_dict(),
        "removed_preprocessed_checks": removed4["hard_failures_final"].value_counts().to_dict(),
        "removed_preprocessed_ids": removed4.index.tolist(),
        "working_set": len(final),
        "convex_final": int((final["n_reflex_final"].eq(0) & final["geom_local"].map(lambda p: len(p.interiors) == 0)).sum()),
        "vertices_final": final["n_vertices_final"].describe().round(2).to_dict(),
        "reflex_final": final["n_reflex_final"].describe().round(2).to_dict(),
        "hausdorff_q": final["fidelity_hausdorff_m"].quantile([0.5, 0.9, 0.99, 1.0]).round(3).tolist(),
        "area_change_abs_q": final["fidelity_area_change_pct"].abs().quantile([0.5, 0.9, 0.99, 1.0]).round(2).tolist(),
        "iou_min": round(float(final["fidelity_iou"].min()), 4),
        "moved_over_0_5": int((final["fidelity_hausdorff_m"] > 0.5).sum()),
        "moved_over_1": int((final["fidelity_hausdorff_m"] > 1.0).sum()),
    }

    # Per step: what changed, from one geometry to the next.
    steps = [("geom_local_raw", "geom_local_clean", "mapping cleanup"), ("geom_local_clean", "geometry_before_align", "normalization"),
             ("geometry_before_align", "geom_local_preray", "alignment"), ("geom_local_preray", "geom_thin", "thin parts"),
             ("geom_thin", "geom_local", "facade offsets")]
    rows = []
    for a, b, name in steps:
        A, B = m[a].to_numpy(), m[b].to_numpy()
        sa, sb = footprint_shapes(A), footprint_shapes(B)
        ra, rb = reflex_vertex_counts(A), reflex_vertex_counts(B)
        moved = shapely.hausdorff_distance(A, B)
        area = 100 * np.abs(shapely.area(B) - shapely.area(A)) / shapely.area(A)
        dv = sb["n_footprint_vertices"].to_numpy() - sa["n_footprint_vertices"].to_numpy()
        sharper = (sb["min_corner_angle_deg"].to_numpy() < sa["min_corner_angle_deg"].to_numpy() - 5)
        rows.append({"step": name, "changed": int((moved > 1e-6).sum()), "vertices_removed": int(-dv[dv < 0].sum()),
                     "vertices_added": int(dv[dv > 0].sum()), "reflex_removed": int((ra - rb)[ra > rb].sum()),
                     "moved_over_0_3": int((moved > 0.3).sum()), "max_moved_m": round(float(moved.max()), 2),
                     "area_over_1pct": int((area > 1).sum()), "corner_sharper_5deg": int(sharper.sum())})
    numbers["steps"] = rows
    (args.out / "osm_adapter_numbers.json").write_text(json.dumps(numbers, indent=1, default=str))
    print(json.dumps(numbers, indent=1, default=str))

    # ----- figures
    (args.out / "osm_adapter_pipeline.svg").write_text(pipeline_svg(numbers))
    print("wrote osm_adapter_pipeline")
    types = pd.read_csv(args.types).set_index("sample_id")["building_type"] if args.types else None
    if types is not None:
        fig_sample_overview(raw, types.reindex(raw["sample_id"]).dropna(), args.out)
    fig_gallery(raw, args.out)

    # BEM4AI Stage 3 mapping cleanup: the largest cuts and the most vertices dropped.
    g2 = g.set_index("sample_id", drop=False)
    g2["dropped"] = g2["n_osm_vertices"] - g2["geom_local_clean"].map(count_exterior_vertices)
    cut_ids = g2.sort_values("cleanup_cut_m2", ascending=False).index[:4].tolist()
    drop_ids = g2.loc[~g2.index.isin(cut_ids)].sort_values("dropped", ascending=False).index[:4].tolist()
    fig_before_after(g2.loc[cut_ids + drop_ids], "geom_local_raw", "geom_local_clean", args.out, "osm_mapping_cleanup",
                     lambda sid, r: f"{sid}\ncut {r['cleanup_cut_m2']:.1f} m², {int(r['dropped'])} vertices dropped")

    # The footprints the thin-part check removes.
    thin = g2.loc[g2["hard_failures"].str.contains("footprint_thin_parts")].sort_values("thin_part_share", ascending=False)
    thin = thin.assign(opened=[clean_polygonal(p, settings.thin_part_shrink_m, settings.thin_part_shrink_m, 2.0)
                               for p in thin["geom_local_clean"]])
    fig_before_after(thin, "geom_local_clean", "opened", args.out, "osm_thin_part_check", dots=False,
                     titles=lambda sid, r: f"{sid}\nloses {r['thin_part_share']:.1%}")

    # Normalization: the largest vertex reductions, and the corners it sharpens.
    norm_ok = m.loc[m["vertex_delta_norm"] < 0].sort_values("vertex_delta_norm", kind="stable").index[:4].tolist()
    norm_issue = [sid for sid in ("Torun_0010", "Paris_0081", "Paris_0079", "Leon_0011") if sid in m.index]
    fig_before_after(m.loc[norm_ok], "geom_local_clean", "geometry_before_align", args.out, "osm_normalization_examples",
                     lambda sid, r: f"{sid}\n{count_exterior_vertices(r['geom_local_clean'])} → "
                                    f"{count_exterior_vertices(r['geometry_before_align'])} vertices")
    marks = {sid: sharpest_corner_point(m.at[sid, "geometry_before_align"])[0] for sid in norm_issue}
    fig_before_after(m.loc[norm_issue], "geom_local_clean", "geometry_before_align", args.out, "osm_normalization_issues",
                     lambda sid, r: f"{sid}\nsharpest corner {sharpest_corner_point(r['geom_local_clean'])[1]:.0f}° → "
                                    f"{sharpest_corner_point(r['geometry_before_align'])[1]:.0f}°", marks=marks)

    # Alignment: angle families and lines.
    m["align_moved"] = shapely.hausdorff_distance(m["geometry_before_align"].to_numpy(), m["geom_local_preray"].to_numpy())
    pool = m.loc[m["n_angle_families_align"].between(2, 4)
                 & m["geometry_before_align"].map(count_exterior_vertices).between(8, 20)]
    align_ids = pool.sort_values("align_moved", ascending=False).index[:4].tolist()
    fig_alignment_axes(m, align_ids, settings.alignment, args.out)

    # Thin parts: stubs erased, and the debris the shrink and expand leaves.
    lost = (shapely.area(m["geom_local_preray"].to_numpy()) - shapely.area(m["geom_thin"].to_numpy()))
    m["thin_lost_m2"] = lost
    stub_ids = m.loc[m["thin_lost_m2"] > 0.5].sort_values("thin_lost_m2", ascending=False).index[:4].tolist()
    fig_before_after(m.loc[stub_ids], "geom_local_preray", "geom_thin", args.out, "osm_thin_parts_examples",
                     lambda sid, r: f"{sid}\n{r['thin_lost_m2']:.1f} m² erased")
    debris = [sid for sid in ("Saint_Etienne_0001", "Rzeszow_0026", "Corby_0008") if sid in m.index]
    dmarks = {sid: sharpest_corner_point(m.at[sid, "geom_thin"])[0] for sid in debris}
    fig_before_after(m.loc[debris], "geom_local_preray", "geom_thin", args.out, "osm_thin_parts_debris", ncols=3,
                     titles=lambda sid, r: f"{sid}\nsharpest corner {sharpest_corner_point(r['geom_local_preray'])[1]:.0f}° → "
                                           f"{sharpest_corner_point(r['geom_thin'])[1]:.0f}°, "
                                           f"shortest edge {shortest_edge(r['geom_thin']):.0e} m", marks=dmarks, size=2.4)

    # Facade offsets: the largest moves.
    fo = m.loc[m["facade_status"].eq("closed")].copy()
    fo["moved"] = shapely.hausdorff_distance(fo["geom_thin"].to_numpy(), fo["geom_local"].to_numpy())
    fo_ids = fo.sort_values("moved", ascending=False).index[:4].tolist()
    fig_before_after(fo.loc[fo_ids], "geom_thin", "geom_local", args.out, "osm_facade_offsets_examples",
                     lambda sid, r: f"{sid}\nmoved {r['moved']:.2f} m")

    # The footprints the checks remove after preprocessing.
    rmarks = {sid: sharpest_corner_point(m.at[sid, "geom_local"])[0] for sid in removed4.index}
    fig_before_after(m.loc[removed4.index], "geom_local_raw", "geom_local", args.out, "osm_preprocessed_check_removals",
                     lambda sid, r: f"{sid}: {r['hard_failures_final']}\nsharpest corner {r['min_corner_final']:.0f}°",
                     ncols=2, marks=rmarks, size=2.4)

    fig_fidelity(final, args.out)
    seq = list(dict.fromkeys([
        final.sort_values("fidelity_hausdorff_m", ascending=False).index[0],
        "Torun_0010" if "Torun_0010" in final.index else final.index[1],
        final.loc[final["geom_local"].map(lambda p: len(p.interiors) > 0)].index[0],
        final.iloc[(final["n_reflex_final"] - 4).abs().argsort()].index[0],
    ]))
    fig_stage_sequence(m, seq, args.out)
    fig_working_set_complexity(final, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
