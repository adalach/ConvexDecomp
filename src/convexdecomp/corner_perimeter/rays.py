"""Corner rays: the corners and their bisectors, casting and cutting the rays, the wing ends, and the helpers of
rules 1 and 2."""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import linemerge, substring

from .geometry import EPS, Line, first_hit, line_x, points_of, reach, unit
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


def wing_end_pairs(cs, cfg: CornerRayConfig) -> list[tuple[int, int, int, int]]:
    """The wing ends, as (h, k, m, n) indices into cs: k-m is an end wall shorter than ``wing_end_max_m`` between
    two convex corners, whose side walls h-k and m-n are parallel within ``wing_end_parallel_deg`` (the two corner
    angles add up to 180° within it) and, with ``wing_end_long_sides``, at least as long as the end wall."""
    out: list[tuple[int, int, int, int]] = []
    if cfg.wing_end_max_m <= 0:
        return out
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
            out.append((h, k, m, n))
    return out


def wing_end_corners(cs, cfg: CornerRayConfig) -> set[int]:
    """The corners (indices into cs) that cast no ray: the two corners of every wing end (:func:`wing_end_pairs`)."""
    return {i for _, k, m, _ in wing_end_pairs(cs, cfg) for i in (k, m)}


def short_wall_plan(poly: Polygon, cs, skip: set[int], cfg: CornerRayConfig):
    """One ray per short wall (``short_wall_one_ray``): ``(drop, insert, records)``. ``drop`` are the corners
    (indices into cs) that cast no ray of their own, ``insert`` the rays cast in their place, as corners
    ``(point, direction, angle, ring)`` keyed by the index of the corner whose place they take in ring order,
    ``records`` one dict per short wall (``kind`` step or chamfer, ``wall``, ``length``, the ``dropped`` corner
    points, the ``ray`` point, and whether it was aimed at the band line, ``to_band``, or is a tooth's, ``tooth``).

    A wall k-m between two neighbouring corners of a ring is short when shorter than ``short_wall_m``; it counts when
    both corners cast rays (neither is in ``skip``, the wing-end corners). A step (one reflex, one convex corner):
    only the reflex corner casts, so a line still leaves every reflex corner. A chamfer (two convex corners, with
    ``short_wall_chamfers``) whose walls before and after are at least ``short_wall_m`` long: one ray from the middle
    of the wall (``short_wall_midpoint``), or from the sharper corner. A wall between two reflex corners keeps both
    rays: they part, and both corners need a line.

    With ``short_wall_to_band`` the ray runs to the band line's corner: where the dropped convex corner's ray meets
    the band line (a step), or where a ray from the meeting point of the chamfer's two rays, along the bisector of
    the walls before and after, meets it (a chamfer); a step's reflex corner keeps its bisector when the aimed ray
    would leave one side of the corner at 180 degrees or more. With ``short_wall_tooth``, of the two reflex corners
    of a tooth (a convex corner between two short steps) the second continues the tooth's wall."""
    drop: set[int] = set()
    insert: dict[int, tuple] = {}
    records: list[dict] = []
    if not cfg.short_wall_one_ray or cfg.short_wall_m <= 0:
        return drop, insert, records
    short = cfg.short_wall_m
    oriented = orient(poly, sign=1.0)
    rings = [oriented.exterior, *oriented.interiors]
    ring_xy = [np.asarray(r.coords)[:-1] for r in rings]
    inside = poly.buffer(EPS)
    core0 = poly.buffer(-cfg.band_m, join_style="mitre", mitre_limit=10.0) if cfg.short_wall_to_band else Polygon()
    band0 = core0.boundary if not core0.is_empty else LineString()
    far = reach(poly)

    def band_corner(start: np.ndarray, src: np.ndarray, d: np.ndarray):
        """Where the ray from src along d first meets the band line, if before the facade and a straight line from
        start reaches it inside the footprint; else None."""
        if band0.is_empty:
            return None
        s, kind = first_hit(src, d, [(poly.boundary, "facade"), (band0, "band")], far)
        x = src + s * d
        if kind != "band" or np.linalg.norm(x - start) < EPS or not inside.covers(LineString([start, x])):
            return None
        return x

    def wall_normal(r: int, p: np.ndarray, after: bool) -> np.ndarray:
        """The inward normal of the outline edge arriving at (after=False) or leaving (after=True) the vertex p."""
        c = ring_xy[r]
        i = int(np.argmin(np.linalg.norm(c - p, axis=1)))
        a, b = (c[i], c[(i + 1) % len(c)]) if after else (c[i - 1], c[i])
        d = unit(b - a)
        return np.array([-d[1], d[0]])

    walls = []  # (length, previous wall, next wall, k, m)
    for r in sorted({c[3] for c in cs}):
        ks = [k for k, c in enumerate(cs) if c[3] == r]
        n = len(ks)
        if n < 3:
            continue
        for j in range(n):
            h, k, m, q = ks[j - 1], ks[j], ks[(j + 1) % n], ks[(j + 2) % n]
            length = float(np.linalg.norm(cs[m][0] - cs[k][0]))
            if length >= short or k in skip or m in skip:
                continue
            walls.append((length, float(np.linalg.norm(cs[k][0] - cs[h][0])),
                          float(np.linalg.norm(cs[q][0] - cs[m][0])), k, m))
    walls.sort(key=lambda w: w[0])  # the shortest first

    first: dict[int, int] = {}  # a dropped convex corner: the reflex corner of the (shorter) step that dropped it
    for length, _, _, k, m in walls:  # steps: only the reflex corner's ray
        rk, rm = cs[k][2] > 180.0, cs[m][2] > 180.0
        if rk == rm:
            continue
        convex, reflex = (m, k) if rk else (k, m)
        drop.add(convex)
        p, bis, angle, r = cs[reflex]
        d, aimed, tooth = bis, False, False
        if cfg.short_wall_tooth and first.get(convex, reflex) != reflex and reflex not in insert:
            # a tooth: the convex corner sits between two short steps, and the first step's reflex corner took its
            # ray's place. This reflex corner continues the tooth's wall, so the two reflex corners' lines part
            # instead of bounding a needle (one side of the corner straight, the other its angle less 180°)
            d, tooth = unit(p - cs[convex][0]), True
        elif cfg.short_wall_to_band and reflex not in insert:
            x = band_corner(p, cs[convex][0], cs[convex][1])
            if x is not None:
                u = unit(x - p)
                # the ray must leave both sides of the reflex corner under 180 degrees
                if math.degrees(math.acos(np.clip(u @ bis, -1.0, 1.0))) < (angle - 180.0) / 2.0 - 1.0:
                    d, aimed = u, True
        if aimed or tooth:
            insert[reflex] = (p, d, angle, r)
            drop.add(reflex)
        first.setdefault(convex, reflex)
        records.append(dict(kind="step", wall=(cs[k][0], cs[m][0]), length=length, dropped=[cs[convex][0]],
                            ray=p, to_band=aimed, tooth=tooth))
    if not cfg.short_wall_chamfers:
        return drop, insert, records

    for length, before, after, k, m in walls:  # chamfers between longer walls: one ray
        if cs[k][2] > 180.0 or cs[m][2] > 180.0 or k in drop or m in drop or before < short or after < short:
            continue
        r = cs[k][3]
        if cfg.short_wall_midpoint:
            ring = rings[r]
            dk, dm = ring.project(Point(cs[k][0])), ring.project(Point(cs[m][0]))
            if dm <= dk:
                dm += ring.length
            start = np.asarray(ring.interpolate(((dk + dm) / 2.0) % ring.length).coords[0])
            bis = cs[k][1] + cs[m][1]
            d, angle, at, dropped = bis / np.linalg.norm(bis), cs[k][2] + cs[m][2] - 180.0, k, [cs[k][0], cs[m][0]]
        else:
            at = k if cs[k][2] <= cs[m][2] else m
            start, d, angle = cs[at][0], cs[at][1], cs[at][2]
            dropped = [cs[m if at == k else k][0]]
        aimed = False
        meet = line_x(cs[k][0], cs[k][1], cs[m][0], cs[m][0] + cs[m][1])  # where the two rays meet
        uv = wall_normal(r, cs[k][0], after=False) + wall_normal(r, cs[m][0], after=True)
        if cfg.short_wall_to_band and meet is not None and (meet - cs[k][0]) @ cs[k][1] > 0 \
                and np.linalg.norm(uv) > 1e-6:
            x = band_corner(start, meet, unit(uv))
            if x is not None and (x - start) @ d > 0:
                d, aimed = unit(x - start), True
        insert[at] = (start, d, angle, r)
        drop |= {k, m}
        records.append(dict(kind="chamfer", wall=(cs[k][0], cs[m][0]), length=length, dropped=dropped,
                            ray=start, to_band=aimed, tooth=False))
    return drop, insert, records


