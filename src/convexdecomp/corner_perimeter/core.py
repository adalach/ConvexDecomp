"""Rule 4, narrow core becomes lines: legs, notches and narrow parts of the core turn into lines of the band, and
the lines that ended on the core follow it.

In each case two sides of the core lie close together and nearly parallel. The shorter side moves onto the longer
one, so the line lies where the longer side was; only when the two are within ``similar_sides_ratio`` in length do
both move half way. Core thinner than ``narrow_core_m`` never vanishes: it becomes a line.

:func:`narrow_core_to_lines` runs the rule: legs (:func:`align_legs`), then notches (:func:`fill_notches`), then
narrow parts (:func:`narrow_tips`); the lines follow the moved core (:func:`refit`), and the line of a narrow part
runs on to the next line (:func:`run_on_spines`).
"""

from __future__ import annotations

import math

import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

from convexdecomp.core.scores import minimum_width

from .geometry import EPS, Line, clean_ring, cross, cut_at, first_hit, line_x, polygons, reach, unit
from .lines import reattach
from .settings import CornerRayConfig


def find_legs(c: np.ndarray, cfg: CornerRayConfig) -> list[tuple[float, int]]:
    """(width, k) of every leg: out along an edge, across a short tip from vertex k, back along a
    parallel or nearly parallel edge less than ``leg_width_m`` away."""
    out = []
    n = len(c)
    for k in range(n):
        b, p, q, e = (c[(k + t) % n] for t in range(-1, 3))
        side1, tip, side2 = p - b, q - p, e - q
        if side1 @ side2 >= 0:
            continue  # not a U-turn
        off = math.degrees(math.asin(min(1.0, abs(cross(unit(side1), unit(-side2))))))
        width = abs(cross(unit(unit(side1) - unit(side2)), tip))
        if off <= cfg.leg_angle_deg and width < cfg.leg_width_m:
            out.append((width, k))
    return out


def sliver_spine(part: Polygon, cfg: CornerRayConfig):
    """A core part thinner than ``narrow_core_m`` (hull width), without holes: the line it becomes
    (see narrow_line) and the moves of its corners onto that line."""
    if part.interiors or minimum_width(part) >= cfg.narrow_core_m:
        return None
    return narrow_line(part, [], cfg)


def axis_ends(part: Polygon, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Which of the vertices v lie at either end of the part's long axis (the long side of its
    smallest enclosing rectangle): within the part's width of that end."""
    rect = np.asarray(part.minimum_rotated_rectangle.exterior.coords)[:4]
    e0, e1 = rect[1] - rect[0], rect[2] - rect[1]
    axis = unit(e0) if np.linalg.norm(e0) >= np.linalg.norm(e1) else unit(e1)
    t = v @ axis
    width = minimum_width(part)
    return t <= t.min() + width, t >= t.max() - width


def midline(part: Polygon, start: np.ndarray | None = None):
    """The line between the middles of the two ends of a narrow polygon (along its long axis), or
    from `start` to the middle of the far end; and the moves of the polygon's corners onto it."""
    v = np.asarray(part.exterior.coords)[:-1]
    at_lo, at_hi = axis_ends(part, v)
    lo = v[at_lo].mean(axis=0)  # the middle of each end: a tip, or the middle of a blunt end
    hi = v[at_hi].mean(axis=0)
    if start is not None:
        lo, hi = start, (hi if np.linalg.norm(hi - start) >= np.linalg.norm(lo - start) else lo)
    if np.linalg.norm(hi - lo) < 1e-6:
        return None
    line = LineString([lo, hi])
    moves = [(p, np.asarray(line.interpolate(line.project(Point(p))).coords[0])) for p in v]
    return line, moves


def side_chains(n: int, end1: set[int], end2: set[int]) -> tuple[list[int], list[int]]:
    """The two paths around a ring of n corners between two ends (sets of adjacent corners)."""
    start = next(i for i in end1 if (i + 1) % n not in end1)
    c1, i = [start], (start + 1) % n
    while i not in end2:
        c1.append(i)
        i = (i + 1) % n
    c1.append(i)
    while (i + 1) % n in end2:
        i = (i + 1) % n
    c2, i = [i], (i + 1) % n
    while i not in end1:
        c2.append(i)
        i = (i + 1) % n
    c2.append(i)
    return c1, c2


