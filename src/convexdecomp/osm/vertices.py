"""Duplicate and collinear vertices: dropping the vertices that do not shape a footprint.

`remove_redundant_vertices` works ring by ring. It first merges consecutive vertices closer than
``duplicate_tolerance_m``, then removes, one at a time and cheapest first, every vertex whose
removal keeps all the vertices removed so far within ``collinear_tolerance_m`` of the new outline.
The outline therefore moves by at most ``collinear_tolerance_m`` (plus the duplicate tolerance),
however many vertices along a wall are removed, and a corner that turns by more than that stays.
Vertices are measured against segments, not infinite lines, so the tip of a spike is never mistaken
for a point on a wall.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import Polygon

__all__ = ["VertexCleanup", "clean_ring", "remove_redundant_vertices"]


@dataclass(frozen=True)
class VertexCleanup:
    """How close two vertices are to count as one, and how far a vertex may lie off a wall to be dropped."""

    duplicate_tolerance_m: float = 0.01
    collinear_tolerance_m: float = 0.05


def _segment_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """The largest distance of `points` (n x 2) from the segment a-b."""
    ab = b - a
    length2 = float(ab @ ab)
    if length2 <= 1e-24:
        return float(np.max(np.hypot(*(points - a).T)))
    t = np.clip(((points - a) @ ab) / length2, 0.0, 1.0)
    nearest = a + t[:, None] * ab
    return float(np.max(np.hypot(*(points - nearest).T)))


def _merge_duplicates(points: list, tolerance: float) -> list:
    """Consecutive vertices (the ring closed on itself) closer than `tolerance` become their first."""
    kept: list = []
    for point in points:
        if kept and math.dist(kept[-1], point) <= tolerance:
            continue
        kept.append(point)
    while len(kept) > 1 and math.dist(kept[0], kept[-1]) <= tolerance:
        kept.pop()
    return kept


def clean_ring(coords, cleanup: VertexCleanup = VertexCleanup()) -> list:
    """One ring without duplicate and collinear vertices, as an open coordinate list (fewer than 3 means none left)."""
    points = [tuple(map(float, p)) for p in list(coords)[:-1]] if len(coords) and tuple(coords[0]) == tuple(coords[-1]) \
        else [tuple(map(float, p)) for p in coords]
    points = _merge_duplicates(points, cleanup.duplicate_tolerance_m)
    if len(points) <= 3:
        return points
    xy = np.asarray(points)
    n = len(xy)
    alive = np.ones(n, dtype=bool)
    # For each kept vertex: the original vertices dropped between it and the next kept one.
    dropped_after: list[list[int]] = [[] for _ in range(n)]

    def neighbours(k: int) -> tuple[int, int]:
        prev, nxt = (k - 1) % n, (k + 1) % n
        while not alive[prev]:
            prev = (prev - 1) % n
        while not alive[nxt]:
            nxt = (nxt + 1) % n
        return prev, nxt

    def cost(k: int) -> float:
        prev, nxt = neighbours(k)
        members = [k, *dropped_after[prev], *dropped_after[k]]
        return _segment_distance(xy[members], xy[prev], xy[nxt])

    costs = np.array([cost(k) for k in range(n)])
    while alive.sum() > 3:
        candidates = np.where(alive, costs, np.inf)
        k = int(np.argmin(candidates))
        if candidates[k] > cleanup.collinear_tolerance_m:
            break
        prev, nxt = neighbours(k)
        dropped_after[prev] = [*dropped_after[prev], k, *dropped_after[k]]
        dropped_after[k] = []
        alive[k] = False
        costs[k] = np.inf
        for j in (prev, nxt):
            costs[j] = cost(j)
    return [points[k] for k in range(n) if alive[k]]


def _clean_polygon(poly: Polygon, cleanup: VertexCleanup) -> Polygon | None:
    exterior = clean_ring(poly.exterior.coords, cleanup)
    if len(exterior) < 3:
        return None
    holes = [ring for ring in (clean_ring(r.coords, cleanup) for r in poly.interiors) if len(ring) >= 3]
    return Polygon(exterior, holes)


def remove_redundant_vertices(geom, cleanup: VertexCleanup = VertexCleanup()) -> tuple[object, int]:
    """A footprint without duplicate and collinear vertices, and how many vertices were removed.

    Polygons and MultiPolygons are cleaned part by part. A result that is not valid or loses a
    ring keeps the footprint as it was (0 removed).
    """
    if geom is None or geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return geom, 0
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    cleaned = [_clean_polygon(part, cleanup) for part in parts]
    if any(part is None for part in cleaned):
        return geom, 0
    result = cleaned[0] if len(cleaned) == 1 else shapely.MultiPolygon(cleaned)
    rings_before = sum(1 + len(part.interiors) for part in parts)
    rings_after = sum(1 + len(part.interiors) for part in cleaned)
    if not result.is_valid or rings_after != rings_before:
        return geom, 0
    removed = int(shapely.get_num_coordinates(geom) - shapely.get_num_coordinates(result))
    return result, removed
