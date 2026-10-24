"""Figures of ``OSM_1_Preprocessing.ipynb``: complexity distributions and footprints before and after a step."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.ops import unary_union

from convexdecomp.plotting.polygons import fill_polygon_geometry, set_geometry_frame

__all__ = ["plot_complexity", "plot_footprint_changes", "plot_footprint_stages"]

_SET_COLORS = ("#e07a5f", "#3d5a80", "#81b29a", "#f2cc8f")


def plot_complexity(sets: Mapping[str, pd.DataFrame], columns: Mapping[str, str], *, title: str = "",
                    cap: int = 40):
    """One histogram per column (exterior vertices, reflex corners, ...), one step outline per set.

    `sets` maps a label to a table holding the `columns`; `columns` maps a column to its axis label.
    Values above `cap` are counted at `cap`. Counts are on a log scale.
    """
    fig, axes = plt.subplots(1, len(columns), figsize=(6.2 * len(columns), 4.2))
    axes = np.atleast_1d(axes)
    for ax, (column, label) in zip(axes, columns.items()):
        values = {name: np.minimum(frame[column].dropna().astype(int).to_numpy(), cap) for name, frame in sets.items()}
        low = min(int(v.min()) for v in values.values() if len(v))
        bins = np.arange(low - 0.5, cap + 1.5, 1.0)
        for color, (name, v) in zip(_SET_COLORS, values.items()):
            ax.hist(v, bins=bins, histtype="step", linewidth=1.6, color=color,
                    label=f"{name} (n={len(v)}, median {np.median(v):.0f})")
        ax.set_yscale("log")
        ax.set_xlabel(f"{label} (values above {cap} counted at {cap})")
        ax.set_ylabel("Footprints (log scale)")
        ax.legend(frameon=False, fontsize=8)
    if title:
        fig.suptitle(title, y=1.02)
    fig.tight_layout()
    plt.show()
    return fig


def plot_footprint_changes(gdf: pd.DataFrame, sample_ids: Sequence[str], before_col: str, after_col: str, *,
                           title_fn: Callable[[pd.Series], str] | None = None, ncols: int = 4, title: str = ""):
    """Each footprint before a step (filled) and after it (outline), one panel per footprint."""
    rows = gdf.set_index("sample_id").loc[list(sample_ids)]
    if rows.empty:
        print("No footprints to show.")
        return None
    nrows = int(np.ceil(len(rows) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.4 * ncols, 3.4 * nrows), squeeze=False)
    for ax in axes.ravel()[len(rows):]:
        ax.axis("off")
    for ax, (sample_id, row) in zip(axes.ravel(), rows.iterrows()):
        fill_polygon_geometry(ax, row[before_col], facecolor="#f4a261", edgecolor="#8d5524", linewidth=0.7, alpha=0.55)
        fill_polygon_geometry(ax, row[after_col], facecolor="none", edgecolor="#1f4e9c", linewidth=1.3)
        set_geometry_frame(ax, unary_union([row[before_col], row[after_col]]), min_pad=0.5, pad_ratio=0.06)
        ax.set_title(title_fn(row.rename(sample_id)) if title_fn else str(sample_id), fontsize=8)
    if title:
        fig.suptitle(title, y=1.0)
    fig.tight_layout()
    plt.show()
    return fig


def plot_footprint_stages(gdf: pd.DataFrame, sample_ids: Sequence[str], stages: Mapping[str, str], *,
                          title_fn: Callable[[pd.Series], str] | None = None):
    """One row per footprint, one panel per geometry column in `stages` (column -> panel title)."""
    rows = gdf.set_index("sample_id").loc[list(sample_ids)]
    fig, axes = plt.subplots(len(rows), len(stages), figsize=(3.3 * len(stages), 3.3 * len(rows)), squeeze=False)
    for axes_row, (sample_id, row) in zip(axes, rows.iterrows()):
        frame = unary_union([row[column] for column in stages])
        for ax, (column, stage_title) in zip(axes_row, stages.items()):
            fill_polygon_geometry(ax, row[column], facecolor="#c7dcef", edgecolor="#1f4e9c", linewidth=0.9)
            if column != next(iter(stages)):
                fill_polygon_geometry(ax, row[next(iter(stages))], facecolor="none", edgecolor="#e07a5f",
                                      linewidth=0.6)
            set_geometry_frame(ax, frame, min_pad=0.5, pad_ratio=0.06)
            ax.set_title(f"{stage_title}\n{int(len(row[column].exterior.coords) - 1) if row[column].geom_type == 'Polygon' else '-'} vertices",
                         fontsize=8)
        axes_row[0].text(-0.08, 0.5, title_fn(row.rename(sample_id)) if title_fn else str(sample_id),
                         transform=axes_row[0].transAxes, rotation=90, va="center", ha="right", fontsize=8)
    fig.tight_layout()
    plt.show()
    return fig
