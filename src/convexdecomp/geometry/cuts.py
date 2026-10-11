"""Exact two-piece cuts of a polygon without holes.

The convex decomposition refines a polygon by cutting it at a reflex vertex.
Cutting with ``shapely.ops.split`` along a long line through the vertex fails in
two ways. The line can cross the polygon several times and return more than two
pieces, and GEOS occasionally returns the pieces without one of the faces. In
both cases floor area disappears if the pieces are taken as they come.

The cuts here use no overlay at all. A cut is a chord: from the vertex along a
ray into the polygon up to the first point of the boundary the ray reaches, or
straight to another vertex for a diagonal. The polygon's own vertex list is then
split in two at the chord's ends. Both pieces share the chord exactly, so their
areas add up to the polygon's by construction, and a cut always gives exactly
two pieces or none.

Only the exterior ring is cut. A polygon with holes has to be split into
hole-less pieces first (``convexdecomp.geometry.holes.holeless_polygons``);
these functions refuse to cut one.
"""

from __future__ import annotations

import math
from typing import List, NamedTuple, Optional, Sequence, Tuple

import shapely
from shapely.geometry import Polygon

__all__ = [
    "BoundaryHit",
    "clean_ring",
    "cut_along_diagonal",
    "cut_along_ray",
    "first_boundary_hit",
    "inward_bisector",
    "points_inward",
    "split_ring",
]

Point2 = Tuple[float, float]

#: Relative tolerance for "the same point" and "on the edge", scaled by the polygon's size.
REL_EPS = 1e-9
#: Pieces whose areas differ from the polygon's by more than this fraction are refused.
AREA_REL_TOL = 1e-9


class BoundaryHit(NamedTuple):
    """Where a ray first meets the boundary.

    ``kind`` is ``"vertex"`` (``index`` is the vertex) or ``"edge"`` (``index``
    is the edge from vertex ``index`` to ``index + 1`` and ``point`` lies
    strictly inside it). ``t`` is the distance from the ray's origin.
    """

    kind: str
    index: int
    t: float
    point: Point2


def _ring_coords(poly: Polygon) -> List[Point2]:
    return [(float(x), float(y)) for x, y in list(poly.exterior.coords)[:-1]]


def _size(coords: Sequence[Point2]) -> float:
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    return max(max(xs) - min(xs), max(ys) - min(ys), 1e-12)


def first_boundary_hit(
    coords: Sequence[Point2],
    origin: int,
    direction: Point2,
    *,
    eps: Optional[float] = None,
) -> Optional[BoundaryHit]:
    """The first boundary point the ray from vertex ``origin`` reaches, or None.

    The vertex's own two edges are skipped. An edge lying along the ray counts
    as reached at its nearer endpoint, and a hit within ``eps`` of an edge's
    endpoint is reported as that vertex, so a chord never ends a hair's breadth
    from a corner.
    """
    n = len(coords)
    eps = REL_EPS * _size(coords) if eps is None else eps
    dx, dy = direction
    vx, vy = coords[origin]
    best: Optional[BoundaryHit] = None
    for j in range(n):
        k = (j + 1) % n
        if j == origin or k == origin:
            continue
        ax, ay = coords[j]
        bx, by = coords[k]
        ex, ey = bx - ax, by - ay
        edge_len = math.hypot(ex, ey)
        if edge_len <= eps:
            continue
        wx, wy = ax - vx, ay - vy
        den = dx * ey - dy * ex
        if abs(den) <= REL_EPS * edge_len:
            # Parallel. Along the ray, the edge is first touched at its nearer endpoint.
            if abs(wx * dy - wy * dx) <= eps:
                for idx, (px, py) in ((j, (ax, ay)), (k, (bx, by))):
                    t = (px - vx) * dx + (py - vy) * dy
                    if t > eps and (best is None or t < best.t - eps):
                        best = BoundaryHit("vertex", idx, t, (px, py))
            continue
        t = (wx * ey - wy * ex) / den
        s = (wx * dy - wy * dx) / den
        if t <= eps or s * edge_len < -eps or (s - 1.0) * edge_len > eps:
            continue
        if best is not None and t >= best.t - eps:
            continue
        if s * edge_len <= eps:
            best = BoundaryHit("vertex", j, t, (ax, ay))
        elif (1.0 - s) * edge_len <= eps:
            best = BoundaryHit("vertex", k, t, (bx, by))
        else:
            best = BoundaryHit("edge", j, t, (vx + t * dx, vy + t * dy))
    return best


