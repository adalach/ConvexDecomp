"""Rules 1 to 3, on the lines: rays that meet merge (rule 1), close opposite rays become one line from corner to
corner (rule 2), and rays that reach nothing are discarded or cast again (rule 3).

They run in the order the lines need: rule 2 is decided on the rays as cast, before the rays are cut against each
other (:func:`join_opposite_rays`); the rays are then cut where they reach another ray, and rule 3 discards the
rays that reach the far facade (:func:`stop_rays`); rule 1 merges the rays that meet (:func:`merge_meeting_rays`);
last, rule 3 casts the discarded rays of reflex corners again (:func:`cast_reflex_rays_again`). After rule 4, a merged
ray that runs along the band line, a facade or another line yields to it (:func:`end_merged_rays_along`).
"""

from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, substring

from .geometry import EPS, Line, cross, cut_at, first_hit, heading_of, reach, unit
from .rays import trim
from .settings import CornerRayConfig


def opposite_pairs(poly: Polygon, cs, rays: list[LineString], cfg: CornerRayConfig):
    """Rule 2, the pairs: two rays running in opposite directions (within ``opposite_angle_deg``) that each pass
    within ``opposite_gap_m`` of the other's corner, closest pairs first, each ray in one pair at most; as
    ``(i, j, line from corner to corner, gap)``."""
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


def join_opposite_rays(poly: Polygon, cs, rays: list[LineString], cfg: CornerRayConfig, active: bool = True):
    """Rule 2: the rays of every pair (:func:`opposite_pairs`) become one line from corner to corner, and the other
    rays are cut where they reach such a line. Returns the pairs, the corner-to-corner lines, the indices of the
    rays kept, the rays, and which of them now end on a corner-to-corner line."""
    pairs = opposite_pairs(poly, cs, rays, cfg) if active else []
    corner_lines = [Line(seg, "corner_line", "corner") for _, _, seg, _ in pairs]
    keep = [k for k in range(len(rays)) if not any(k in (i, j) for i, j, _, _ in pairs)]
    rays = list(rays)
    on_corner_line = [False] * len(rays)
    if corner_lines:
        wall = shapely.union_all([m.geom for m in corner_lines])
        for k in keep:
            rays[k], on_corner_line[k] = cut_at(rays[k], wall)
    return pairs, corner_lines, keep, rays, on_corner_line


def next_corners(cs) -> dict[int, int]:
    """For every corner (index into cs), the next corner on its ring."""
    ring: dict[int, list[int]] = {}
    for k, c in enumerate(cs):
        ring.setdefault(c[3], []).append(k)
    return {ks[t]: ks[(t + 1) % len(ks)] for ks in ring.values() for t in range(len(ks))}


def stop_rays(cs, rays: list[LineString], keep: list[int], on_corner_line: list[bool], at_band: list[bool],
              cfg: CornerRayConfig, discard: bool = True):
    """The rays stop on the first ray they meet (they grow at the same speed, two arriving within ``tie_m`` both
    stop), or on the band line or a corner-to-corner line. Rule 3: a ray that reaches the far facade without meeting
    anything is discarded, and the others are cut again without it, until none reaches the facade; kept is a ray the
    ray of a neighbouring corner ends on, as the two close a triangle for rule 1. Returns the rays as lines, the
    indices of the rays kept and those discarded."""
    nxt = next_corners(cs)
    keep = list(keep)
    discarded: list[int] = []
    protected: set[int] = set()
    while True:
        trimmed, cut = trim([cs[k] for k in keep], [rays[k] for k in keep], cfg.tie_m)
        stop = {k: "ray" if c or on_corner_line[k] else "band" if at_band[k] else "facade" for k, c in zip(keep, cut)}
        if not discarded:
            geom = dict(zip(keep, trimmed))
            for k in keep:
                for m in (nxt[k], *[j for j in keep if nxt[j] == k]):
                    if m != k and m in geom and Point(geom[k].coords[-1]).distance(geom[m]) < EPS:
                        protected |= {k, m}
        free = [k for k in keep if stop[k] == "facade" and k not in protected] if discard else []
        if not free:
            break
        discarded += free
        keep = [k for k in keep if k not in free]
    return [Line(t, "ray", stop[k], cs[k][2] > 180.0) for k, t in zip(keep, trimmed)], keep, discarded


