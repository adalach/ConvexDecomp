"""Small geometric helpers shared by the steps of the corner-ray perimeter."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import substring

from convexdecomp.core.scores import minimum_width

#: lengths and distances below this are zero
EPS = 1e-6
#: two zone corners closer than this are one point
SAME = 1e-7


@dataclass
class Line:
    """A line that cuts the footprint: a corner ray, a merged ray (rule 1) or a corner-to-corner line (rule 2)."""

    geom: LineString
    kind: str  # ray | merged_ray (rule 1) | corner_line (rule 2) | spine (a line of the narrow core, rule 4)
    stop: str  # what it ends on: ray | band | facade | corner
    reflex: bool = False
    again: bool = False  # a reflex corner's ray that reached nothing, cast again after rule 1 (rule 3)


def unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def cross(a: np.ndarray, b: np.ndarray) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


def reach(geom) -> float:
    """Twice the diagonal of geom's bounding box: longer than any line inside it."""
    minx, miny, maxx, maxy = geom.bounds
    return 2.0 * math.hypot(maxx - minx, maxy - miny)


def is_convex(f: Polygon) -> bool:
    return f.area >= (1.0 - 1e-6) * f.convex_hull.area


def polygons(g) -> list[Polygon]:
    return [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon" and not p.is_empty]


def one_polygon(g) -> Polygon | None:
    """g if it is one polygon (ignoring specks under 1e-6 m²), else None."""
    parts = [q for q in polygons(g) if q.area > 1e-6]
    return parts[0] if len(parts) == 1 else None


def points_of(geom) -> list[Point]:
    """Crossing points; a shared stretch (collinear lines) counts by its two ends."""
    if geom.is_empty:
        return []
    out = []
    for g in getattr(geom, "geoms", [geom]):
        if g.geom_type == "Point":
            out.append(g)
        elif g.geom_type == "LineString":
            out += [Point(g.coords[0]), Point(g.coords[-1])]
    return out


def outline_corners(poly: Polygon) -> np.ndarray:
    """The vertices of the footprint's outline and courtyards."""
    return np.vstack([np.asarray(r.coords)[:-1] for r in [poly.exterior, *poly.interiors]])


def kind_of(p: Polygon, k: str, min_width_m: float) -> str:
    """A zone's kind: core stays core; a perimeter zone is thin when narrower than min_width_m."""
    return k if k == "core" else "thin" if minimum_width(p) < min_width_m else "zone"


def shares_edge(a: Polygon, b: Polygon) -> bool:
    return shapely.intersection(a.boundary, b.boundary).length > 1e-6


def shared_length(boundary, edge, tol: float = 0.0) -> float:
    """How much of boundary runs along edge: exactly, or within tol of it (edges that agree only to rounding, or
    where one of them carries an extra point the other does not)."""
    if tol <= 0.0:
        return shapely.intersection(boundary, edge).length
    return shapely.intersection(boundary, edge.buffer(tol, cap_style="flat")).length


def zone_across(zones: list[tuple[Polygon, str]], i: int, edge: LineString,
                tol: float = 0.0) -> tuple[float, int | None]:
    """(shared length, index) of the zone other than zone i that shares most of the edge (within tol)."""
    return max(((shared_length(zones[j][0].boundary, edge, tol), j) for j in range(len(zones)) if j != i),
               default=(0.0, None))


def insert_point(z: Polygon, p: np.ndarray, tol: float = 1e-7) -> Polygon:
    """z with p inserted into the edges of its rings that p lies on (within tol) and does not end."""
    def node(c: np.ndarray) -> np.ndarray:
        out = []
        for a, b in zip(c[:-1], c[1:]):
            out.append(a)
            ab = b - a
            L2 = ab @ ab
            if L2 > 0:
                t = (p - a) @ ab / L2
                if 1e-9 < t < 1 - 1e-9 and np.linalg.norm(a + t * ab - p) < tol \
                        and min(np.linalg.norm(p - a), np.linalg.norm(p - b)) > 1e-9:
                    out.append(np.asarray(p, dtype=float))
        out.append(c[-1])
        return np.asarray(out)

    if z.distance(Point(p)) > tol:
        return z
    rings = [node(np.asarray(r.coords)) for r in [z.exterior, *z.interiors]]
    return Polygon(rings[0], rings[1:])


def clean_ring(c: np.ndarray) -> np.ndarray:
    """Drop repeated vertices and vertices the outline runs straight through."""
    k = 0
    while len(c) > 3 and k < len(c):
        a, p, b = c[k - 1], c[k], c[(k + 1) % len(c)]
        if np.linalg.norm(p - a) < 1e-6 or (abs(cross(unit(p - a), unit(b - p))) < 1e-3
                                            and (p - a) @ (b - p) > 0):
            c = np.delete(c, k, axis=0)
            k = 0
        else:
            k += 1
    return c


def convex_corners(z: Polygon) -> tuple[np.ndarray, list[tuple[float, int]]]:
    """The cleaned outer ring of z (counter-clockwise) and (angle in degrees, index) of its convex
    corners."""
    c = clean_ring(np.asarray(orient(z, 1.0).exterior.coords)[:-1])
    out = []
    for t in range(len(c)):
        x, a, y = c[t - 1], c[t], c[(t + 1) % len(c)]
        if cross(a - x, y - a) > 0:
            out.append((math.degrees(math.acos(np.clip(unit(x - a) @ unit(y - a), -1.0, 1.0))), t))
    return c, out


def line_x(p: np.ndarray, u: np.ndarray, a: np.ndarray, b: np.ndarray):
    """Where the line through p along u meets the line through a and b (None if parallel)."""
    d = b - a
    den = cross(u, d)
    if abs(den) < 1e-9:
        return None
    return p + cross(a - p, d) / den * u


def cut_at(ray: LineString, geom) -> tuple[LineString, bool]:
    """The ray up to where it first meets geom; whether it met it."""
    s = min((ray.project(q) for q in points_of(shapely.intersection(ray, geom)) if ray.project(q) > EPS),
            default=ray.length)
    return (substring(ray, 0.0, s), True) if s < ray.length - EPS else (ray, False)


def first_hit(start: np.ndarray, u: np.ndarray, obstacles, far: float) -> tuple[float, str]:
    """Distance from start along u to the first obstacle, and what it is."""
    seg = LineString([start, start + far * u])
    best = (far, "facade")
    for geom, kind in obstacles:
        for q in points_of(shapely.intersection(seg, geom)):
            s = seg.project(q)
            if EPS < s < best[0]:
                best = (s, kind)
    return best


def heading_of(ln: Line) -> np.ndarray:
    a, b = np.asarray(ln.geom.coords[0]), np.asarray(ln.geom.coords[-1])
    return unit(b - a)


def angle_at(a: np.ndarray, p: np.ndarray, b: np.ndarray) -> float:
    """The interior angle at p, in degrees, of a counter-clockwise ring running a, p, b."""
    u, v = a - p, b - p
    return math.degrees(math.atan2(cross(v, u), float(v @ u))) % 360.0


def angle_between(u: np.ndarray, v: np.ndarray) -> float:
    """The unsigned angle between two directions, 0 to 180 degrees."""
    return math.degrees(math.acos(np.clip(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)), -1.0, 1.0)))