def _walk(coords: Sequence[Point2], start: int, stop: int) -> List[Point2]:
    """Vertices from ``start`` to ``stop`` inclusive, going forward around the ring."""
    n = len(coords)
    out = [coords[start]]
    idx = start
    while idx != stop:
        idx = (idx + 1) % n
        out.append(coords[idx])
    return out


def split_ring(
    coords: Sequence[Point2], origin: int, hit: BoundaryHit
) -> Optional[Tuple[List[Point2], List[Point2]]]:
    """The two vertex lists either side of the chord from vertex ``origin`` to ``hit``.

    None when the chord would run along one of the vertex's own edges.
    """
    n = len(coords)
    if hit.kind == "vertex":
        k = hit.index
        if k in (origin, (origin - 1) % n, (origin + 1) % n):
            return None
        return _walk(coords, origin, k), _walk(coords, k, origin)
    j = hit.index
    return _walk(coords, origin, j) + [hit.point], [hit.point] + _walk(coords, (j + 1) % n, origin)


def clean_ring(ring: Sequence[Point2], *, eps: float) -> List[Point2]:
    """Drop repeated points and vertices where the ring runs straight on.

    "Straight" is judged by the angle, not by the raw cross product, so a
    vertex next to a very short edge is not mistaken for a straight one. One
    vertex is removed at a time, so the two ends of a short edge can never both
    go in one pass and take a corner with them.
    """
    pts = list(ring)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            ab = (b[0] - a[0], b[1] - a[1])
            bc = (c[0] - b[0], c[1] - b[1])
            lab, lbc = math.hypot(*ab), math.hypot(*bc)
            if lab <= eps:
                del pts[i]
                changed = True
                break
            cross = ab[0] * bc[1] - ab[1] * bc[0]
            dot = ab[0] * bc[0] + ab[1] * bc[1]
            if lbc > eps and dot > 0 and abs(cross) <= REL_EPS * lab * lbc:
                del pts[i]
                changed = True
                break
    return pts


def _pieces(
    poly: Polygon,
    rings: Tuple[List[Point2], List[Point2]],
    *,
    eps: float,
    min_part_area: float,
) -> Optional[List[Polygon]]:
    parts = []
    for ring in rings:
        cleaned = clean_ring(ring, eps=eps)
        if len(cleaned) < 3:
            return None
        part = Polygon(cleaned)
        if part.is_empty or not part.is_valid or part.area <= 0.0:
            return None
        parts.append(part)
    if any(part.area < min_part_area for part in parts):
        return None
    if abs(sum(part.area for part in parts) - poly.area) > AREA_REL_TOL * poly.area:
        return None
    return parts


def points_inward(poly: Polygon, origin: int, direction: Point2) -> bool:
    """Whether the ray from vertex ``origin`` starts into the polygon."""
    coords = _ring_coords(poly)
    step = 1e-6 * _size(coords)
    x, y = coords[origin]
    return bool(shapely.contains_xy(poly, x + step * direction[0], y + step * direction[1]))


def inward_bisector(poly: Polygon, origin: int) -> Optional[Point2]:
    """The unit direction halving the polygon's interior angle at vertex ``origin``."""
    coords = _ring_coords(poly)
    n = len(coords)
    vx, vy = coords[origin]
    (ax, ay), (bx, by) = coords[origin - 1], coords[(origin + 1) % n]
    la, lb = math.hypot(ax - vx, ay - vy), math.hypot(bx - vx, by - vy)
    if la == 0.0 or lb == 0.0:
        return None
    sx, sy = (ax - vx) / la + (bx - vx) / lb, (ay - vy) / la + (by - vy) / lb
    norm = math.hypot(sx, sy)
    if norm < 1e-12:
        # The two edges are opposite: the bisector is perpendicular to them.
        sx, sy, norm = -(by - vy) / lb, (bx - vx) / lb, 1.0
    for cand in ((-sx / norm, -sy / norm), (sx / norm, sy / norm)):
        if points_inward(poly, origin, cand):
            return cand
    return None