def _angle_between(u: np.ndarray, v: np.ndarray) -> float:
    """The unsigned angle between two directions, 0 to 180 degrees."""
    return math.degrees(math.acos(np.clip(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)), -1.0, 1.0)))


def bay_runs(poly: Polygon, cs, cfg: CornerRayConfig) -> list[tuple[int, tuple[int, ...], int]]:
    """The bays, as (a, inner, b) indices into cs: a run of at least ``bay_min_corners`` consecutive corners
    between the two reflex base corners a and b, every inner corner convex (or reflex by less than
    ``bay_wiggle_deg``), with

    - the opening (a to b) inside the footprint and at most ``bay_width_band`` band depths long,
    - every inner corner outside the opening's line, the farthest at most ``bay_depth_band`` band depths out,
    - the wall before a and the wall after b each within ``bay_edge_deg`` of the opening's direction: the facade
      runs on along one line on both sides (the continuous edge the bay protrudes from).

    The inner corners cast no ray; the base corners do."""
    out: list[tuple[int, tuple[int, ...], int]] = []
    if not cfg.bays:
        return out
    max_w, max_d = cfg.bay_width_band * cfg.band_m, cfg.bay_depth_band * cfg.band_m
    inside = poly.buffer(1e-6)
    for r in sorted({c[3] for c in cs}):
        ks = [k for k, c in enumerate(cs) if c[3] == r]
        n = len(ks)
        reflex = [cs[k][2] >= 180.0 + cfg.bay_wiggle_deg for k in ks]
        if sum(reflex) < 2:
            continue
        for t in range(n):
            if not reflex[t]:
                continue
            run, u = [], (t + 1) % n
            while not reflex[u]:
                run.append(u)
                u = (u + 1) % n
            if len(run) < cfg.bay_min_corners:
                continue
            a, b = ks[t], ks[u]
            pa, pb = cs[a][0], cs[b][0]
            chord = pb - pa
            width = float(np.linalg.norm(chord))
            if width < EPS or width > max_w:
                continue
            e = chord / width
            out_side = [-(e[0] * (cs[ks[i]][0][1] - pa[1]) - e[1] * (cs[ks[i]][0][0] - pa[0])) for i in run]
            if min(out_side) < -1e-3 or max(out_side) > max_d:  # the inner corners lie outside the opening
                continue
            before, after = pa - cs[ks[(t - 1) % n]][0], cs[ks[(u + 1) % n]][0] - pb
            if max(_angle_between(before, chord), _angle_between(after, chord)) > cfg.bay_edge_deg:
                continue
            if not inside.covers(LineString([pa, pb])):
                continue
            out.append((a, tuple(ks[i] for i in run), b))
    return out


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
