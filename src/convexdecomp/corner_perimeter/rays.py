"""The band and the corner rays: the corners and their bisectors, the band offset from the footprint, and the rays
cast from the corners, cut off at the band line and where they reach another ray."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, substring

from .geometry import points_of, reach
from .settings import CornerRayConfig


def offset_band(poly: Polygon, caps: list[Polygon], cfg: CornerRayConfig):
    """The band: the footprint (without the bay caps) offset inwards by ``band_m`` with mitred corners. Returns the
    core as offset and its boundary, the band line (empty when the footprint is too narrow for a core)."""
    base = poly.difference(shapely.union_all(caps)) if caps else poly
    offset_core = base.buffer(-cfg.band_m, join_style="mitre", mitre_limit=10.0)
    return offset_core, offset_core.boundary if not offset_core.is_empty else LineString()


def corners(poly: Polygon, min_turn_deg: float = 1.0):
    """(point, inward bisector, interior angle in degrees, ring index) for every corner of the outline and the
    courtyards; a vertex turning less than min_turn_deg is not a corner."""
    poly = orient(poly, sign=1.0)  # exterior CCW, holes CW: the inside is left of every edge
    out = []
    for r, ring in enumerate([poly.exterior, *poly.interiors]):
        c = np.asarray(ring.coords)[:-1]
        n = len(c)
        for i in range(n):
            a, p, b = c[i - 1], c[i], c[(i + 1) % n]
            d1 = (p - a) / np.linalg.norm(p - a)
            d2 = (b - p) / np.linalg.norm(b - p)
            turn = math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1 @ d2))  # > 0: convex
            if abs(turn) < min_turn_deg:
                continue
            n1 = np.array([-d1[1], d1[0]])  # inward normals of the two walls
            n2 = np.array([-d2[1], d2[0]])
            bis = (n1 + n2) / np.linalg.norm(n1 + n2)  # equidistant from both walls
            out.append((p, bis, 180.0 - turn, r))
    return out


def cast(poly: Polygon, p: np.ndarray, d: np.ndarray) -> LineString:
    """The ray from corner p along d, up to where it leaves the footprint."""
    inside = shapely.intersection(poly, LineString([p, p + reach(poly) * d]))
    parts = list(getattr(inside, "geoms", [inside]))
    lines = linemerge([g for g in parts if g.geom_type == "LineString"])
    start = Point(p)
    for g in getattr(lines, "geoms", [lines]):
        if g.distance(start) < 1e-6:
            return g if Point(g.coords[0]).distance(start) < 1e-6 else LineString(g.coords[::-1])
    raise ValueError("ray does not start inside the footprint")


def trim(cs, rays: list[LineString], tie_m: float = 0.10) -> tuple[list[LineString], list[bool]]:
    """Cut every ray where it reaches another ray's trace (all rays grow at the same speed; two arriving within
    tie_m of each other both stop); returns the rays and which were cut."""
    n = len(rays)
    p = [np.asarray(r.coords[0]) for r in rays]
    length = [r.length for r in rays]
    events = []  # (time, i, j, arrival of i, arrival of j)
    for i in range(n):
        for j in range(i + 1, n):
            x = shapely.intersection(rays[i], rays[j])
            for g in [] if x.is_empty else getattr(x, "geoms", [x]):
                if g.geom_type == "Point":
                    q = np.asarray(g.coords[0])
                    si, sj = np.linalg.norm(q - p[i]), np.linalg.norm(q - p[j])
                elif cs[i][1] @ cs[j][1] < 0:  # collinear, head-on: they meet half way
                    si = sj = np.linalg.norm(p[i] - p[j]) / 2
                elif rays[j].distance(Point(p[i])) < 1e-6:  # collinear, j runs over corner i
                    si, sj = 0.0, np.linalg.norm(p[i] - p[j])
                else:  # collinear, i runs over corner j
                    si, sj = np.linalg.norm(p[i] - p[j]), 0.0
                events.append((max(si, sj), i, j, si, sj))
    stop = list(length)
    cut = [False] * n
    for _, i, j, si, sj in sorted(events):
        if stop[i] < si - 1e-9 or stop[j] < sj - 1e-9:
            continue  # one of them stopped before the crossing
        tie = abs(si - sj) <= tie_m
        for k, s in ((i, si), (j, sj)):
            later = s >= max(si, sj)  # the ray arriving second hits the other's trace
            if (tie or later) and s < length[k] - 1e-6 and s < stop[k]:
                stop[k], cut[k] = s, True
    return [substring(r, 0.0, s) for r, s in zip(rays, stop)], cut


def to_band(rays: list[LineString], band_line) -> tuple[list[LineString], list[bool]]:
    """Every ray up to where it first meets the band line; which of them meet it."""
    out, hit = [], []
    for r in rays:
        s = min((r.project(q) for q in points_of(shapely.intersection(r, band_line))), default=r.length)
        hit.append(s < r.length - 1e-6)
        out.append(substring(r, 0.0, s) if hit[-1] else r)
    return out, hit