def signed_area(c: np.ndarray) -> float:
    """The signed area of the ring c (positive counter-clockwise)."""
    x, y = c[:, 0], c[:, 1]
    return 0.5 * float(x @ np.roll(y, -1) - np.roll(x, -1) @ y)


def rings_of(z: Polygon) -> list[np.ndarray]:
    """The vertices of z's outer ring and of its holes, without the closing vertex."""
    return [np.asarray(z.exterior.coords)[:-1], *(np.asarray(h.coords)[:-1] for h in z.interiors)]


def sharp_corner_count(z: Polygon, deg: float) -> int:
    """How many corners of z's outer ring are sharper than deg."""
    return sum(1 for angle, _ in convex_corners(z)[1] if angle < deg)


def corner_points(poly: Polygon, zones: list[tuple[Polygon, str]]) -> tuple[np.ndarray, np.ndarray]:
    """The distinct zone corners (outline corners first, then every corner of a zone's cleaned outer ring) and
    whether each lies on the outline, where it may never move."""
    pts = list(outline_corners(poly))
    for z, _ in zones:
        for p in clean_ring(np.asarray(orient(z, 1.0).exterior.coords)[:-1]):
            if np.min(np.linalg.norm(np.asarray(pts) - p, axis=1)) > SAME:
                pts.append(p)
    pts = np.asarray(pts)
    on_outline = np.array([poly.boundary.distance(Point(p)) < EPS for p in pts])
    return pts, on_outline


def close_groups(pts: np.ndarray, fixed: np.ndarray, reach_m: float, span_m: float) -> list[list[int]]:
    """Groups of points (indices into pts) to become one: grown from the closest pairs closer than reach_m, each at
    most span_m across, without the fixed points. Groups of one point are left out."""
    d = np.linalg.norm(pts[:, None] - pts[None], axis=2)
    group = {i: [i] for i in range(len(pts))}  # the members of the group each point is in
    for i, j in sorted(zip(*np.nonzero(np.triu(d < reach_m, 1))), key=lambda ij: d[ij]):
        gi, gj = group[i], group[j]
        if gi is gj or fixed[i] or fixed[j]:
            continue
        both = gi + gj
        if d[np.ix_(both, both)].max() > span_m:
            continue
        for k in both:
            group[k] = both
    return list({id(g): g for g in group.values() if len(g) > 1}.values())
