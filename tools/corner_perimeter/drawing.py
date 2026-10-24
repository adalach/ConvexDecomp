"""Drawing the corner-ray perimeter with matplotlib: the lines of a result, its zones, legends and windows, and the
bare construction (rays to the facade, rays stopped by rays, rays cut at the band line) for the first figure.

The tools put ``src/`` first on ``sys.path`` (``ROOT``); the footprint sample defaults to ``SAMPLE``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shapely  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from shapely.geometry import LineString, Point  # noqa: E402
from shapely.plotting import plot_line, plot_polygon  # noqa: E402

from convexdecomp.corner_perimeter import CornerRayConfig  # noqa: E402
from convexdecomp.corner_perimeter.geometry import is_convex, points_of  # noqa: E402
from convexdecomp.corner_perimeter.rays import cast, corners, to_band, trim  # noqa: E402

#: the 990-footprint sample every number of the method was measured on (a snapshot, local metres)
SAMPLE = ROOT / "temp" / "perimeter" / "subset_snapshot.pkl"
#: the three test footprints of the method
TEST_IDS = ("Valdemoro_0001", "Krakow_0013", "Bognor_Regis_0005")

RAY_CONVEX, RAY_REFLEX = "#fd8d3c", "#d94801"
BAND_FILL, BAND_LINE = "#deebf7", "#2171b5"
PINK = "#e7298a"
LINE_STYLE = {"corner_line": dict(color="#6a3d9a", linewidth=1.8), "merged_ray": dict(color="#1a9850", linewidth=1.8)}
ZONE_STYLE = {
    "zone": dict(facecolor="#9ecae1", edgecolor="#08306b"),
    "thin": dict(facecolor="#fcbba1", edgecolor="#cb181d"),
    "core": dict(facecolor="#d9d9d9", edgecolor="k", hatch="///"),
}
POINT_STYLE = {
    "ray x ray": dict(color="#cb181d", marker="o", s=16, zorder=6),
    "ray x band": dict(color="#08519c", marker="s", s=16, zorder=7),
    "ray end": dict(facecolor="white", edgecolor="#525252", marker="o", s=14, linewidths=0.8, zorder=5),
}
#: crossing kinds, the first kept where several meet at one place (a ray end where another ray also ends)
KINDS = ("ray end", "ray x band", "ray x ray")

LEGEND = {
    "outline": Line2D([], [], color="k", lw=1.3, label="footprint"),
    "band": Patch(facecolor=BAND_FILL, label=f"{CornerRayConfig().band_m:g} m perimeter band"),
    "band_line": Line2D([], [], color=BAND_LINE, lw=1.1, label="band line"),
    "offset_band_line": Line2D([], [], color="#969696", lw=0.8, ls="--", label="band line before rule 4"),
    "ray": Line2D([], [], color=RAY_CONVEX, lw=0.9, label="ray from a convex corner"),
    "ray_r": Line2D([], [], color=RAY_REFLEX, lw=0.9, label="ray from a reflex corner"),
    "corner_line": Line2D([], [], color="#6a3d9a", lw=1.8, label="corner-to-corner line (rule 2)"),
    "merged_ray": Line2D([], [], color="#1a9850", lw=1.8, label="merged ray (rule 1)"),
    "x_ray": Line2D([], [], ls="none", marker="o", color="#cb181d", label="ray x ray"),
    "x_band": Line2D([], [], ls="none", marker="s", color="#08519c", label="ray x band line"),
    "x_end": Line2D([], [], ls="none", marker="o", mfc="white", mec="#525252", label="ray end on facade"),
    "wing_end": Line2D([], [], ls="none", marker="x", color="#252525", mew=1.4, label="wing-end corner: no ray"),
    "diagonal": Line2D([], [], color=PINK, lw=1.8, label="diagonal replacing a narrow zone (rule 6)"),
    "zone": Patch(facecolor="#9ecae1", edgecolor="#08306b", label="perimeter zone"),
    "thin": Patch(facecolor="#fcbba1", edgecolor="#cb181d", label="zone thinner than 2 m"),
    "nonconvex": Patch(facecolor="white", edgecolor="#08306b", hatch="..", label="zone not convex"),
    "core": Patch(facecolor="#d9d9d9", edgecolor="k", hatch="///", label="core"),
    "group": Line2D([], [], ls="none", marker="o", mfc="none", mec=PINK, label="merged vertex"),
    "cut": Line2D([], [], color=PINK, lw=1.6, ls=(0, (2, 1)), label="cut-off sharp corner"),
}
ZONE_KEYS = ["outline", "zone", "thin", "nonconvex", "core"]


def sample(path: Path | str = SAMPLE) -> pd.DataFrame:
    """The footprint sample, indexed by sample id (a pickled frame or a GeoPackage with ``sample_id``)."""
    path = Path(path)
    if path.suffix == ".gpkg":
        import geopandas as gpd

        return gpd.read_file(path).set_index("sample_id")
    return pd.read_pickle(path).set_index("sample_id")


def letter(ax, i: int) -> None:
    ax.text(0.01, 0.99, f"({'abcdefghijkl'[i]})", transform=ax.transAxes, va="top", ha="left", fontsize=10)


def frame(ax, window) -> None:
    (x0, x1), (y0, y1) = window
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")


def window(c, half_w, half_h=None):
    """The window of half width half_w (and half height half_h) around the point c."""
    half_h = half_w if half_h is None else half_h
    return (c[0] - half_w, c[0] + half_w), (c[1] - half_h, c[1] + half_h)


def whole(poly, margin: float = 1.0):
    """The window around the whole footprint."""
    return (poly.bounds[0] - margin, poly.bounds[2] + margin), (poly.bounds[1] - margin, poly.bounds[3] + margin)


def legend(fig, keys, ncol=None) -> None:
    fig.legend(handles=[LEGEND[k] for k in keys], loc="lower center", ncol=ncol or min(len(keys), 6),
               frameon=False, fontsize=8)


def draw_lines(ax, r, win, old_band: bool = False, diagonals: bool = False) -> None:
    """The construction of a result: band, band line, rays, the lines the rules add, the wing ends."""
    plot_polygon(r.band, ax=ax, add_points=False, facecolor=BAND_FILL, edgecolor="none")
    plot_polygon(r.poly, ax=ax, add_points=False, facecolor="none", edgecolor="k", linewidth=1.3)
    if old_band:
        plot_line(r.offset_band_line, ax=ax, add_points=False, color="#969696", linewidth=0.8, linestyle="--")
    if not r.band_line.is_empty:
        plot_line(r.band_line, ax=ax, add_points=False, color=BAND_LINE, linewidth=1.1)
    for ln in r.lines:
        style = LINE_STYLE.get(ln.kind) or dict(color=RAY_REFLEX if ln.reflex else RAY_CONVEX,
                                                linewidth=0.9 if ln.reflex else 0.8)
        plot_line(ln.geom, ax=ax, add_points=False, **style)
    if diagonals:
        for d in r.diagonals:
            plot_line(d, ax=ax, add_points=False, color=PINK, linewidth=1.8)
    xy = np.array(r.wing_ends).reshape(-1, 2)
    ax.scatter(xy[:, 0], xy[:, 1], marker="x", s=45, color="#252525", linewidths=1.4, zorder=8)
    frame(ax, win)


def draw_zones(ax, r, win, groups=None, cuts: bool = False) -> None:
    """The zones of a result, coloured by kind and dotted where not convex; ``groups`` marks merged vertices and
    ``cuts`` the cut-off sharp corners."""
    (x0, x1), (y0, y1) = win
    box = shapely.box(x0 - 1, y0 - 1, x1 + 1, y1 + 1)  # only what the window shows
    for z, k in r.zones:
        if not z.intersects(box):
            continue
        hatch = ZONE_STYLE[k].get("hatch") or (None if is_convex(z) else "..")
        plot_polygon(z.intersection(box), ax=ax, add_points=False, linewidth=0.7, **{**ZONE_STYLE[k], "hatch": hatch})
    plot_polygon(r.poly.intersection(box), ax=ax, add_points=False, facecolor="none", edgecolor="k", linewidth=1.3)
    if groups is not None:
        xy = np.array(groups).reshape(-1, 2)
        ax.scatter(xy[:, 0], xy[:, 1], s=60, facecolor="none", edgecolor=PINK, linewidths=1.6, zorder=8)
    if cuts:
        for c in r.sharp_cuts:
            plot_line(c, ax=ax, add_points=False, color=PINK, linewidth=2.0, linestyle=(0, (2, 1)))
    frame(ax, win)


def corner_dots(ax, r) -> None:
    for z, _ in r.zones:
        xy = np.asarray(z.exterior.coords)
        ax.scatter(xy[:, 0], xy[:, 1], s=8, color="k", zorder=6)


def zones_title(r) -> str:
    c = r.counts()
    return f"{c['zones']} zones, {c['thin']} thin, {c['not_convex']} not convex"


def construction(poly, mode: str = "band", cfg: CornerRayConfig | None = None):
    """The bare construction before any rule: every ray to the facade (``full``), rays stopped by the first ray
    (``cut``), or also cut at the band line (``band``). Returns the corners, rays, band, band line and the crossing
    points as ``(point, kind)``."""
    cfg = cfg or CornerRayConfig()
    cs = corners(poly, cfg.min_turn_deg)
    core = poly.buffer(-cfg.band_m, join_style="mitre", mitre_limit=10.0)
    band_line = core.boundary if not core.is_empty else LineString()
    rays = [cast(poly, p, d) for p, d, _, _ in cs]
    at_band = [False] * len(rays)
    if mode == "band":
        rays, at_band = to_band(rays, band_line)
    cut = [False] * len(rays)
    if mode != "full":
        rays, cut = trim(cs, rays, cfg.tie_m)
    stop = ["ray" if c else "band" if b else "facade" for c, b in zip(cut, at_band)]
    raw = []
    for i, ray in enumerate(rays):
        if mode == "full":
            for j in range(i + 1, len(rays)):
                for q in points_of(shapely.intersection(ray, rays[j])):
                    # a ray running through another ray's corner is not a crossing
                    if q.distance(Point(ray.coords[0])) > 0.05 and q.distance(Point(rays[j].coords[0])) > 0.05:
                        raw.append((q, "ray x ray"))
        # a stopped ray's end is on the line it stops on only up to float noise
        raw.append((Point(ray.coords[-1]), {"ray": "ray x ray", "band": "ray x band", "facade": "ray end"}[stop[i]]))
        if mode != "band":
            raw += [(q, "ray x band") for q in points_of(shapely.intersection(ray, band_line))]
    kept = []
    for p, k in sorted(raw, key=lambda t: KINDS.index(t[1])):
        if all(p.distance(q) > 0.05 for q, _ in kept):
            kept.append((p, k))
    return cs, rays, poly.difference(core), band_line, kept


def save(fig, out: Path, stem: str, png: Path | None = None) -> None:
    fig.savefig(out / f"{stem}.svg", bbox_inches="tight")
    if png:
        fig.savefig(png / f"{stem}.png", dpi=70, bbox_inches="tight")
    plt.close(fig)