def narrow_line(piece: Polygon, chords: list[tuple[np.ndarray, np.ndarray]], cfg: CornerRayConfig):
    """The line a narrow stretch of core becomes. Its two sides run between its two ends (a cut
    where it joins wider core, or a tip). The shorter side moves onto the longer one, so the line
    is the longer side; if the two are within ``similar_sides_ratio`` in length, the line runs half way
    between them, unless that straight line would leave the stretch (a passage that bends), when it is the longer
    side too. Returns the line and the moves of the stretch's corners onto it."""
    v = np.asarray(orient(piece, 1.0).exterior.coords)[:-1]
    n = len(v)

    def at(p):
        return int(np.argmin(np.linalg.norm(v - p, axis=1)))

    if len(chords) == 2:  # a passage between two cuts
        ends = [{at(a), at(b)} for a, b in chords]
        mid = LineString([(a + b) / 2 for a, b in chords])
    elif len(chords) == 1:  # a tip: from the cut to the far end
        a, b = chords[0]
        tip = int(np.argmax(np.linalg.norm(v - (a + b) / 2, axis=1)))
        ends = [{at(a), at(b)}, {tip}]
        got = midline(piece, start=(a + b) / 2)
        mid = got[0] if got else None
    else:  # a whole part: between the two ends of its long axis
        lo, hi = (set(np.nonzero(at)[0].tolist()) for at in axis_ends(piece, v))
        if lo & hi:
            return None
        ends = [lo, hi]
        got = midline(piece)
        mid = got[0] if got else None
    if mid is None or any(not e for e in ends) or ends[0] & ends[1]:
        return None
    try:
        c1, c2 = side_chains(n, ends[0], ends[1])
    except StopIteration:
        return None
    lengths = [LineString(v[c]).length if len(c) > 1 else 0.0 for c in (c1, c2)]
    longer = c1 if lengths[0] >= lengths[1] else c2
    if min(lengths) >= cfg.similar_sides_ratio * max(lengths) and piece.buffer(0.05).covers(mid):
        line = mid
    else:  # a passage that bends: its straight middle would leave it, so the longer side is the line
        line = LineString(v[longer])
    moves = [(p, np.asarray(line.interpolate(line.project(Point(p))).coords[0])) for p in v
             if line.distance(Point(p)) > 1e-9]
    return line, moves


def narrow_tips(core, cfg: CornerRayConfig):
    """Rule 4, narrow parts of the core: a whole core part thinner than ``narrow_core_m``, or the
    stretch where a part narrows below it (a tapered tip, a passage between two wider parts, or a narrow stretch
    of a ring around a courtyard), cut off straight where the part reaches that width, becomes a line (see
    narrow_line). The
    narrow stretches are found as what an opening of the part by half that width removes (pieces
    under 0.3 m² are rounding crumbs). Returns the new core, the lines, the vertex moves
    (old -> new) and the narrow parts."""
    parts, spines, moves, tips = [], [], [], []
    for part in polygons(core):
        if cfg.collapse_narrow_parts and not part.interiors and part.buffer(-cfg.narrow_core_m / 2).is_empty:
            # thinner than narrow_core_m everywhere (also when a leg or a notch made it so)
            got = narrow_line(part, [], cfg)
            if got is not None:
                line, part_moves = got
                spines.append(line)
                moves += part_moves
                tips.append(dict(kind="whole", area_m2=part.area, length_m=line.length, line=line))
                continue
        wide = part.buffer(-cfg.narrow_core_m / 2).buffer(cfg.narrow_core_m / 2)
        bodies = [part]
        narrow_area = part.difference(wide).buffer(-1e-3, join_style="mitre").buffer(1e-3, join_style="mitre")
        for q in sorted(polygons(narrow_area), key=lambda q: -q.area):  # hair-thin rounding crumbs dropped
            if q.area < 0.3:
                continue
            # the necks: the stretches of the piece's boundary inside the part (not on its outline)
            neck = shapely.difference(q.boundary, part.boundary.buffer(1e-6))
            neck = [g for g in getattr(neck, "geoms", [neck]) if g.geom_type == "LineString" and g.length > 1e-4]
            if not neck:
                continue
            necks = shapely.line_merge(shapely.union_all(neck))
            necks = [g for g in getattr(necks, "geoms", [necks]) if g.length > 0.05]
            if len(necks) not in (1, 2):
                continue  # a junction of narrow stretches: left as it is
            chords = [(np.asarray(g.coords[0]), np.asarray(g.coords[-1])) for g in necks]
            host = max(range(len(bodies)), key=lambda i: shapely.intersection(bodies[i], q).area)
            cuts = [LineString([a - 0.01 * unit(b - a), b + 0.01 * unit(b - a)]) for a, b in chords]
            # all cuts at once: across a ring around a courtyard, one cut alone divides nothing
            pieces = polygons(shapely.ops.split(bodies[host], shapely.MultiLineString(cuts)))
            ring = bool(bodies[host].interiors) and len(chords) == 2
            if len(pieces) != (2 if ring else len(chords) + 1):
                continue
            narrow = max(pieces, key=lambda pc: shapely.intersection(pc, q).area)
            rest = [pc for pc in pieces if pc is not narrow]
            if any(minimum_width(pc) < cfg.narrow_core_m for pc in rest):
                continue
            got = narrow_line(narrow, chords, cfg)
            if got is not None and not narrow.buffer(0.05).covers(got[0]):
                got = None
            if got is None:
                continue
            line, narrow_moves = got
            ends = [p for ab in chords for p in ab]
            spines.append(line)
            moves += [(p, m) for p, m in narrow_moves if min(np.linalg.norm(p - e) for e in ends) > 1e-6]
            tips.append(dict(kind="tip" if len(chords) == 1 else "passage", area_m2=narrow.area,
                             length_m=line.length, line=line))
            bodies = [bd for i, bd in enumerate(bodies) if i != host] + rest
        parts += bodies
    return shapely.union_all(parts) if parts else Polygon(), spines, moves, tips