def merge_meeting_rays(poly: Polygon, cs, lines: list[Line], keep: list[int], pairs, corner_lines: list[Line],
                       band_line, active: bool = True) -> list[Line]:
    """Rule 1: wherever two rays meet they merge into one. The ray that is hit is cut at the meeting point and a new
    ray leaves it between the two directions, stopping on the first line it meets. Meetings: a ray ending on another
    ray (or two ending at one point), and a ray ending on its neighbouring corner's corner-to-corner line (which is
    not cut). One pass, earliest meeting first: merged rays do not merge again. lines starts with the rays of keep,
    in that order; the rays that are hit are cut in place. Returns the merged rays."""
    if not active:
        return []
    nxt = next_corners(cs)
    far = reach(poly)
    line_of = {k: lines[n] for n, k in enumerate(keep)}
    heading = {k: cs[k][1] for k in keep}
    for (i, j, _, _), m in zip(pairs, corner_lines):  # a corner-to-corner line is both corners' line
        line_of[i] = line_of[j] = m
        heading[i], heading[j] = unit(cs[j][0] - cs[i][0]), unit(cs[i][0] - cs[j][0])
    meets = []  # (when the ray ending there arrives, that ray, the line it ends on, that line's heading)
    for k in sorted(line_of):
        m = nxt[k]
        for p, q, hq in ((line_of.get(k), line_of.get(m), heading.get(m)),
                         (line_of.get(m), line_of.get(k), heading.get(k))):
            if (p is not None and q is not None and q.kind == "corner_line" and p.kind == "ray"
                    and p.stop == "ray" and Point(p.geom.coords[-1]).distance(q.geom) < EPS):
                meets.append((p.geom.length, p, q, hq))
    for p in lines:
        if p.kind != "ray" or p.stop != "ray":
            continue
        x = Point(p.geom.coords[-1])
        for q in lines:
            if q is not p and q.kind == "ray" and x.distance(q.geom) < EPS and q.geom.project(x) > EPS:
                meets.append((p.geom.length, p, q, heading_of(q)))
                break
    merged: list[Line] = []
    for t, p, q, hq in sorted(meets, key=lambda mt: mt[0]):
        x = np.asarray(p.geom.coords[-1])
        if abs(p.geom.length - t) > EPS or Point(x).distance(q.geom) > EPS:
            continue  # an earlier merge cut one of them short of this point
        if any(np.linalg.norm(x - np.asarray(o.geom.coords[0])) < 1e-6 for o in merged):
            continue  # two rays ending at one point: already merged from the other side
        if q.kind == "ray" and q.geom.project(Point(x)) < q.geom.length - EPS:
            q.geom = substring(q.geom, 0.0, q.geom.project(Point(x)))
            q.stop = "ray"
        u = heading_of(p) + hq
        if np.linalg.norm(u) < 1e-6:
            continue
        u = unit(u)
        obstacles = [(poly.boundary, "facade"), (band_line, "band")] + [(o.geom, "ray") for o in lines + merged]
        s_hit, kind = first_hit(x, u, obstacles, far)
        merged.append(Line(LineString([x, x + s_hit * u]), "merged_ray", kind))
    return merged


#: a merged ray yields to a line it runs along for at least this many times the distance it keeps from it
#: (``merged_ray_band_m``, ``merged_ray_lines_m``), within ``PARALLEL_DEG`` of its direction
RUN_ALONG = 4.0
PARALLEL_DEG = 5.0


def _direction_at(geom, q: Point) -> np.ndarray | None:
    """The direction of geom's segment through q (a point on it)."""
    for part in shapely.get_parts(geom):
        c = np.asarray(part.coords)
        for a, b in zip(c[:-1], c[1:]):
            if np.linalg.norm(b - a) > EPS and LineString([a, b]).distance(q) < 1e-9:
                return unit(b - a)
    return None


def _first_run(g: LineString, other, tol: float) -> tuple[float, float] | None:
    """Where g first runs along other: within tol of it and within PARALLEL_DEG of its direction for at least
    RUN_ALONG times tol, as distances along g; None when it never does."""
    if tol <= 0.0 or other is None or other.is_empty:
        return None
    runs = []
    for r in shapely.get_parts(shapely.intersection(g, other.buffer(tol))):
        if r.geom_type != "LineString" or r.length < RUN_ALONG * tol:
            continue
        along = _direction_at(other, nearest_points(other, r.interpolate(0.5, normalized=True))[0])
        ray = unit(np.asarray(r.coords[-1]) - np.asarray(r.coords[0]))
        if along is not None and math.degrees(math.asin(min(1.0, abs(cross(along, ray))))) <= PARALLEL_DEG:
            runs.append(tuple(sorted((g.project(Point(r.coords[0])), g.project(Point(r.coords[-1]))))))
    return min(runs) if runs else None


