"""Exact openings of a hole: the cuts that turn a polygon with a hole into two pieces.

A single chord from a hole to the outer boundary leaves one piece that touches
itself along the chord. Two chords are needed, and together they cut the
polygon into two pieces that each take a part of the hole's boundary. Each
chord runs from a vertex of the hole into the polygon, along one of the hole's
edges carried on or along the interior bisector, to the first point of the
outer boundary it reaches: the same chords the search cuts at a reflex vertex
of the outer ring. Where the hole already touches the outer boundary, the touch
point counts as a chord of length zero, and one more chord opens the hole.

The pieces are built from the vertex lists of the outer ring and the hole, like
the cuts in ``convexdecomp.geometry.cuts``, so their areas add up to the
polygon's by construction. Any further holes go to the piece that contains
them and are opened later in the same way.

A chord that reaches another hole before the outer boundary is not used. If no
pair of chords opens a hole, ``hole_openings`` returns nothing and the caller
has to fall back to another split.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import List, NamedTuple, Optional, Sequence, Tuple

import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from convexdecomp.geometry.cuts import AREA_REL_TOL, REL_EPS, clean_ring

__all__ = ["HoleChord", "Opening", "hole_chords", "hole_openings", "open_hole"]

Point2 = Tuple[float, float]


class HoleChord(NamedTuple):
    """A chord from vertex ``vertex`` of a hole to the outer ring.

    ``kind`` is ``"prev"`` (the hole edge ending at the vertex, carried on),
    ``"next"`` (the edge starting at the vertex, carried on backwards),
    ``"bis"`` (the interior bisector) or ``"touch"`` (the vertex lies on the
    outer ring; the chord has length zero). The chord ends at ``point``, on
    outer edge ``edge`` at parameter ``s`` (0 at the edge's first vertex).
    """

    vertex: int
    kind: str
    edge: int
    s: float
    point: Point2
    length: float


class Opening(NamedTuple):
    """Two chords that open a hole, and the two pieces they cut."""

    label: str
    chords: Tuple[HoleChord, HoleChord]
    pieces: Tuple[Polygon, Polygon]


def _coords(ring) -> List[Point2]:
    return [(float(x), float(y)) for x, y in list(ring.coords)[:-1]]


def _size(poly: Polygon) -> float:
    minx, miny, maxx, maxy = poly.bounds
    return max(maxx - minx, maxy - miny, 1e-12)


def _ray_hit(
    coords: Sequence[Point2],
    origin: Point2,
    d: Point2,
    eps: float,
    skip: Optional[int] = None,
) -> Optional[Tuple[float, int, float]]:
    """Nearest point where the ray from ``origin`` along ``d`` meets the ring: ``(t, edge, s)``.

    The two edges at vertex ``skip`` (the ray's own vertex) are left out. An
    edge lying along the ray counts as reached at its nearer endpoint.
    """
    best: Optional[Tuple[float, int, float]] = None
    n = len(coords)
    for j in range(n):
        k = (j + 1) % n
        if skip is not None and skip in (j, k):
            continue
        (ax, ay), (bx, by) = coords[j], coords[k]
        ex, ey = bx - ax, by - ay
        edge_len = math.hypot(ex, ey)
        if edge_len <= eps:
            continue
        wx, wy = ax - origin[0], ay - origin[1]
        den = d[0] * ey - d[1] * ex
        if abs(den) <= REL_EPS * edge_len:
            if abs(wx * d[1] - wy * d[0]) <= eps:  # collinear: first touched at the nearer end
                for s, (px, py) in ((0.0, (ax, ay)), (1.0, (bx, by))):
                    t = (px - origin[0]) * d[0] + (py - origin[1]) * d[1]
                    if t > eps and (best is None or t < best[0] - eps):
                        best = (t, j, s)
            continue
        t = (wx * ey - wy * ex) / den
        s = (wx * d[1] - wy * d[0]) / den
        if t <= eps or s * edge_len < -eps or (s - 1.0) * edge_len > eps:
            continue
        if best is None or t < best[0] - eps:
            best = (t, j, min(max(s, 0.0), 1.0))
    return best


def _on_vertex(edge: int, s: float, n: int, edge_len: float, eps: float) -> Tuple[int, float]:
    """Move a hit within ``eps`` of an edge's end onto that vertex (``s = 0`` of its edge)."""
    if s * edge_len <= eps:
        return edge, 0.0
    if (1.0 - s) * edge_len <= eps:
        return (edge + 1) % n, 0.0
    return edge, s


def hole_chords(poly: Polygon, hole: int = 0, *, snap_tol: float = 0.0) -> List[HoleChord]:
    """Every chord from a vertex of hole ``hole`` that reaches the outer ring first.

    ``poly`` must be oriented with ``shapely.geometry.polygon.orient(poly, 1.0)``.
    An outer vertex that the chord passes within ``snap_tol`` of, before its end,
    takes the chord instead, as in ``cut_along_ray``.
    """
    E = _coords(poly.exterior)
    H = _coords(poly.interiors[hole])
    others = [_coords(r) for i, r in enumerate(poly.interiors) if i != hole]
    eps = REL_EPS * _size(poly)
    n, m = len(E), len(H)
    out: List[HoleChord] = []

    for v in range(m):
        p = H[v]
        # The hole already touches the outer ring here.
        for j in range(n):
            (ax, ay), (bx, by) = E[j], E[(j + 1) % n]
            ex, ey = bx - ax, by - ay
            edge_len = math.hypot(ex, ey)
            if edge_len <= eps:
                continue
            s = ((p[0] - ax) * ex + (p[1] - ay) * ey) / (edge_len * edge_len)
            if -eps <= s * edge_len and (s - 1.0) * edge_len <= eps:
                if abs((p[0] - ax) * ey - (p[1] - ay) * ex) / edge_len <= eps:
                    e, s = _on_vertex(j, min(max(s, 0.0), 1.0), n, edge_len, eps)
                    out.append(HoleChord(v, "touch", e, s, E[e] if s == 0.0 else p, 0.0))
                    break

        a, b = H[v - 1], H[(v + 1) % m]
        la, lb = math.dist(a, p), math.dist(b, p)
        if la <= eps or lb <= eps:
            continue
        ua = ((a[0] - p[0]) / la, (a[1] - p[1]) / la)
        ub = ((b[0] - p[0]) / lb, (b[1] - p[1]) / lb)
        directions = [("prev", (-ua[0], -ua[1])), ("next", (-ub[0], -ub[1]))]
        sx, sy = ua[0] + ub[0], ua[1] + ub[1]
        norm = math.hypot(sx, sy)
        if norm > 1e-12:
            directions += [("bis", (sx / norm, sy / norm)), ("bis", (-sx / norm, -sy / norm))]

        step = 1e-6 * _size(poly)
        for kind, d in directions:
            if not shapely.contains_xy(poly, p[0] + step * d[0], p[1] + step * d[1]):
                continue  # does not start into the polygon
            hit = _ray_hit(E, p, d, eps)
            if hit is None:
                continue
            own = _ray_hit(H, p, d, eps, skip=v)
            if own is not None and own[0] < hit[0]:
                continue  # runs back into its own hole first
            if any((o := _ray_hit(r, p, d, eps)) is not None and o[0] < hit[0] for r in others):
                continue  # reaches another hole first
            t, j, s = hit
            edge_len = math.dist(E[j], E[(j + 1) % n])
            j, s = _on_vertex(j, s, n, edge_len, eps)

            if snap_tol > 0.0:
                nearest = None
                for k, (px, py) in enumerate(E):
                    tk = (px - p[0]) * d[0] + (py - p[1]) * d[1]
                    if tk <= eps or tk > t + snap_tol:
                        continue
                    if abs((px - p[0]) * d[1] - (py - p[1]) * d[0]) <= snap_tol:
                        if nearest is None or tk < nearest[0]:
                            nearest = (tk, k)
                if nearest is not None and not (s == 0.0 and nearest[1] == j):
                    chord = LineString([p, E[nearest[1]]])
                    if not shapely.contains(poly, chord):
                        continue
                    j, s = nearest[1], 0.0

            point = E[j] if s == 0.0 else (E[j][0] + s * (E[(j + 1) % n][0] - E[j][0]),
                                           E[j][1] + s * (E[(j + 1) % n][1] - E[j][1]))
            out.append(HoleChord(v, kind, j, s, point, math.dist(p, point)))

    # The same chord can come from two directions (a bisector along an edge, a snap).
    unique: List[HoleChord] = []
    for c in out:
        if not any(c.vertex == u.vertex and math.dist(c.point, u.point) <= eps for u in unique):
            unique.append(c)
    return unique


def _walk(coords: Sequence[Point2], start: int, stop: int) -> List[Point2]:
    out, k = [coords[start]], start
    while k != stop:
        k = (k + 1) % len(coords)
        out.append(coords[k])
    return out


def open_hole(
    poly: Polygon,
    c1: HoleChord,
    c2: HoleChord,
    hole: int = 0,
    *,
    min_part_area: float = 0.0,
) -> Optional[Tuple[Polygon, Polygon]]:
    """The two pieces cut by chords ``c1`` and ``c2`` of hole ``hole``, or None.

    None when the chords start at the same hole vertex, end at the same point,
    cross each other, or leave a piece smaller than ``min_part_area``.
    ``poly`` must be oriented with ``orient(poly, 1.0)``: the outer ring runs
    counter-clockwise and the hole clockwise, so both keep the floor on their
    left and both are walked forwards.
    """
    if c1.vertex == c2.vertex:
        return None
    eps = REL_EPS * _size(poly)
    if math.dist(c1.point, c2.point) <= eps:
        return None
    E = _coords(poly.exterior)
    H = _coords(poly.interiors[hole])
    others = [r for i, r in enumerate(poly.interiors) if i != hole]

    # The outer ring with the chords' ends inserted.
    points = sorted([(k, 0.0, E[k]) for k in range(len(E))]
                    + [(c.edge, c.s, c.point) for c in (c1, c2) if c.s > 0.0],
                    key=lambda q: (q[0], q[1]))
    ring = [q[2] for q in points]
    a = next(k for k, q in enumerate(ring) if math.dist(q, c1.point) <= eps)
    b = next(k for k, q in enumerate(ring) if math.dist(q, c2.point) <= eps)

    pieces = []
    for outer, inner in ((_walk(ring, a, b), _walk(H, c2.vertex, c1.vertex)),
                         (_walk(ring, b, a), _walk(H, c1.vertex, c2.vertex))):
        cleaned = clean_ring(outer + inner, eps=eps)
        if len(cleaned) < 3:
            return None
        shell = Polygon(cleaned)
        if shell.is_empty or not shell.is_valid or shell.area <= 0.0:
            return None  # the chords cross
        inside = [r for r in others if shell.contains(Polygon(r).representative_point())]
        piece = Polygon(cleaned, inside)
        if not piece.is_valid:
            return None
        pieces.append(piece)
    if any(p.area < min_part_area for p in pieces):
        return None
    if abs(sum(p.area for p in pieces) - poly.area) > AREA_REL_TOL * poly.area:
        return None
    return pieces[0], pieces[1]


def _label(c: HoleChord) -> str:
    return f"h{c.vertex} {c.kind}"


def hole_openings(
    poly: Polygon,
    hole: int = 0,
    *,
    snap_tol: float = 0.0,
    min_part_area: float = 0.0,
) -> List[Opening]:
    """Every distinct pair of chords that opens hole ``hole``, shortest chords first.

    Two pairs that cut the same two pieces count once.
    """
    if not isinstance(poly, Polygon) or poly.is_empty or not 0 <= hole < len(poly.interiors):
        return []
    poly = orient(poly, 1.0)
    chords = hole_chords(poly, hole, snap_tol=snap_tol)
    openings: List[Opening] = []
    seen = set()
    for c1, c2 in sorted(combinations(chords, 2), key=lambda pair: pair[0].length + pair[1].length):
        pieces = open_hole(poly, c1, c2, hole, min_part_area=min_part_area)
        if pieces is None:
            continue
        key = tuple(sorted(
            tuple(sorted((round(x, 6), round(y, 6)) for x, y in p.exterior.coords[:-1])) for p in pieces
        ))
        if key in seen:
            continue
        seen.add(key)
        openings.append(Opening(f"{_label(c1)} + {_label(c2)}", (c1, c2), pieces))
    return openings