def run_on_spines(spines: list[LineString], lines: list[Line], poly: Polygon, core) -> list[LineString]:
    """An end of a narrow part's line that touches nothing (no line, core or other such line) runs
    on along it to the first line it meets, so that the line splits the zone around it."""
    far = reach(poly)
    out = list(spines)
    for k, sp in enumerate(out):
        c = [np.asarray(xy) for xy in sp.coords]
        for end, inner in ((0, 1), (-1, -2)):
            p = c[end]
            others = [ln.geom for ln in lines] + [o for j, o in enumerate(out) if j != k]
            if not core.is_empty:
                others.append(core.boundary)
            if any(Point(p).distance(o) < 1e-6 for o in others) or poly.boundary.distance(Point(p)) < 1e-6:
                continue
            d = unit(p - c[inner])
            obstacles = [(poly.boundary, "facade")] + [(o, "line") for o in others]
            s_hit, _ = first_hit(p, d, obstacles, far)
            c[end] = p + s_hit * d
        out[k] = LineString(c)
    return out


def align_legs(core, cfg: CornerRayConfig):
    """Rule 4 on every leg the core has (narrowest first, one pass): the shorter side moves onto
    the line of the longer one, or, if their lengths are within ``similar_sides_ratio``, both move onto the
    line half way between them. The leg becomes that line, its spine; the core keeps the part of
    the line next to it as its new edge. A whole core part thinner than ``narrow_core_m`` becomes a
    line at once (sliver_spine). Returns the new core, the spines, the vertex moves (old -> new)
    and the legs."""
    parts, spines, moves, legs = [], [], [], []
    for part in polygons(core):
        sliver = sliver_spine(part, cfg) if cfg.collapse_narrow_parts else None
        if sliver is not None:  # the whole part is thinner than narrow_core_m: it becomes a line
            spine, part_moves = sliver
            spines.append(spine)
            moves += part_moves
            legs.append(dict(width_m=minimum_width(part), sides_m=(spine.length, spine.length), sliver=True))
            continue
        c = clean_ring(np.asarray(orient(part, 1.0).exterior.coords)[:-1])
        n = len(c)
        pts = {i: c[i] for i in range(n)}
        ring = list(range(n))
        for width, k in sorted(find_legs(c, cfg)) if n >= 6 else []:
            legs_ids = [(k + t) % n for t in range(-1, 3)]  # b, p, q, e
            if any(i not in ring for i in legs_ids) or len(ring) < 6:
                continue
            at = ring.index(legs_ids[0])
            if [ring[(at + t) % len(ring)] for t in range(4)] != legs_ids:
                continue  # an earlier leg moved one of its corners
            # the neighbours only give the lines the leg's roots slide along; an earlier leg may
            # have moved them along those lines
            ids = [ring[(at - 1) % len(ring)], *legs_ids, ring[(at + 4) % len(ring)]]  # a, b, p, q, e, f
            a, b, p, q, e, f = (pts[i] for i in ids)
            mid = (p + q) / 2
            l1, l2 = np.linalg.norm(p - b), np.linalg.norm(e - q)
            if min(l1, l2) >= cfg.similar_sides_ratio * max(l1, l2):  # similar sides: the line half way between
                origin, u = mid, unit(unit(p - b) - unit(e - q))
            elif l1 > l2:  # the shorter side moves onto the longer side's line
                origin, u = p, unit(p - b)
            else:
                origin, u = q, unit(q - e)
            tip = origin + ((mid - origin) @ u) * u  # the middle of the tip, on that line
            x1, x2 = line_x(origin, u, a, b), line_x(origin, u, e, f)
            if x1 is None or x2 is None:
                continue
            nb, ne = len(pts), len(pts) + 1
            trial = [i for i in ring if i not in ids[1:5]]
            trial.insert(trial.index(ids[0]) + 1, nb)
            trial.insert(trial.index(nb) + 1, ne)
            trial_pts = {**pts, nb: x1, ne: x2}
            poly = Polygon([trial_pts[i] for i in trial])
            if len(trial) < 3 or not poly.is_valid or poly.area <= 0:
                continue
            ring, pts = trial, trial_pts
            moves += [(b, x1), (p, tip), (q, tip), (e, x2)]
            near = max((x1, x2), key=lambda x: (x - tip) @ u)  # the root nearer the tip
            if (near - tip) @ u < -EPS:
                spines.append(LineString([near, tip]))
            legs.append(dict(width_m=width, sides_m=(float(np.linalg.norm(p - b)), float(np.linalg.norm(e - q)))))
        parts.append(Polygon([pts[i] for i in ring], [h.coords for h in part.interiors]))
    return shapely.union_all(parts), spines, moves, legs