def end_merged_rays_along(lines: list[Line], poly: Polygon, band_line, band_tol: float,
                          line_tol: float) -> list[tuple[LineString, str]]:
    """Rule 1, once the band line is final (after rule 4): where a merged ray runs along the band line (within
    ``band_tol``), a facade or another line (within ``line_tol``), within ``PARALLEL_DEG`` of its direction for at
    least ``RUN_ALONG`` times that distance, the line it runs along takes its place, the earliest such run first. The
    ray ends on that line at its point nearest to where the run starts; if the ray goes on past the run, its rest
    starts again from that line at its point nearest to where the run ends; every line that ended on the run ends on
    that line instead, at its point nearest to the old end. Two lines that close would otherwise leave a sliver
    between them. lines change in place, in their order. Returns the runs dropped, each with what took its place
    (``band``, ``facade``, or the other line's kind)."""
    dropped: list[tuple[LineString, str]] = []
    for ln in [ln for ln in lines if ln.kind == "merged_ray"]:
        if ln not in lines:
            continue
        g = ln.geom
        others = [(band_line, band_tol, "band"), (poly.boundary, line_tol, "facade")] + \
                 [(o.geom, line_tol, o.kind) for o in lines if o is not ln]
        found = [(run, other, kind) for other, tol, kind in others
                 if (run := _first_run(g, other, tol)) is not None]
        if not found:
            continue
        (s0, s1), other, kind = min(found, key=lambda f: f[0])

        def onto(q, other=other) -> np.ndarray:
            return np.asarray(nearest_points(other, q)[0].coords[0])

        run = substring(g, s0, s1)
        for o in lines:
            if o is not ln and o.geom is not other and Point(o.geom.coords[-1]).distance(run) < EPS:
                o.geom = LineString([*o.geom.coords[:-1], onto(Point(o.geom.coords[-1]))])
                o.stop = "band" if kind == "band" else "facade" if kind == "facade" else "ray"
        rest = None
        if s1 < g.length - EPS:
            rest = Line(LineString([onto(g.interpolate(s1)), *substring(g, s1, g.length).coords[1:]]), "merged_ray",
                        ln.stop)
        if s0 > EPS:
            ln.geom = LineString([*substring(g, 0.0, s0).coords[:-1], onto(g.interpolate(s0))])
            ln.stop = "band" if kind == "band" else "facade" if kind == "facade" else "ray"
            if rest is not None:
                lines.append(rest)
        elif rest is not None:
            ln.geom = rest.geom
        else:
            lines.remove(ln)
        dropped.append((run, kind))
    return dropped


def cast_reflex_rays_again(poly: Polygon, cs, rays: list[LineString], lines: list[Line], discarded: list[int],
                           on_corner_line: list[bool], at_band: list[bool], band_line, cfg: CornerRayConfig):
    """Rule 3, the second part: a reflex corner's ray that was discarded (with ``keep_reflex_rays``; every discarded
    ray with ``recast_discarded``) is cast again, now that the merged rays are there, and stops on the first line it
    meets: a zone can only be convex if a line leaves every reflex corner. Returns the rays cast again and the
    indices of the rays that stay discarded."""
    recast = [k for k in discarded if cfg.recast_discarded or (cfg.keep_reflex_rays and cs[k][2] > 180.0)]
    again: list[Line] = []
    for k in recast:
        p, d = cs[k][0], cs[k][1]
        obstacles = [(poly.boundary, "facade"), (band_line, "band")] + [(o.geom, "ray") for o in lines + again]
        s_hit, kind = first_hit(p, d, obstacles, rays[k].length)
        if s_hit >= rays[k].length - EPS:
            kind = "ray" if on_corner_line[k] else "band" if at_band[k] else "facade"
        again.append(Line(LineString([p, p + s_hit * d]), "ray", kind, cs[k][2] > 180.0, again=True))
    return again, [k for k in discarded if k not in recast]


def reattach(lines: list[Line], poly: Polygon, band) -> None:
    """A line left ending on nothing (the line it stopped on was cut back) runs on to the first
    line it meets."""
    far = reach(poly)
    for ln in lines:
        if ln.kind == "corner_line":
            continue
        a, b = np.asarray(ln.geom.coords[0]), np.asarray(ln.geom.coords[-1])
        end = Point(b)
        if (end.distance(poly.boundary) < EPS or (not band.is_empty and end.distance(band) < EPS)
                or any(end.distance(o.geom) < EPS for o in lines if o is not ln)):
            continue
        obstacles = [(poly.boundary, "facade"), (band, "band")] + [(o.geom, "ray") for o in lines if o is not ln]
        s, ln.stop = first_hit(b, unit(b - a), obstacles, far)
        ln.geom = LineString([a, b + s * unit(b - a)])
