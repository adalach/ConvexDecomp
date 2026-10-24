"""Tidying the zone corners: inner points snap onto close outline points, close inner points become one, corners
within a few degrees of straight are straightened and points straight in every zone are dropped.

Every move is a move of one or more inner points (never a point of the outline), applied to every zone that has the
point, and it is kept only if the zones stay valid and tile the footprint, no convex zone becomes non-convex, no zone
gets narrower than the zone width (a core part the narrowest core) unless it already was, and no corner sharper than
``sharp_corner_deg`` and no speck appears. A zone may vanish under a move only if it is thin or a speck.
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Point, Polygon
from shapely.geometry.polygon import orient

from convexdecomp.core.scores import minimum_width

from .geometry import (EPS, SAME, angle_at, clean_ring, close_groups, corner_points, is_convex, kind_of, polygons,
                       rings_of, sharp_corner_count, signed_area, unit)
from .settings import CornerRayConfig


def _near_straight(z: Polygon, tol_deg: float) -> int:
    """How many corners of z's outer ring (straight vertices dropped) are within tol_deg of 180°."""
    c = clean_ring(np.asarray(orient(z, 1.0).exterior.coords)[:-1])
    return sum(1 for t in range(len(c)) if abs(angle_at(c[t - 1], c[t], c[(t + 1) % len(c)]) - 180.0) < tol_deg)


def try_move(poly: Polygon, zones: list[tuple[Polygon, str]], src: np.ndarray, dst: np.ndarray,
             cfg: CornerRayConfig):
    """The zones after every ring vertex at a point of src moves to the point of dst in the same row (NaN: the vertex
    is dropped), all at once, with the zones that changed before and after (``old``, ``new``); None if a check fails.
    A zone may vanish (thin zones and specks only) or, with ``split_pinched``, fall apart into two zones where the
    move makes it touch itself. src must hold inner points only."""
    src, dst = np.asarray(src, dtype=float).reshape(-1, 2), np.asarray(dst, dtype=float).reshape(-1, 2)
    after: dict[int, list[Polygon]] = {}
    old, new = [], []
    for i, (z, k) in enumerate(zones):
        rings, hit = [], False
        for c in rings_of(z):
            d = np.linalg.norm(c[:, None] - src[None], axis=2)
            j = np.argmin(d, axis=1)
            m = d[np.arange(len(c)), j] < SAME
            if m.any():
                hit = True
                c = c.copy()
                c[m] = dst[j[m]]
                c = c[~np.isnan(c[:, 0])]
                c = c[np.linalg.norm(c - np.roll(c, 1, axis=0), axis=1) > 1e-9] if len(c) else c
            rings.append(c)
        if not hit:
            continue
        old.append(z)
        ext = rings[0]
        if len(ext) < 3 or abs(signed_area(ext)) < 1e-9:
            if k == "core" or (k != "thin" and z.area >= cfg.speck_m2):
                return None  # only a thin zone or a speck may vanish
            after[i] = []
            continue
        if np.sign(signed_area(ext)) != np.sign(signed_area(rings_of(z)[0])):
            return None  # the ring turned over: the point crossed an edge
        p = Polygon(ext, [h for h in rings[1:] if len(h) >= 3 and abs(signed_area(h)) > 1e-9])
        parts = [p]
        if not p.is_valid:
            if not cfg.split_pinched or p.interiors:
                return None
            # the zone touches itself where the move brought two of its corners together: its two loops become
            # two zones (a ring that crosses itself is refused: its loops would not add up to its signed area)
            parts = [q for q in polygons(shapely.make_valid(p)) if q.area > 1e-9]
            if len(parts) != 2 or abs(sum(q.area for q in parts) - abs(signed_area(ext))) > 1e-9 * max(z.area, 1.0):
                return None
            if not all(is_convex(q) for q in parts):
                return None  # a split is worth its extra zone only if both parts are convex
        limit = cfg.narrow_core_m if k == "core" else cfg.min_zone_width_m
        for q in parts:
            if is_convex(z) and not is_convex(q):
                return None
            if minimum_width(q) < min(limit, minimum_width(z)) - 1e-9:
                return None
            if k != "core" and z.area >= cfg.speck_m2 and q.area < cfg.speck_m2:
                return None
        deg = cfg.sharp_corner_deg
        if sum(sharp_corner_count(q, deg) for q in parts) > sharp_corner_count(z, deg):
            return None
        after[i] = parts
        new += parts
    if not old:
        return None
    if abs(sum(z.area for z in old) - sum(q.area for q in new)) > 1e-6 * poly.area \
            or shapely.union_all(old).symmetric_difference(shapely.union_all(new)).area > 1e-6 * poly.area:
        return None  # the tiling would break
    out = []
    for i, (z, k) in enumerate(zones):
        out += [(q, kind_of(q, k, cfg.min_zone_width_m)) for q in after[i]] if i in after else [(z, k)]
    return out, old, new