def core_thickness(core, p: np.ndarray, q: np.ndarray, samples: int = 9) -> float:
    """The core's thickness across the edge p-q (the core on its left): the shortest distance
    from points along the edge to the core's boundary straight across."""
    u = unit(q - p)
    n = np.array([-u[1], u[0]])  # left of the edge: inside the core (counter-clockwise ring)
    far = reach(core)
    best = math.inf
    for t in np.linspace(0.05, 0.95, samples):
        x = p + t * (q - p)
        s, _ = first_hit(x + 1e-7 * n, n, [(core.boundary, "core")], far)
        best = min(best, s)
    return best


def fill_notches(core, cfg: CornerRayConfig):
    """Rule 4, notches: a notch is a stretch of one side of the core (the floor p-q)
    stepped into the core between two jogs b-p and q-e, while the side runs on along one line
    before and after it (a-b and e-f). Where the core is thinner than ``notch_passage_m`` across the
    floor, the floor moves onto the line of a-b and e-f: the notch fills. Narrowest first, one
    pass. Returns the new core, the vertex moves (old -> new) and the notches."""
    parts, moves, notches = [], [], []
    for part in polygons(core):
        c = clean_ring(np.asarray(orient(part, 1.0).exterior.coords)[:-1])
        found = []
        n = len(c)
        for k in range(n) if n >= 6 else []:
            a, b, p, q, e, f = (c[(k + t) % n] for t in range(6))
            side1, floor, side2 = unit(b - a), unit(q - p), unit(f - e)
            off = max(math.degrees(math.asin(min(1.0, abs(cross(side1, floor))))),
                      math.degrees(math.asin(min(1.0, abs(cross(side2, floor))))))
            if off > cfg.leg_angle_deg or side1 @ floor <= 0 or side2 @ floor <= 0:
                continue  # the side does not run on in the same direction
            depth_p, depth_q = cross(side1, p - b), cross(side1, q - b)
            if min(depth_p, depth_q) <= 1e-6 or abs(cross(side1, e - b)) > 0.1 * min(depth_p, depth_q):
                continue  # not stepped into the core, or the side does not resume on one line
            thick = core_thickness(part, p, q)
            if thick < cfg.notch_passage_m:
                found.append((thick, k))
        ring = list(range(n))
        pts = {i: c[i] for i in range(n)}
        for thick, k in sorted(found):
            ids = [(k + t) % n for t in range(6)]
            if any(i not in ring for i in ids[2:4]):
                continue
            a, b, p, q = (pts[i] for i in ids[:4])
            u = unit(b - a)
            p2, q2 = b + ((p - b) @ u) * u, b + ((q - b) @ u) * u  # onto the line of a-b
            trial_pts = {**pts, ids[2]: p2, ids[3]: q2}
            trial = Polygon([trial_pts[i] for i in ring], [h.coords for h in part.interiors])
            if not trial.is_valid or trial.area <= part.area:
                continue
            pts = trial_pts
            moves += [(p, p2), (q, q2)]
            notches.append(dict(thickness_m=thick, depth_m=float(cross(u, p - b)),
                                length_m=float(np.linalg.norm(q - p)), floor=LineString([p, q])))
        parts.append(Polygon(clean_ring(np.array([pts[i] for i in ring])), [h.coords for h in part.interiors]))
    return shapely.union_all(parts), moves, notches


