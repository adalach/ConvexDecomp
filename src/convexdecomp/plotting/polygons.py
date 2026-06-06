from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

__all__ = [
    "configure_dense_integer_log_axis",
    "fill_polygon_geometry",
    "iter_polygon_parts",
    "plot_matched_split_violin_pair",
    "set_geometry_frame",
    "split_violin",
]


def iter_polygon_parts(geom: BaseGeometry | None) -> list[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if getattr(geom, "geom_type", None) == "Polygon":
        return [geom]
    return [
        part
        for part in getattr(geom, "geoms", [])
        if isinstance(part, Polygon) and not getattr(part, "is_empty", True)
    ]


def fill_polygon_geometry(
    ax: Any,
    geom: BaseGeometry | None,
    *,
    facecolor: Any = "none",
    edgecolor: Any = "black",
    linewidth: float = 1.0,
    alpha: float = 1.0,
    hole_facecolor: Any = "white",
) -> None:
    for polygon in iter_polygon_parts(geom):
        x, y = polygon.exterior.xy
        ax.fill(x, y, facecolor=facecolor, edgecolor=edgecolor, linewidth=linewidth, alpha=alpha)
        for interior in polygon.interiors:
            hx, hy = interior.xy
            ax.fill(
                hx,
                hy,
                facecolor=hole_facecolor,
                edgecolor=edgecolor,
                linewidth=max(0.4, linewidth * 0.8),
                alpha=1.0,
            )


def set_geometry_frame(
    ax: Any,
    geom: BaseGeometry,
    *,
    min_pad: float,
    pad_ratio: float,
    hide_ticks: bool = True,
) -> None:
    minx, miny, maxx, maxy = geom.bounds
    padx = max(min_pad, pad_ratio * max(maxx - minx, 1.0))
    pady = max(min_pad, pad_ratio * max(maxy - miny, 1.0))
    ax.set_xlim(minx - padx, maxx + padx)
    ax.set_ylim(miny - pady, maxy + pady)
    ax.set_aspect("equal")
    if hide_ticks:
        ax.set_xticks([])
        ax.set_yticks([])
        ax.axis("off")


def split_violin(
    ax: Any,
    left_values,
    right_values,
    *,
    position: float = 1.0,
    width: float = 0.9,
    left_color: str = "#e07a5f",
    right_color: str = "#2a9d8f",
    title: str = "",
    ylabel: str = "",
) -> None:
    import numpy as np

    datasets = [np.asarray(left_values, dtype=float), np.asarray(right_values, dtype=float)]
    datasets = [vals[np.isfinite(vals)] for vals in datasets]
    violin_parts = ax.violinplot(
        datasets,
        positions=[position, position],
        widths=width,
        showmeans=False,
        showmedians=False,
        showextrema=False,
    )

    center = float(position)
    for body, side, color in zip(violin_parts["bodies"], ["left", "right"], [left_color, right_color]):
        body.set_facecolor(color)
        body.set_edgecolor("black")
        body.set_alpha(0.50)
        path = body.get_paths()[0]
        verts = path.vertices
        if side == "left":
            verts[:, 0] = np.minimum(verts[:, 0], center)
        else:
            verts[:, 0] = np.maximum(verts[:, 0], center)

    left_median = float(np.median(datasets[0])) if len(datasets[0]) else np.nan
    right_median = float(np.median(datasets[1])) if len(datasets[1]) else np.nan
    if np.isfinite(left_median):
        ax.hlines(left_median, center - width * 0.45, center, colors="black", linewidth=1.5)
    if np.isfinite(right_median):
        ax.hlines(right_median, center, center + width * 0.45, colors="black", linewidth=1.5)

    ax.set_xlim(center - 0.75, center + 0.75)
    ax.set_xticks([center - 0.22, center + 0.22])
    ax.set_xticklabels(["Original", "Final"])
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", alpha=0.25)


def configure_dense_integer_log_axis(ax: Any) -> None:
    import numpy as np
    from matplotlib.ticker import FuncFormatter, LogLocator

    # Label all integer-like log ticks that Matplotlib places in the visible range.
    def _integer_log_formatter(value, _pos):
        if value <= 0 or not np.isfinite(value):
            return ""
        rounded = int(round(value))
        if abs(value - rounded) <= 1e-9:
            return str(rounded)
        return ""

    ax.set_yscale("log")
    ax.yaxis.set_major_locator(LogLocator(base=10.0, subs=np.arange(1.0, 10.0)))
    ax.yaxis.set_major_formatter(FuncFormatter(_integer_log_formatter))
    ax.grid(axis="y", which="major", alpha=0.25)


def plot_matched_split_violin_pair(
    *,
    plt: Any,
    original_vertex_values,
    final_vertex_values,
    original_direction_values,
    final_direction_values,
    title_suffix: str,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11, 6.2))

    # Both halves already use matched ids; this helper only handles the visual comparison.
    split_violin(
        axes[0],
        original_vertex_values,
        final_vertex_values,
        left_color="#e07a5f",
        right_color="#2a9d8f",
        title="Exterior vertices",
        ylabel="Count per footprint",
    )
    configure_dense_integer_log_axis(axes[0])

    split_violin(
        axes[1],
        original_direction_values,
        final_direction_values,
        left_color="#4c78a8",
        right_color="#457b9d",
        title="Distinct exterior directions",
        ylabel="Count per footprint",
    )
    configure_dense_integer_log_axis(axes[1])

    fig.suptitle(title_suffix, y=1.02)
    plt.tight_layout()
    plt.show()
