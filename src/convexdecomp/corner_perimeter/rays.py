"""Corner rays: the corners and their bisectors, casting and cutting the rays, the wing ends, and the helpers of
rules 1 and 2."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, substring

from .geometry import EPS, Line, first_hit, points_of, reach, unit
from .settings import CornerRayConfig


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


def wing_end_corners(cs, cfg: CornerRayConfig) -> set[int]:
    """The corners (indices into cs) that cast no ray: the two convex corners of an end wall shorter than
    ``wing_end_max_m`` whose side walls are parallel within ``wing_end_parallel_deg`` (the two corner angles add up
    to 180° within it), and, with ``wing_end_long_sides``, at least as long as the end wall."""
    skip: set[int] = set()
    if cfg.wing_end_max_m <= 0:
        return skip
    for r in {c[3] for c in cs}:
        ks = [k for k, c in enumerate(cs) if c[3] == r]
        for j, k in enumerate(ks):
            h, m, n = ks[j - 1], ks[(j + 1) % len(ks)], ks[(j + 2) % len(ks)]
            end = np.linalg.norm(cs[m][0] - cs[k][0])
            if end >= cfg.wing_end_max_m or cs[k][2] >= 180.0 or cs[m][2] >= 180.0:
                continue
            if abs(cs[k][2] + cs[m][2] - 180.0) > cfg.wing_end_parallel_deg:  # side walls not parallel (curves)
                continue
            if cfg.wing_end_long_sides and min(np.linalg.norm(cs[k][0] - cs[h][0]),
                                               np.linalg.norm(cs[n][0] - cs[m][0])) < end:  # a step, not a wing
                continue
            skip |= {k, m}
    return skip


def close_pairs(poly: Polygon, cs, rays: list[LineString], cfg: CornerRayConfig):
    """Rule 2: opposite rays that each pass within ``opposite_gap_m`` of the other's corner, closest pairs first."""
    cands = []
    for i in range(len(rays)):
        for j in range(i + 1, len(rays)):
            if cs[i][1] @ cs[j][1] > -math.cos(math.radians(cfg.opposite_angle_deg)):
                continue
            gap = max(rays[i].distance(Point(cs[j][0])), rays[j].distance(Point(cs[i][0])))
            seg = LineString([cs[i][0], cs[j][0]])
            if gap < cfg.opposite_gap_m and poly.buffer(EPS).covers(seg):
                cands.append((gap, i, j, seg))
    used, out = set(), []
    for gap, i, j, seg in sorted(cands, key=lambda c: c[0]):
        if not {i, j} & used:
            used |= {i, j}
            out.append((i, j, seg, gap))
    return out


def reattach(lines: list[Line], poly: Polygon, band) -> None:
    """A line left ending on nothing (the line it stopped on was cut back) runs on to the first
    line it meets."""
    far = reach(poly)
    for ln in lines:
        if ln.kind == "merged":
            continue
        a, b = np.asarray(ln.geom.coords[0]), np.asarray(ln.geom.coords[-1])
        end = Point(b)
        if (end.distance(poly.boundary) < EPS or (not band.is_empty and end.distance(band) < EPS)
                or any(end.distance(o.geom) < EPS for o in lines if o is not ln)):
            continue
        obstacles = [(poly.boundary, "facade"), (band, "band")] + [(o.geom, "ray") for o in lines if o is not ln]
        s, ln.stop = first_hit(b, unit(b - a), obstacles, far)
        ln.geom = LineString([a, b + s * unit(b - a)])
