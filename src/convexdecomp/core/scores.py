"""A configurable score for a partition into zones (lower is better).

The default reproduces ``score_polygon_partition``: the number of zones plus a
weight times one minus the area-weighted mean Polsby-Popper compactness. The
other terms are off by default:

- ``area_weighting`` is the exponent of each zone's weight in the means: 1
  weights a zone by its area, as before, so a small zone hardly counts; 0 makes
  every zone count the same.
- rectangularity is a zone's area over the area of its smallest enclosing
  rectangle. It adds to compactness rather than replacing it: a long rectangle
  is perfectly rectangular but not compact, and keeps the compactness penalty.
- a zone narrower than ``narrow_width_m`` costs in proportion to how much
  narrower it is; only convex pieces count, since a non-convex piece of an
  unfinished state is cut further.
- a zone smaller than ``small_area_m2`` costs in proportion to how much smaller
  it is.

``ZoneMetrics`` holds the per-zone numbers the score is computed from, so a
caller can recompute the score for other weights without the geometry.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Dict, NamedTuple, Sequence

from shapely.geometry import Polygon

from convexdecomp.core.convexity import is_convex_polygon

__all__ = ["PartitionScore", "ZoneMetrics", "minimum_width", "zone_metrics"]


class ZoneMetrics(NamedTuple):
    area: float
    compactness: float  # Polsby-Popper, 4 pi A / P^2; 1 for a circle, 0.785 for a square
    rectangularity: float  # area / area of the smallest enclosing rectangle
    width: float  # smallest width over all directions, of the convex hull
    convex: bool


def minimum_width(poly: Polygon) -> float:
    """The smallest width of the polygon's convex hull over all directions.

    For a convex polygon the narrowest direction is perpendicular to one of its
    edges, so it is the smallest, over the edges, of the farthest distance of a
    vertex from the edge's line.
    """
    hull = poly.convex_hull
    if not isinstance(hull, Polygon) or hull.is_empty:
        return 0.0
    pts = list(hull.exterior.coords)[:-1]
    best = math.inf
    for i in range(len(pts)):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % len(pts)]
        length = math.hypot(bx - ax, by - ay)
        if length == 0.0:
            continue
        far = max(abs((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / length for px, py in pts)
        best = min(best, far)
    return 0.0 if best is math.inf else best


_METRICS_CACHE: Dict[bytes, ZoneMetrics] = {}
_METRICS_CACHE_SIZE = 200_000


def zone_metrics(poly: Polygon) -> ZoneMetrics:
    """All metrics of one zone, cached by its geometry (the search scores the same piece many times)."""
    key = poly.wkb
    cached = _METRICS_CACHE.get(key)
    if cached is not None:
        return cached
    area = float(poly.area)
    perimeter = float(poly.length)
    compactness = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0.0 else 0.0
    rect = poly.minimum_rotated_rectangle
    rect_area = float(rect.area) if isinstance(rect, Polygon) else 0.0
    rectangularity = area / rect_area if rect_area > 0.0 else 0.0
    metrics = ZoneMetrics(area, compactness, rectangularity, minimum_width(poly), bool(is_convex_polygon(poly)))
    if len(_METRICS_CACHE) >= _METRICS_CACHE_SIZE:
        _METRICS_CACHE.clear()
    _METRICS_CACHE[key] = metrics
    return metrics


@dataclass(frozen=True)
class PartitionScore:
    """``score(parts)``: lower is better. The defaults give the engine's original score."""

    weight_parts: float = 1.0
    weight_compactness: float = 20.0
    area_weighting: float = 1.0
    weight_rectangularity: float = 0.0
    weight_narrow: float = 0.0
    narrow_width_m: float = 2.0
    weight_small: float = 0.0
    small_area_m2: float = 10.0

    def terms_from_metrics(self, metrics: Sequence[ZoneMetrics]) -> Dict[str, float]:
        """Each term's contribution to the score, from the zones' metrics."""
        if not metrics:
            return {"parts": 0.0, "compactness": 0.0, "rectangularity": 0.0, "narrow": 0.0, "small": 0.0}
        weights = [m.area ** self.area_weighting if m.area > 0.0 else 0.0 for m in metrics]
        total = sum(weights) or 1.0
        mean_c = sum(w * m.compactness for w, m in zip(weights, metrics)) / total
        mean_r = sum(w * m.rectangularity for w, m in zip(weights, metrics)) / total
        narrow = 0.0
        if self.weight_narrow and self.narrow_width_m > 0.0:
            narrow = sum(max(0.0, self.narrow_width_m - m.width) / self.narrow_width_m
                         for m in metrics if m.convex)
        small = 0.0
        if self.weight_small and self.small_area_m2 > 0.0:
            small = sum(max(0.0, self.small_area_m2 - m.area) / self.small_area_m2 for m in metrics)
        return {
            "parts": self.weight_parts * len(metrics),
            "compactness": self.weight_compactness * (1.0 - mean_c),
            "rectangularity": self.weight_rectangularity * (1.0 - mean_r) if self.weight_rectangularity else 0.0,
            "narrow": self.weight_narrow * narrow,
            "small": self.weight_small * small,
        }

    def from_metrics(self, metrics: Sequence[ZoneMetrics]) -> float:
        return float(sum(self.terms_from_metrics(metrics).values()))

    def terms(self, parts: Sequence[Polygon]) -> Dict[str, float]:
        return self.terms_from_metrics([zone_metrics(p) for p in parts])

    def __call__(self, parts: Sequence[Polygon]) -> float:
        if not (self.weight_rectangularity or self.weight_narrow):
            # Only area and perimeter are needed: skip the enclosing rectangle and the width.
            metrics = []
            for p in parts:
                area, perimeter = float(p.area), float(p.length)
                c = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter > 0.0 else 0.0
                metrics.append(ZoneMetrics(area, c, 0.0, 0.0, True))
            return self.from_metrics(metrics)
        return self.from_metrics([zone_metrics(p) for p in parts])

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)
