"""
Notebook support helpers for SVG-based geometry experiments.

These helpers keep plotting and project-path logic out of notebooks so the
notebooks stay focused on parameter choices and pipeline calls.
"""

from __future__ import annotations

import math
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from shapely import affinity


def find_project_root(start: Path) -> Path:
    for path in (start, *start.parents):
        if (path / ".git").exists() and (path / "notebooks").exists():
            return path
    raise FileNotFoundError("Could not locate the FloorplanDecompositionPaper project root.")


def _nice_tick_step(span_m: float) -> float:
    if span_m <= 0:
        return 1.0
    target = span_m / 4.5
    magnitude = 10 ** math.floor(math.log10(target))
    for multiplier in (1, 2, 5, 10):
        step = multiplier * magnitude
        if step >= target:
            return float(step)
    return float(10 * magnitude)


def plot_local_geometry_grid(
    layers: list[dict],
    ids: list[str],
    *,
    id_col: str = "sample_id",
    ncols: int = 4,
    figsize_scale: float = 3.0,
    title_fn=None,
    pad_fraction: float = 0.08,
    min_pad_m: float = 0.25,
    tick_step_m: float | None = None,
    size_label: bool = True,
):
    if not layers:
        raise ValueError("layers must be non-empty")

    ref_layer = layers[0]
    ref_gdf = ref_layer["gdf"]
    ref_id_col = ref_layer.get("id_col", id_col)
    ref_geom_col = ref_layer.get("geometry_col", "geometry")

    ref_bounds: dict[str, tuple[float, float, float, float]] = {}
    spans: list[float] = []
    for sid in ids:
        sub = ref_gdf.loc[ref_gdf[ref_id_col] == sid]
        if sub.empty or ref_geom_col not in sub.columns:
            continue
        geom = sub.iloc[0][ref_geom_col]
        if geom is None or getattr(geom, "is_empty", True):
            continue
        minx, miny, maxx, maxy = geom.bounds
        ref_bounds[sid] = (minx, miny, maxx, maxy)
        spans.append(max(maxx - minx, maxy - miny))

    if not spans:
        raise ValueError("No valid geometries available for plotting.")

    max_span = max(spans)
    pad_m = max(min_pad_m, pad_fraction * max_span)
    panel_span = max_span + 2 * pad_m
    tick_step = tick_step_m or _nice_tick_step(panel_span)

    n = len(ids)
    nrows = max(1, math.ceil(n / ncols))
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(figsize_scale * ncols, figsize_scale * nrows),
        squeeze=False,
    )
    axes_flat = axes.flatten()

    for ax_idx, ax in enumerate(axes_flat):
        if ax_idx >= n:
            ax.axis("off")
            continue

        sid = ids[ax_idx]
        bounds = ref_bounds.get(sid)
        if bounds is None:
            ax.axis("off")
            continue

        minx, miny, maxx, maxy = bounds
        xoff = pad_m - minx
        yoff = pad_m - miny

        for layer in layers:
            layer_gdf = layer["gdf"]
            layer_id_col = layer.get("id_col", id_col)
            geom_col = layer.get("geometry_col", "geometry")
            if geom_col not in layer_gdf.columns:
                continue
            sub = layer_gdf.loc[layer_gdf[layer_id_col] == sid]
            if sub.empty:
                continue

            for geom in sub[geom_col]:
                if geom is None or getattr(geom, "is_empty", True):
                    continue
                translated = affinity.translate(geom, xoff=xoff, yoff=yoff)
                gpd.GeoSeries([translated]).plot(
                    ax=ax,
                    facecolor=layer.get("color", "#d9d9d9"),
                    edgecolor=layer.get("edgecolor", "black"),
                    linewidth=layer.get("linewidth", 0.8),
                    alpha=layer.get("alpha", 1.0),
                    zorder=layer.get("zorder", 1),
                )

        ticks = np.arange(0.0, panel_span + 0.5 * tick_step, tick_step)
        ax.set_xlim(0.0, panel_span)
        ax.set_ylim(0.0, panel_span)
        ax.set_xticks(ticks)
        ax.set_yticks(ticks)
        ax.set_aspect("equal")
        ax.grid(True, color="#e5e5e5", linewidth=0.6, zorder=0)
        ax.set_title(title_fn(sid, ax_idx) if title_fn else str(sid))

        width_m = maxx - minx
        height_m = maxy - miny
        if size_label:
            ax.text(
                0.02,
                0.98,
                f"{width_m:.2f} x {height_m:.2f} m",
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=8,
                bbox={"facecolor": "white", "edgecolor": "#cccccc", "boxstyle": "round,pad=0.2"},
            )

        row = ax_idx // ncols
        col = ax_idx % ncols
        ax.set_xlabel("m" if row == nrows - 1 else "")
        ax.set_ylabel("m" if col == 0 else "")

    plt.tight_layout()
    return fig, axes