def snap_to_outline(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig, reach: float):
    """An inner zone corner closer than reach to an outline point (a footprint corner or a line end on the facade)
    moves onto it, the closest first. Returns the zones and the outline points that took a corner."""
    snapped, tried = [], set()
    while True:
        pts, fixed = corner_points(poly, zones)
        if fixed.all() or not fixed.any():
            return zones, snapped
        inner, outer = pts[~fixed], pts[fixed]
        d = np.linalg.norm(inner[:, None] - outer[None], axis=2)
        cands = sorted((d[i, j], i, j) for i, j in zip(*np.nonzero(d < reach)))
        for _, i, j in cands:
            key = (round(inner[i][0], 6), round(inner[i][1], 6), round(outer[j][0], 6), round(outer[j][1], 6))
            if key in tried:
                continue
            tried.add(key)
            res = try_move(poly, zones, inner[i], outer[j], cfg)
            if res is not None:
                zones = res[0]
                snapped.append(outer[j])
                break
        else:
            return zones, snapped


def merge_close_again(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig, reach: float):
    """Rule 5 once more, on the zones as they are now: inner zone corners closer than reach become one, in groups at
    most ``corner_group_span_m`` across (grown from the closest pairs, :func:`~.geometry.close_groups`). A group
    moves to its mean, or, if that fails a check, onto one of its own points (the one the others are closest to
    first). Returns the zones and the points the groups became."""
    pts, fixed = corner_points(poly, zones)
    merged = []
    for members in close_groups(pts, fixed, reach, cfg.corner_group_span_m):
        m = pts[members]
        targets = [m.mean(axis=0)] + [m[k] for k in np.argsort(np.linalg.norm(m[:, None] - m[None], axis=2).sum(1))]
        for t in targets:
            res = try_move(poly, zones, m, np.repeat(t[None], len(m), axis=0), cfg)
            if res is not None:
                zones = res[0]
                merged.append(t)
                break
    return zones, merged


def _chain(raw: np.ndarray, a: np.ndarray, p: np.ndarray, b: np.ndarray):
    """The vertices of the ring raw strictly between a and p, and between p and b (running forward)."""
    def at(q):
        return int(np.argmin(np.linalg.norm(raw - q, axis=1)))

    n = len(raw)
    ia, ip, ib = at(a), at(p), at(b)
    first = [raw[(ia + s) % n] for s in range(1, (ip - ia) % n)]
    second = [raw[(ip + s) % n] for s in range(1, (ib - ip) % n)]
    return first, second


