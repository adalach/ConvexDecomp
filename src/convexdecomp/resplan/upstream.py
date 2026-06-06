from __future__ import annotations

from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from shapely.geometry import MultiPolygon, Polygon

__all__ = [
    "CATEGORY_COLORS",
    "get_category_colors",
    "plot_plan",
]


CATEGORY_COLORS: dict[str, str] = {
    "living": "#d9d9d9",
    "bedroom": "#66c2a5",
    "bathroom": "#fc8d62",
    "kitchen": "#8da0cb",
    "door": "#e78ac3",
    "window": "#a6d854",
    "wall": "#ffd92f",
    "front_door": "#a63603",
    "balcony": "#b3b3b3",
    "storage": "#db822b",
    "stair": "#c49a6c",
}

DEFAULT_PLAN_CATEGORIES: list[str] = [
    "living",
    "bedroom",
    "bathroom",
    "kitchen",
    "storage",
    "stair",
    "door",
    "window",
    "wall",
    "front_door",
    "balcony",
]


def _flatten_polygon_parts(value: Any) -> list[Polygon]:
    if isinstance(value, Polygon):
        return [] if value.is_empty else [value]
    if isinstance(value, MultiPolygon):
        return [poly for poly in value.geoms if isinstance(poly, Polygon) and not poly.is_empty]
    if isinstance(value, (list, tuple)):
        out: list[Polygon] = []
        for item in value:
            out.extend(_flatten_polygon_parts(item))
        return out
    geoms = getattr(value, "geoms", None)
    if geoms is not None:
        out: list[Polygon] = []
        for item in geoms:
            out.extend(_flatten_polygon_parts(item))
        return out
    return []


def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(plan)
    if "balacony" in normalized and "balcony" not in normalized:
        normalized["balcony"] = normalized.pop("balacony")
    return normalized


def get_category_colors() -> dict[str, str]:
    return dict(CATEGORY_COLORS)


def plot_plan(
    plan: dict[str, Any],
    categories: list[str] | None = None,
    colors: dict[str, str] | None = None,
    ax: plt.Axes | None = None,
    legend: bool = True,
    title: str | None = None,
    tight: bool = True,
) -> plt.Axes:
    normalized = _normalize_plan(plan)
    categories = DEFAULT_PLAN_CATEGORIES if categories is None else categories
    colors = CATEGORY_COLORS if colors is None else colors

    geoms: list[Polygon] = []
    color_list: list[str] = []
    present: list[str] = []
    for key in categories:
        parts = _flatten_polygon_parts(normalized.get(key))
        if not parts:
            continue
        geoms.extend(parts)
        color_list.extend([colors.get(key, "#000000")] * len(parts))
        present.append(key)

    if not geoms:
        raise ValueError("No geometries to plot.")

    gseries = gpd.GeoSeries(geoms)
    if ax is None:
        _, ax = plt.subplots(figsize=(8, 8))
    gseries.plot(ax=ax, color=color_list, edgecolor="black", linewidth=0.5)
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()

    if title:
        ax.set_title(title)

    if legend:
        uniq_present = list(dict.fromkeys(present))
        handles = [
            Patch(facecolor=colors.get(key, "#000000"), edgecolor="black", label=key.replace("_", " "))
            for key in uniq_present
        ]
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1, 1), frameon=False)

    if tight:
        plt.tight_layout()
    return ax

