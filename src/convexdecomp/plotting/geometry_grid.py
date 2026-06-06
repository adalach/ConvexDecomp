"""
Reusable grid plotting helper for GHSL notebooks (patched for per-source linewidth/edgecolor/zorder).

Features:
- Plot one or more geometry sources (e.g., main buildings and neighbors) in a grid by a shared id (e.g., sample_id).
- Each source can provide either a single geometry per row (geometry_col) or multiple sub-geometries/parts (parts_col).
- Flexible styling:
  * Solid colors for single-geometry sources; colormap for sources with parts.
  * NEW: per-source linewidth, optional per-source edgecolor, and zorder.
- Sensible defaults:
  * main buildings → blue (#1f77b4)
  * neighbors → light grey (#d3d3d3)
  * parts colormap → viridis for main, Greys for neighbors
- CRS-agnostic via GeoPandas .plot on the provided geometry column(s).

API
---
GHSL_gridplot(
    sources,
    ids=None,
    *,
    id_col="sample_id",
    ncols=5,
    nrows=None,
    figsize_scale=3.5,
    title_fn=None,
    edgecolor_main="black",
    edgecolor_neighbor="none",
    linewidth=0.5,
    background_color=None,
    tight_layout=True,
    share_aspect_equal=True,
):
Each source dict may include (besides required 'gdf'):
- role: "main" | "neighbor" | anything else (affects default colors and draw order)
- id_col: column name for ids (defaults to GHSL_gridplot id_col)
- geometry_col: column with geometries to plot (defaults to the active geometry)
- parts_col: iterable-of-geoms column to render as parts with a colormap
- color: solid color for single-geometry rendering
- cmap: colormap for parts rendering
- alpha: float transparency
- linewidth: float per-source linewidth (falls back to global linewidth)
- linestyle: str per-source linestyle (falls back to solid)
- edgecolor: str per-source edgecolor (falls back to edgecolor_main/edgecolor_neighbor)
- zorder: int/float per-source zorder within an axis
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Sequence

import numpy as np
import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib import colormaps as cmaps
try:
    # Shapely 2.x
    from shapely.geometry.base import BaseGeometry  # type: ignore
except Exception:
    # Fallback for older shapely
    from shapely.geometry import base as _shp_base  # type: ignore
    BaseGeometry = getattr(_shp_base, 'BaseGeometry', object)  # type: ignore


def _default_color_for(role: str) -> str:
    if role == "main":
        return "#1f77b4"  # matplotlib default blue
    return "#d3d3d3"      # light grey for neighbors


def _default_cmap_for(role: str):
    if role == "main":
        return cmaps.get("viridis")   # returns a Colormap object
    return cmaps.get("Greys")


def _normalize_cmap(cmap_like, role: str):
    """Accepts a Colormap or a string name; returns a Colormap.
       Falls back to the role default if the name is invalid."""
    if cmap_like is None:
        return _default_cmap_for(role)
    if isinstance(cmap_like, str):
        try:
            return cmaps.get(cmap_like)
        except Exception:
            return _default_cmap_for(role)
    # Assume it's already a Colormap/callable
    return cmap_like


def _iter_parts(geom_or_parts, parts_col_present: bool) -> Iterable[BaseGeometry]:
    if parts_col_present:
        parts = geom_or_parts
        if parts is None:
            return []
        try:
            return [g for g in parts if g is not None]
        except TypeError:
            # Not iterable, treat as single
            return [geom_or_parts]
    else:
        return [geom_or_parts]


def GHSL_gridplot(
    sources: Sequence[dict],
    ids: Optional[Sequence] = None,
    *,
    id_col: str = "sample_id",
    ncols: int = 5,
    nrows: Optional[int] = None,
    figsize_scale: float = 3.5,
    title_fn: Optional[Callable[[object, int], str]] = None,
    edgecolor_main: str = "black",
    edgecolor_neighbor: str = "none",
    linewidth: float = 0.5,         # global default linewidth
    background_color: Optional[str] = None,
    tight_layout: bool = True,
    share_aspect_equal: bool = True,
):
    if not sources:
        raise ValueError("sources must be a non-empty list of source dicts")

    # Normalize sources and detect columns
    norm_sources = []
    for s in sources:
        if "gdf" not in s:
            raise ValueError("Each source dict must contain a 'gdf' key (GeoDataFrame)")
        role = s.get("role", "neighbor")
        name = s.get("name", role)
        gdf: gpd.GeoDataFrame = s["gdf"]
        src_id_col = s.get("id_col", id_col)
        geometry_col = s.get(
            "geometry_col",
            getattr(gdf, "_geometry_column_name", None) or (gdf.geometry.name if hasattr(gdf, "geometry") else "geometry"),
        )
        parts_col = s.get("parts_col")
        color = s.get("color")
        cmap = s.get("cmap")
        alpha = s.get("alpha", 1.0)

        # NEW: per-source linewidth / edgecolor / zorder (fallbacks to global/defaults)
        src_linewidth = s.get("linewidth", linewidth)
        src_linestyle = s.get("linestyle", "solid")
        # if explicit edgecolor provided, use it; else use defaults by role
        src_edgecolor = s.get("edgecolor", (edgecolor_main if role == "main" else edgecolor_neighbor))
        src_zorder = s.get("zorder", None)

        norm_sources.append({
            "name": name,
            "gdf": gdf,
            "role": role,
            "id_col": src_id_col,
            "geometry_col": geometry_col,
            "parts_col": parts_col,
            "color": color,
            "cmap": cmap,
            "alpha": alpha,
            "linewidth": src_linewidth,     # NEW
            "linestyle": src_linestyle,     # NEW
            "edgecolor": src_edgecolor,     # NEW
            "zorder": src_zorder,           # NEW
        })

    # Build ID list
    if ids is None:
        anchor = norm_sources[0]
        if anchor["id_col"] not in anchor["gdf"].columns:
            raise KeyError(f"Anchor source '{anchor['name']}' is missing id column '{anchor['id_col']}'")
        ids = list(anchor["gdf"][anchor["id_col"]])
    ids = list(ids)

    # Grid layout
    ids = list(ids)
    ncols = max(1, int(ncols))
    if nrows is not None:
        nrows = max(1, int(nrows))
        max_plots = ncols * nrows
        ids = ids[:max_plots]
        n = len(ids)
    else:
        n = len(ids)
        nrows = int(np.ceil(n / ncols)) if n > 0 else 1
    fig, axes = plt.subplots(nrows, ncols, figsize=(figsize_scale*ncols, figsize_scale*nrows))
    axes = axes.flatten() if isinstance(axes, np.ndarray) else [axes]

    if background_color is not None:
        fig.patch.set_facecolor(background_color)

    # Draw order: neighbors first, then mains
    draw_order = [s for s in norm_sources if s["role"] != "main"] + [s for s in norm_sources if s["role"] == "main"]

    for ax_idx, ax in enumerate(axes[:n]):
        cur_id = ids[ax_idx]

        # Optionally set aspect
        if share_aspect_equal:
            ax.set_aspect("equal")

        # For each source
        for src in draw_order:
            gdf = src["gdf"]
            if src["id_col"] not in gdf.columns:
                continue
            mask = gdf[src["id_col"]] == cur_id
            sub = gdf[mask]
            if sub.empty:
                continue

            role = src["role"]
            parts_col = src["parts_col"]
            has_parts = parts_col is not None and (parts_col in sub.columns)

            # Determine styling for this source
            if has_parts:
                cmap = _normalize_cmap(src.get("cmap"), role)
                color = None  # ignored for parts rendering
            else:
                color = src.get("color", None)
                if color is None:
                    color = _default_color_for(role)
                cmap = None  # not used

            # Common style
            lw = float(src["linewidth"])
            ls = src["linestyle"]
            ec = src["edgecolor"]
            z = src.get("zorder", None)

            # Plot each row
            if has_parts:
                # Multiple parts per row (colored by cmap)
                parts_series = sub[parts_col]
                total_parts = sum(len(_iter_parts(parts, True)) for parts in parts_series)
                denom = max(1, total_parts - 1)
                i_part = 0
                for _, row in sub.iterrows():
                    parts = _iter_parts(row[parts_col], True)
                    for part in parts:
                        rgba = cmap(i_part / denom) if hasattr(cmap, "__call__") else cmap
                        gpd.GeoSeries([part], crs=sub.crs).plot(
                            ax=ax,
                            color=rgba,
                            edgecolor=ec,
                            linewidth=lw,
                            linestyle=ls,
                            alpha=src["alpha"],
                            zorder=z,
                        )
                        i_part += 1
            else:
                # Single geometry per row, use the specified geometry column
                geom_col = src["geometry_col"]
                geoms = sub[geom_col]
                # Plot entire GeoSeries at once (handles MultiPolygons)
                gpd.GeoSeries(geoms.values, crs=sub.crs).plot(
                    ax=ax,
                    color=color,
                    edgecolor=ec,
                    linewidth=lw,
                    linestyle=ls,
                    alpha=src["alpha"],
                    zorder=z,
                )

        # Title
        title = title_fn(cur_id, ax_idx) if callable(title_fn) else str(cur_id)
        ax.set_title(title, fontsize=8, pad=10)
        ax.axis("off")

    # Hide unused axes
    for ax in axes[n:]:
        ax.axis("off")

    if tight_layout:
        fig.tight_layout(pad=1.0, h_pad=2.0, w_pad=1.0)
        fig.subplots_adjust(top=0.90)

    return fig, axes