def straighten_corners(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig, tol_deg: float):
    """An inner point where a zone's corner is within tol_deg of 180° moves onto the straight line between that
    corner's two neighbours (the foot of the perpendicular); points lying on the zone's two edges move with it, so
    the edges stay straight. Kept if the checks hold and the changed zones have fewer non-convex zones, or as many
    and fewer corners within tol_deg of straight. Closest to straight first. Returns the zones and the moved points
    (where they went)."""
    moved, tried = [], set()
    while True:
        cands = []
        for i, (z, _) in enumerate(zones):
            raw = np.asarray(orient(z, 1.0).exterior.coords)[:-1]
            c = clean_ring(raw)
            for t in range(len(c)):
                a, p, b = c[t - 1], c[t], c[(t + 1) % len(c)]
                dev = abs(angle_at(a, p, b) - 180.0)
                if dev < tol_deg and poly.boundary.distance(Point(p)) >= EPS:
                    cands.append((dev, i, a, p, b, raw))
        accepted = False
        for dev, i, a, p, b, raw in sorted(cands, key=lambda c_: c_[0]):
            key = (round(p[0], 6), round(p[1], 6), round(zones[i][0].area, 6))
            if key in tried:
                continue
            tried.add(key)
            w = unit(b - a)
            q = a + ((p - a) @ w) * w
            first, second = _chain(raw, a, p, b)
            src, dst = [p], [q]
            for t_ in first:
                src.append(t_)
                dst.append(a + np.linalg.norm(t_ - a) / np.linalg.norm(p - a) * (q - a))
            for t_ in second:
                src.append(t_)
                dst.append(q + np.linalg.norm(t_ - p) / np.linalg.norm(b - p) * (b - q))
            if any(poly.boundary.distance(Point(s)) < EPS for s in src[1:]):
                continue  # a point of the outline lies on one of the edges: it never moves
            res = try_move(poly, zones, np.asarray(src), np.asarray(dst), cfg)
            if res is None:
                continue
            new_zones, old, new = res
            nc_old = sum(not is_convex(z) for z in old)
            nc_new = sum(not is_convex(z) for z in new)
            ns_old = sum(_near_straight(z, tol_deg) for z in old)
            ns_new = sum(_near_straight(z, tol_deg) for z in new)
            if nc_new < nc_old or (nc_new == nc_old and ns_new < ns_old):
                zones = new_zones
                moved.append(q)
                accepted = True
                break
        if not accepted:
            return zones, moved


def drop_collinear(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig, tol_deg: float):
    """An inner point that is straight within tol_deg in every zone that has it is dropped from them. Returns the
    zones and the dropped points."""
    dropped, tried = [], set()
    sin_tol = math.sin(math.radians(tol_deg))
    while True:
        seen: dict[tuple[float, float], list] = {}  # point -> [point, straight in every ring so far]
        for z, _ in zones:
            for c in rings_of(z):
                u = np.roll(c, -1, axis=0) - c  # edge from each vertex to the next
                v = c - np.roll(c, 1, axis=0)   # edge into each vertex
                lu, lv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
                ok = (lu > 1e-9) & (lv > 1e-9)
                with np.errstate(invalid="ignore", divide="ignore"):
                    sin = np.abs(v[:, 0] * u[:, 1] - v[:, 1] * u[:, 0]) / (lu * lv)
                straight = ok & (sin < sin_tol) & ((v * u).sum(axis=1) > 0)
                for p, s in zip(c, straight):
                    key = (round(p[0], 7), round(p[1], 7))
                    if key in seen:
                        seen[key][1] = seen[key][1] and bool(s)
                    else:
                        seen[key] = [p, bool(s)]
        for key, (p, straight) in seen.items():
            if not straight or key in tried or poly.boundary.distance(Point(p)) < EPS:
                continue
            tried.add(key)
            res = try_move(poly, zones, p, np.array([np.nan, np.nan]), cfg)
            if res is not None:
                zones = res[0]
                dropped.append(p)
                break
        else:
            return zones, dropped


def tidy_corners(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig) -> tuple[list, dict]:
    """The switched-on tidy steps in turn: snap to the outline, close points again, straighten, drop collinear points.
    Returns the zones and what each step did."""
    done = {"snapped": [], "merged_again": [], "straightened": [], "collinear_dropped": []}
    if cfg.snap_to_outline:
        zones, done["snapped"] = snap_to_outline(poly, zones, cfg, cfg.tidy_close_m)
    if cfg.merge_close_again:
        zones, done["merged_again"] = merge_close_again(poly, zones, cfg, cfg.tidy_close_m)
    if cfg.straighten_corners:
        zones, done["straightened"] = straighten_corners(poly, zones, cfg, cfg.straight_deg)
    if cfg.drop_collinear:
        zones, done["collinear_dropped"] = drop_collinear(poly, zones, cfg, cfg.collinear_deg)
    return zones, done
