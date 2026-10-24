"""Which corners cast rays: all corners but the wing ends' and the bays' inner corners, and of a short wall's two
corners only one (or one ray from its middle).

:func:`ray_sources` decides it for a footprint (:class:`RaySources`); :func:`casting_corners` gives the corners that
cast rays, with the short walls' rays in their place; :func:`bay_caps` the parts of the footprint cut off at the bays'
openings before the band is offset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

from .geometry import EPS, angle_between, first_hit, line_x, reach, unit
from .settings import CornerRayConfig


@dataclass
class ShortWalls:
    """One ray per short wall (:func:`short_wall_plan`): ``drop`` are the corners (indices into the corners) that
    cast no ray of their own, ``insert`` the rays cast in their place, as corners ``(point, direction, angle,
    ring)`` keyed by the index of the corner whose place they take in ring order, ``records`` one dict per short
    wall (``kind`` step or chamfer, ``wall``, ``length``, the ``dropped`` corner points, the ``ray`` point, and
    whether it was aimed at the band line, ``to_band``, or is a tooth's, ``tooth``)."""

    drop: set[int] = field(default_factory=set)
    insert: dict[int, tuple] = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)


@dataclass
class RaySources:
    """Which corners (indices into the footprint's corners) cast rays: ``wing_ends`` as ``(h, k, m, n)`` (k-m the
    end wall), ``bays`` as ``(a, inner corners, b)``, ``rayless`` the wing-end and bay corners that cast no ray, and
    the ``short_walls``."""

    wing_ends: list[tuple[int, int, int, int]]
    bays: list[tuple[int, tuple[int, ...], int]]
    rayless: set[int]
    short_walls: ShortWalls


def ray_sources(poly: Polygon, cs, cfg: CornerRayConfig) -> RaySources:
    """The wing ends (:func:`wing_end_pairs`), the bays (:func:`bay_runs`) and the short walls
    (:func:`short_wall_plan`) of a footprint with the corners cs. A bay whose two corners are a wing end stays a wing
    end; a wing end inside a longer bay is the bay's. The short walls are decided on the corners that still cast
    rays: a wing end or bay a check gives its rays back casts all of them."""
    wing_ends = wing_end_pairs(cs, cfg)
    bays = [bay for bay in bay_runs(poly, cs, cfg) if not any(set(bay[1]) == {k, m} for _, k, m, _ in wing_ends)]
    bay_set = {i for _, inner, _ in bays for i in inner}
    wing_ends = [w for w in wing_ends if not {w[1], w[2]} <= bay_set]
    rayless = {i for _, k, m, _ in wing_ends for i in (k, m)} | bay_set
    return RaySources(wing_ends, bays, rayless, short_wall_plan(poly, cs, rayless, cfg))


def casting_corners(cs, rayless: set[int], short_walls: ShortWalls) -> list:
    """The corners that cast rays, in ring order: all but the rayless ones and the short walls' dropped corners,
    with each short wall's one ray in the place of the corner it replaces."""
    out = []
    for k, c in enumerate(cs):
        if k in short_walls.insert:  # a short wall's one ray (a chamfer's middle: between its two corners)
            out.append(short_walls.insert[k])
        if k not in rayless and k not in short_walls.drop:
            out.append(c)
    return out


def bay_caps(cs, bays, rayless: set[int], cfg: CornerRayConfig) -> list[Polygon]:
    """With ``bays_in_band``: every bay whose inner corners cast no ray, as the polygon between its opening and its
    walls. The band is offset from the footprint without them, so a bay holds no sliver of core."""
    if not cfg.bays_in_band:
        return []
    return [Polygon([cs[i][0] for i in (a, *inner, b)]) for a, inner, b in bays if set(inner) <= rayless]


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


def short_wall_plan(poly: Polygon, cs, rayless: set[int], cfg: CornerRayConfig) -> ShortWalls:
    """One ray per short wall (``short_wall_one_ray``), as :class:`ShortWalls`.

    A wall k-m between two neighbouring corners of a ring is short when shorter than ``short_wall_m``; it counts when
    both corners cast rays (neither is rayless: a wing-end or bay corner). A step (one reflex, one convex corner):
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
        return ShortWalls(drop, insert, records)
    short = cfg.short_wall_m
    oriented = orient(poly, sign=1.0)
    rings = [oriented.exterior, *oriented.interiors]
    ring_xy = [np.asarray(r.coords)[:-1] for r in rings]
    inside = poly.buffer(EPS)
    offset_core = poly.buffer(-cfg.band_m, join_style="mitre", mitre_limit=10.0) if cfg.short_wall_to_band \
        else Polygon()
    band_line = offset_core.boundary if not offset_core.is_empty else LineString()
    far = reach(poly)

    def band_corner(start: np.ndarray, src: np.ndarray, d: np.ndarray):
        """Where the ray from src along d first meets the band line, if before the facade and a straight line from
        start reaches it inside the footprint; else None."""
        if band_line.is_empty:
            return None
        s, kind = first_hit(src, d, [(poly.boundary, "facade"), (band_line, "band")], far)
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
            if length >= short or k in rayless or m in rayless:
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
        return ShortWalls(drop, insert, records)

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
    return ShortWalls(drop, insert, records)


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
            if max(angle_between(before, chord), angle_between(after, chord)) > cfg.bay_edge_deg:
                continue
            if not inside.covers(LineString([pa, pb])):
                continue
            out.append((a, tuple(ks[i] for i in run), b))
    return out