def refit(lines: list[Line], poly: Polygon, core, band, moves) -> None:
    """After rule 4: lines that ended on a moved core corner follow it; lines into the new core
    are cut back to it; lines that ended on the band line where it moved away run on to the
    first line they meet."""
    far = reach(poly)
    for ln in lines:
        coords = [np.asarray(xy) for xy in ln.geom.coords]
        for old, new in moves:
            if np.linalg.norm(coords[-1] - old) < EPS:
                coords[-1] = new
        ln.geom = LineString(coords)
    for ln in lines:
        g = ln.geom
        a, b = np.asarray(g.coords[0]), np.asarray(g.coords[-1])
        if shapely.intersection(g, core).length > EPS:
            ln.geom, _ = cut_at(g, core.boundary)
            ln.stop = "band"
        elif ln.stop == "band" and Point(b).distance(band) > EPS:
            obstacles = [(poly.boundary, "facade"), (band, "band")] + [(o.geom, "ray") for o in lines if o is not ln]
            s, ln.stop = first_hit(b, unit(b - a), obstacles, far)
            ln.geom = LineString([a, b + s * unit(b - a)])


def narrow_core_to_lines(offset_core, lines: list[Line], poly: Polygon, cfg: CornerRayConfig, active: bool = True):
    """Rule 4 on the core as offset: legs, notches (with ``fill_notches``) and narrow parts become lines of the band;
    the lines that ended on a moved core corner follow it, lines into the new core are cut back to it, lines left
    ending on nothing run on, and the line of a narrow part or tip (not of a leg) runs on along it to the next line.
    lines change in place. Returns the core, the lines the narrow core became (spines), and the records of the legs,
    notches and narrow parts."""
    has_core = not offset_core.is_empty
    core, spines, moves, legs = align_legs(offset_core, cfg) if active and has_core else (offset_core, [], [], [])
    core, notch_moves, notches = fill_notches(core, cfg) if cfg.fill_notches and active and not core.is_empty \
        else (core, [], [])
    core, tip_spines, tip_moves, tips = narrow_tips(core, cfg) if active and not core.is_empty else (core, [], [], [])
    if tip_moves:  # leg lines that ended on a corner of a part that became a line follow it
        spines = [LineString([next((new for old, new in tip_moves if np.linalg.norm(np.asarray(xy) - old) < 1e-6),
                                   np.asarray(xy)) for xy in sp.coords]) for sp in spines]
    narrow = [sp for sp, leg in zip(spines, legs) if leg.get("sliver")] + tip_spines  # lines of narrow core
    spines = spines + tip_spines
    moves = moves + notch_moves + tip_moves
    band_line = shapely.union_all([core.boundary, *spines]) if not core.is_empty or spines else LineString()
    refit(lines, poly, core, band_line, moves)
    reattach(lines, poly, band_line)
    if narrow:  # only the lines of narrow core parts and tips run on, not those of legs
        legs_only = [sp for sp in spines if not any(sp is n for n in narrow)]
        ran = run_on_spines(narrow, lines + [Line(sp, "spine", "band") for sp in legs_only], poly, core)
        spines = legs_only + ran
    return core, spines, legs, notches, tips