def cut_along_ray(
    poly: Polygon,
    origin: int,
    direction: Point2,
    *,
    snap_tol: float = 0.0,
    min_part_area: float = 0.0,
) -> List[Polygon]:
    """Cut ``poly`` along the ray from vertex ``origin`` to the first boundary hit.

    A vertex that the ray passes within ``snap_tol`` of before the hit takes
    the cut instead, so the chord does not leave a hairline sliver beside it.

    Returns the two pieces, or ``[poly]`` when there is no valid cut: the
    polygon has holes, the ray does not start into it, the chord would run
    along an edge, or a piece would be smaller than ``min_part_area``.
    """
    if not isinstance(poly, Polygon) or poly.is_empty or poly.interiors:
        return [poly]
    coords = _ring_coords(poly)
    n = len(coords)
    norm = math.hypot(*direction)
    if n <= 3 or not 0 <= origin < n or norm == 0.0:
        return [poly]
    direction = (direction[0] / norm, direction[1] / norm)
    if not points_inward(poly, origin, direction):
        return [poly]
    eps = REL_EPS * _size(coords)
    hit = first_boundary_hit(coords, origin, direction, eps=eps)
    if hit is None:
        return [poly]

    if snap_tol > 0.0:
        vx, vy = coords[origin]
        nearest = None
        for k, (px, py) in enumerate(coords):
            if k in (origin, (origin - 1) % n, (origin + 1) % n):
                continue
            t = (px - vx) * direction[0] + (py - vy) * direction[1]
            if t <= eps or t > hit.t + snap_tol:
                continue
            if abs((px - vx) * direction[1] - (py - vy) * direction[0]) <= snap_tol:
                if nearest is None or t < nearest[0]:
                    nearest = (t, k)
        if nearest is not None:
            hit = BoundaryHit("vertex", nearest[1], nearest[0], coords[nearest[1]])
            if not _chord_inside(poly, coords, origin, hit.index):
                return [poly]

    rings = split_ring(coords, origin, hit)
    if rings is None:
        return [poly]
    parts = _pieces(poly, rings, eps=eps, min_part_area=min_part_area)
    return parts if parts is not None else [poly]


def _segments_cross(p: Point2, q: Point2, a: Point2, b: Point2) -> bool:
    """Whether segments pq and ab share any point."""
    return bool(shapely.intersects(shapely.LineString([p, q]), shapely.LineString([a, b])))


def _chord_inside(poly: Polygon, coords: Sequence[Point2], i: int, j: int) -> bool:
    """Whether the straight chord between vertices i and j runs inside the polygon."""
    n = len(coords)
    p, q = coords[i], coords[j]
    mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)
    if not shapely.contains_xy(poly, *mid):
        return False
    for k in range(n):
        m = (k + 1) % n
        if k in (i, j) or m in (i, j):
            continue
        if _segments_cross(p, q, coords[k], coords[m]):
            return False
    return True


def cut_along_diagonal(
    poly: Polygon, i: int, j: int, *, min_part_area: float = 0.0
) -> List[Polygon]:
    """Cut ``poly`` along the straight chord between vertices ``i`` and ``j``.

    Returns the two pieces, or ``[poly]`` when the chord is not a diagonal
    running inside the polygon or a piece would be smaller than ``min_part_area``.
    """
    if not isinstance(poly, Polygon) or poly.is_empty or poly.interiors:
        return [poly]
    coords = _ring_coords(poly)
    n = len(coords)
    if n <= 3 or i == j or not (0 <= i < n and 0 <= j < n) or (j - i) % n in (1, n - 1):
        return [poly]
    if not _chord_inside(poly, coords, i, j):
        return [poly]
    rings = split_ring(coords, i, BoundaryHit("vertex", j, 0.0, coords[j]))
    if rings is None:
        return [poly]
    parts = _pieces(poly, rings, eps=REL_EPS * _size(coords), min_part_area=min_part_area)
    return parts if parts is not None else [poly]
