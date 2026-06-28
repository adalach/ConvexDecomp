from __future__ import annotations

from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

__all__ = [
    "fill_polygon_geometry",
    "iter_polygon_parts",
    "set_geometry_frame",
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
