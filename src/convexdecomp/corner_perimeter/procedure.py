"""The corner-ray perimeter of one footprint: the band, the corner rays, rules 1 to 10, and the zones.

:func:`corner_ray_perimeter` runs the whole procedure, or stops after one of :data:`STAGES`, and returns a
:class:`PerimeterResult` with the zones and every intermediate line and record that the figures and previews draw.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Point, Polygon
from shapely.ops import substring

from .core import align_legs, fill_notches, narrow_tips, refit, run_on_spines
from .geometry import EPS, Line, cut_at, first_hit, heading_of, reach, unit
from .rays import bay_runs, cast, close_pairs, corners, reattach, short_wall_plan, to_band, trim, wing_end_pairs
from .settings import CornerRayConfig
from .tidy import tidy_corners
from .zones import (PerimeterError, absorb_lone_tiny_zones, cut_sharp_corners, join_specks, merge_tiny_zones,
                    merge_vertex_groups, node_lines, split_narrow, zone_counts, zones_from)

#: the stages in the order they run; ``upto`` stops after one of them
STAGES = ("band", "rays", "merge_rays", "opposite", "discard", "narrow_core", "close_points", "diagonals",
          "corner_groups", "tiny_touching", "tiny_lone", "sharp", "specks", "tidy")
#: what each stage is, in words
STAGE_TITLES = {
    "band": "Band", "rays": "Corner rays (wing ends without)", "merge_rays": "Rule 1: rays that meet merge",
    "opposite": "Rule 2: close opposite rays become one line", "discard": "Rule 3: rays that reach nothing",
    "narrow_core": "Rule 4: narrow core becomes lines", "close_points": "Rule 5: close points become one",
    "diagonals": "Rule 6: narrow zones give way to a diagonal", "corner_groups": "Rule 5 again, longer reach",
    "tiny_touching": "Rule 7: touching tiny zones merge", "tiny_lone": "Rule 8: lone tiny zones merge",
    "sharp": "Rule 9: sharp corners are cut off", "specks": "Rule 10: specks join a neighbour",
    "tidy": "Tidy zone corners",
}
#: what the tidy steps record
TIDIED = ("snapped", "merged_again", "straightened", "collinear_dropped")
#: rules 9, 7, 8 and 10 repeat at most this often (``repeat_until_stable``)
MAX_ROUNDS = 8
#: what the wing-end check counts as bad: thin, non-convex and sharp zones and specks
BAD = ("thin", "not_convex", "sharp_20", "specks")


@dataclass
class PerimeterResult:
    """What the procedure made of one footprint.

    ``zones`` are ``(polygon, kind)`` pairs that tile the footprint: kind ``zone`` (a perimeter zone), ``thin`` (a
    perimeter zone narrower than ``min_zone_width_m``) or ``core``. ``lines`` are the corner rays and the lines the
    rules added (:class:`~.geometry.Line`), ``band_line`` the band line after rule 4 (core outline and the lines
    the narrow core became), ``core0``/``band0`` the core and band line as offset. ``corners`` are the corners that
    cast rays, as ``(point, bisector, interior angle, ring)``; ``wing_ends`` and ``bay_corners`` the corner points
    that did not.
    """

    poly: Polygon
    config: CornerRayConfig
    corners: list
    wing_ends: list[np.ndarray]
    core0: Any
    band0: Any
    core: Any
    band_line: Any
    lines: list[Line]
    pairs: list = field(default_factory=list)            # rule 2: (corner i, corner j, line, gap)
    discarded: list[int] = field(default_factory=list)   # rule 3: indices into corners
    legs: list[dict] = field(default_factory=list)       # rule 4
    notches: list[dict] = field(default_factory=list)    # rule 4
    narrow_parts: list[dict] = field(default_factory=list)  # rule 4: whole parts, tips and passages
    zones: list[tuple[Polygon, str]] = field(default_factory=list)
    points_merged: list[np.ndarray] = field(default_factory=list)  # rule 5
    diagonals: list[LineString] = field(default_factory=list)      # rule 6
    corner_groups: list[np.ndarray] = field(default_factory=list)  # rule 5 again
    tiny_merged: list[Polygon] = field(default_factory=list)       # rule 7
    lone_merged: list[tuple[Polygon, bool]] = field(default_factory=list)  # rule 8: (zone, after a relink)
    sharp_cuts: list[LineString] = field(default_factory=list)     # rule 9
    sharp_whole: list[Polygon] = field(default_factory=list)       # rule 9: triangles handed over whole
    specks_joined: list[Polygon] = field(default_factory=list)     # rule 10: the zones specks joined
    wing_ends_restored: list[np.ndarray] = field(default_factory=list)  # wing-end check: corners given rays back
    tidied: dict = field(default_factory=dict)  # tidy: snapped, merged_again, straightened, collinear_dropped
    rounds: int = 0                                                # rounds of rules 9, 7, 8 and 10
    upto: str | None = None
    short_walls: list[dict] = field(default_factory=list)          # short walls that cast one ray
    bay_corners: list[np.ndarray] = field(default_factory=list)    # bays: inner corners that cast no ray
    bays_restored: list[np.ndarray] = field(default_factory=list)  # bay check: bay corners given rays back

    @property
    def band(self):
        """The perimeter band: the footprint without the core."""
        return self.poly.difference(self.core)

    @property
    def perimeter_zones(self) -> list[Polygon]:
        return [z for z, k in self.zones if k != "core"]

    @property
    def core_parts(self) -> list[Polygon]:
        return [z for z, k in self.zones if k == "core"]

    def counts(self) -> dict[str, int]:
        """Perimeter zones, core parts, thin and non-convex zones, corners under 20° and specks (:func:`zone_counts`),
        and how often each step acted."""
        legs = [leg for leg in self.legs if not leg.get("sliver")]
        sw = self.short_walls
        dropped = sum(len(w["dropped"]) for w in sw)
        middles = sum(w["kind"] == "chamfer" and len(w["dropped"]) == 2 for w in sw)  # a middle casts instead
        return {**zone_counts(self.zones),
                "corners": len(self.corners) + len(self.wing_ends) + len(self.bay_corners) + dropped - middles,
                "wing_end_corners": len(self.wing_ends), "bay_corners": len(self.bay_corners),
                "short_walls": len(sw), "short_wall_steps": sum(w["kind"] == "step" for w in sw),
                "short_wall_chamfers": sum(w["kind"] == "chamfer" for w in sw), "short_wall_corners": dropped,
                "short_wall_to_band": sum(w["to_band"] for w in sw), "short_wall_teeth": sum(w["tooth"] for w in sw),
                "bay_corners_restored": len(self.bays_restored),
                "merged_rays": sum(ln.kind == "apex" for ln in self.lines), "corner_lines": len(self.pairs),
                "rays_discarded": len(self.discarded), "rays_cast_again": sum(ln.again for ln in self.lines),
                "legs": len(legs), "notches": len(self.notches),
                "narrow_parts": len(self.narrow_parts) + len(self.legs) - len(legs),
                "points_merged": len(self.points_merged), "diagonals": len(self.diagonals),
                "corner_groups": len(self.corner_groups), "tiny_merged": len(self.tiny_merged),
                "lone_merged": len(self.lone_merged), "lone_relinked": sum(r for _, r in self.lone_merged),
                "sharp_cuts": len(self.sharp_cuts), "sharp_whole": len(self.sharp_whole),
                "specks_joined": len(self.specks_joined), "wing_ends_restored": len(self.wing_ends_restored),
                **{k: len(self.tidied.get(k, [])) for k in TIDIED}}


def corner_ray_perimeter(poly: Polygon, config: CornerRayConfig | None = None, upto: str | None = None,
                         skip: tuple[str, ...] = ()) -> PerimeterResult:
    """The perimeter zones and core of a footprint (a valid polygon in metres, courtyards allowed), or the state
    after the stage ``upto`` (one of :data:`STAGES`), without the stages in ``skip``.

    With ``bays``, the inner corners of every bay (:func:`~.rays.bay_runs`) cast no ray either; a bay whose two
    corners are a wing end stays a wing end. With ``wing_end_check``, every wing end whose wing (the hull of its end
    wall and side walls) touches a thin zone of the whole procedure's result is tried again with its rays, one
    after another; with ``bay_check``, so is every bay while the result has any thin, non-convex or sharp zone or
    speck, and finally all bays together. A try is kept when the footprint's thin, non-convex and sharp zones and
    specks (:data:`BAD`) go down. A stage ``upto`` is then drawn with the wing ends and bays the whole procedure
    kept.

    Raises :class:`~.zones.PerimeterError` if the zones would not tile the footprint (never seen on the 990
    footprints of the test sample)."""
    cfg = config or CornerRayConfig()
    if upto is not None and upto not in STAGES:
        raise ValueError(f"upto must be one of {', '.join(STAGES)}, not {upto!r}")
    unknown = set(skip) - set(STAGES)
    if unknown:
        raise ValueError(f"unknown stages to skip: {', '.join(sorted(unknown))}")
    cs = corners(poly, cfg.min_turn_deg)
    pairs = wing_end_pairs(cs, cfg)
    # a bay whose two corners are a wing end stays a wing end; a wing end inside a longer bay is the bay's
    bays = [bay for bay in bay_runs(poly, cs, cfg) if not any(set(bay[1]) == {k, m} for _, k, m, _ in pairs)]
    bay_set = {i for _, inner, _ in bays for i in inner}
    pairs = [p for p in pairs if not {p[1], p[2]} <= bay_set]
    skip_corners = {i for _, k, m, _ in pairs for i in (k, m)} | bay_set
    # one ray per short wall, decided once on the corners that cast rays besides the wing ends and bays: a wing end
    # or bay given its rays back by a check casts all of them
    plan = short_wall_plan(poly, cs, skip_corners, cfg)
    # what the checks try: the corners given their rays back, and the region a thin zone must touch for the try
    # (a wing end's wing) or None (a bay: tried whenever the footprint keeps any thin, non-convex or sharp zone or
    # speck, since a bay's rays reach far)
    tries = [((k, m), MultiPoint([cs[i][0] for i in (h, k, m, n)]).convex_hull.buffer(0.1))
             for h, k, m, n in pairs] if cfg.wing_end_check else []
    tries += [(inner, None) for _, inner, _ in bays] if cfg.bay_check else []
    if not tries:
        return _perimeter(poly, cfg, upto, skip, cs, skip_corners, [], bays, plan)

    def bad(r: PerimeterResult) -> int:
        c = zone_counts(r.zones)
        return sum(c[k] for k in BAD)

    result = _perimeter(poly, cfg, None, skip, cs, skip_corners, [], bays, plan)
    restored: list[int] = []
    for give_back, region in tries:
        if region is not None and not any(kind == "thin" and z.intersects(region) for z, kind in result.zones):
            continue
        if region is None and bad(result) == 0:
            continue
        trial = _perimeter(poly, cfg, None, skip, cs, skip_corners - set(give_back), restored + list(give_back),
                           bays, plan)
        if bad(trial) < bad(result):
            result, skip_corners, restored = trial, skip_corners - set(give_back), restored + list(give_back)
    left = sorted(skip_corners & bay_set)
    if cfg.bay_check and left and len(bays) > 1 and bad(result) > 0:  # no bay one by one, but all bays together
        trial = _perimeter(poly, cfg, None, skip, cs, skip_corners - set(left), restored + left, bays, plan)
        if bad(trial) < bad(result):
            result, skip_corners, restored = trial, skip_corners - set(left), restored + left
    return result if upto is None else _perimeter(poly, cfg, upto, skip, cs, skip_corners, restored, bays, plan)


def _perimeter(poly: Polygon, cfg: CornerRayConfig, upto: str | None, skip: tuple[str, ...], cs: list,
               skip_corners: set[int], restored: list[int],
               bays: list[tuple[int, tuple[int, ...], int]] = (), plan=(set(), {}, [])) -> PerimeterResult:
    """The procedure with the corners skip_corners (indices into the corners cs) casting no ray; bays are the bays
    as (base, inner corners, base) (their inner corners among skip_corners are bay corners, the other corners of
    skip_corners wing-end corners)."""

    def on(stage: str) -> bool:
        return stage not in skip and (upto is None or STAGES.index(stage) <= STAGES.index(upto))

    # the corners that cast rays: all but the two corners of a narrow wing's end wall and the corners of a bay
    bay_set = {i for _, inner, _ in bays for i in inner}
    wing_ends = [cs[k][0] for k in sorted(skip_corners - bay_set)]
    bay_corners = [cs[k][0] for k in sorted(skip_corners & bay_set)]
    wing_ends_restored = [cs[k][0] for k in restored if k not in bay_set]
    bays_restored = [cs[k][0] for k in restored if k in bay_set]
    # with bays_in_band, the core is offset from the footprint with its bays cut off at their openings
    caps = [Polygon([cs[i][0] for i in (a, *inner, b)]) for a, inner, b in bays
            if cfg.bays_in_band and set(inner) <= skip_corners]
    drop, insert, short_walls = plan  # one ray per short wall: corners that cast none, rays cast in their place
    kept = []
    for k, c in enumerate(cs):
        if k in insert:  # a short wall's one ray, in the place of corner k (a chamfer's middle: between its corners)
            kept.append(insert[k])
        if k not in skip_corners and k not in drop:
            kept.append(c)
    cs = kept
    # the perimeter band: the footprint offset inwards, its inner boundary is the band line
    base = poly.difference(shapely.union_all(caps)) if caps else poly
    core0 = base.buffer(-cfg.band_m, join_style="mitre", mitre_limit=10.0)
    band0 = core0.boundary if not core0.is_empty else LineString()
    far = reach(poly)
    # corner rays, cut off where they reach the band line
    rays = [cast(poly, p, d) for p, d, _, _ in cs]
    rays, at_band = to_band(rays, band0)

    # rule 2 (close opposite rays become one line) is decided on the rays as cast: once rays are
    # cut against each other, the two rays of a pair no longer reach near each other's corner.
    # Its line is in place before the rays are cut, so the other rays stop on it.
    pairs = close_pairs(poly, cs, rays, cfg) if on("opposite") else []
    merged = [Line(seg, "merged", "corner") for _, _, seg, _ in pairs]
    keep = [k for k in range(len(rays)) if not any(k in (i, j) for i, j, _, _ in pairs)]
    on_merged = [False] * len(rays)
    if merged:
        wall = shapely.union_all([m.geom for m in merged])
        for k in keep:
            rays[k], on_merged[k] = cut_at(rays[k], wall)

    ring: dict[int, list[int]] = {}
    for k, c in enumerate(cs):
        ring.setdefault(c[3], []).append(k)
    nxt = {ks[t]: ks[(t + 1) % len(ks)] for ks in ring.values() for t in range(len(ks))}

    # rays stop on the first ray they meet (motorcycle rule), or on the band line or a rule 2 line.
    # rule 3: a ray that reaches the far facade without meeting anything is discarded, and the
    # others are cut again without it, until none reaches the facade. Kept: a ray the ray of a
    # neighbouring corner ends on, as the two close a triangle for rule 1
    discarded: list[int] = []
    protected: set[int] = set()
    while True:
        trimmed, cut = trim([cs[k] for k in keep], [rays[k] for k in keep], cfg.tie_m)
        stop = {k: "ray" if c or on_merged[k] else "band" if at_band[k] else "facade" for k, c in zip(keep, cut)}
        if not discarded:
            geom = dict(zip(keep, trimmed))
            for k in keep:
                for m in (nxt[k], *[j for j in keep if nxt[j] == k]):
                    if m != k and m in geom and Point(geom[k].coords[-1]).distance(geom[m]) < EPS:
                        protected |= {k, m}
        free = [k for k in keep if stop[k] == "facade" and k not in protected] if on("discard") else []
        if not free:
            break
        discarded += free
        keep = [k for k in keep if k not in free]
    lines = [Line(t, "ray", stop[k], cs[k][2] > 180.0) for k, t in zip(keep, trimmed)] + merged

    # rule 1: wherever two rays meet they merge into one. The ray that is hit is cut at the
    # meeting point and a new ray leaves it between the two directions, stopping on the first
    # line it meets. Meetings: a ray ending on another ray (or two ending at one point), and a
    # ray ending on its neighbouring corner's rule 2 line (which is not cut)
    line_of = {k: lines[n] for n, k in enumerate(keep)}
    heading = {k: cs[k][1] for k in keep}
    for (i, j, _, _), m in zip(pairs, merged):  # a corner-to-corner line is both corners' line
        line_of[i] = line_of[j] = m
        heading[i], heading[j] = unit(cs[j][0] - cs[i][0]), unit(cs[i][0] - cs[j][0])
    meets = []  # (when the ray ending there arrives, that ray, the line it ends on, that line's heading)
    for k in sorted(line_of):
        m = nxt[k]
        for p, q, hq in ((line_of.get(k), line_of.get(m), heading.get(m)),
                         (line_of.get(m), line_of.get(k), heading.get(k))):
            if (p is not None and q is not None and q.kind == "merged" and p.kind == "ray" and p.stop == "ray"
                    and Point(p.geom.coords[-1]).distance(q.geom) < EPS):
                meets.append((p.geom.length, p, q, hq))
    for p in lines if on("merge_rays") else []:
        if p.kind != "ray" or p.stop != "ray":
            continue
        x = Point(p.geom.coords[-1])
        for q in lines:
            if q is not p and q.kind == "ray" and x.distance(q.geom) < EPS and q.geom.project(x) > EPS:
                meets.append((p.geom.length, p, q, heading_of(q)))
                break
    merges: list[Line] = []
    for t, p, q, hq in sorted(meets, key=lambda mt: mt[0]) if on("merge_rays") else []:
        x = np.asarray(p.geom.coords[-1])
        if abs(p.geom.length - t) > EPS or Point(x).distance(q.geom) > EPS:
            continue  # an earlier merge cut one of them short of this point
        if any(np.linalg.norm(x - np.asarray(o.geom.coords[0])) < 1e-6 for o in merges):
            continue  # two rays ending at one point: already merged from the other side
        if q.kind == "ray" and q.geom.project(Point(x)) < q.geom.length - EPS:
            q.geom = substring(q.geom, 0.0, q.geom.project(Point(x)))
            q.stop = "ray"
        u = heading_of(p) + hq
        if np.linalg.norm(u) < 1e-6:
            continue
        u = unit(u)
        obstacles = [(poly.boundary, "facade"), (band0, "band")] + [(o.geom, "ray") for o in lines + merges]
        s_hit, kind = first_hit(x, u, obstacles, far)
        merges.append(Line(LineString([x, x + s_hit * u]), "apex", kind))
    lines += merges
    reattach(lines, poly, band0)
    recast = [k for k in discarded if cfg.recast_discarded or (cfg.keep_reflex_rays and cs[k][2] > 180.0)]
    discarded = [k for k in discarded if k not in recast]
    for k in recast:  # cast again, now that the merged rays are there: stop on the first line
        p, d = cs[k][0], cs[k][1]
        obstacles = [(poly.boundary, "facade"), (band0, "band")] + [(o.geom, "ray") for o in lines]
        s_hit, kind = first_hit(p, d, obstacles, rays[k].length)
        if s_hit >= rays[k].length - EPS:
            kind = "ray" if on_merged[k] else "band" if at_band[k] else "facade"
        lines.append(Line(LineString([p, p + s_hit * d]), "ray", kind, cs[k][2] > 180.0, again=True))

    # rule 4: narrow parts of the core (legs, notches, narrow parts) become lines
    narrow_core = on("narrow_core")
    core1, spines, moves, legs = align_legs(core0, cfg) if narrow_core and not core0.is_empty \
        else (core0, [], [], [])
    core1, notch_moves, notches = fill_notches(core1, cfg) if cfg.fill_notches and narrow_core \
        and not core1.is_empty else (core1, [], [])
    core1, tip_spines, tip_moves, tips = narrow_tips(core1, cfg) if narrow_core and not core1.is_empty \
        else (core1, [], [], [])
    if tip_moves:  # leg lines that ended on a corner of a part that became a line follow it
        spines = [LineString([next((new for old, new in tip_moves if np.linalg.norm(np.asarray(xy) - old) < 1e-6),
                                   np.asarray(xy)) for xy in sp.coords]) for sp in spines]
    narrow = [sp for sp, leg in zip(spines, legs) if leg.get("sliver")] + tip_spines  # lines of narrow core
    spines = spines + tip_spines
    moves = moves + notch_moves + tip_moves
    band1 = shapely.union_all([core1.boundary, *spines]) if not core1.is_empty or spines else LineString()
    refit(lines, poly, core1, band1, moves)
    reattach(lines, poly, band1)
    if narrow:  # only the lines of narrow core parts and tips run on, not those of legs
        legs_only = [sp for sp in spines if not any(sp is n for n in narrow)]
        ran = run_on_spines(narrow, lines + [Line(sp, "spine", "band") for sp in legs_only], poly, core1)
        spines = legs_only + ran

    # the zones: every point lying on a line is inserted into it, so each line end joins the line
    # it stops on
    core2, spines, lines, facade = node_lines(poly, core1, spines, lines)
    band2 = shapely.union_all([core2.boundary, *spines]) if not core2.is_empty or spines else LineString()
    width = cfg.min_zone_width_m
    zones = zones_from(poly, core2, [*facade, band2, *(ln.geom for ln in lines)], width)
    # rule 5: zone corners closer than close_points_m become one
    zones, points_merged = merge_vertex_groups(poly, zones, cfg.close_points_m, math.inf, width) \
        if on("close_points") else (zones, [])
    # rule 6: narrow four-cornered zones give way to their shorter diagonal
    zones, diagonals = split_narrow(zones, cfg) if on("diagonals") else (zones, [])
    # rule 5 again, with a longer reach: zone corners gathered in one place become one vertex
    zones, groups = merge_vertex_groups(poly, zones, cfg.corner_group_m, cfg.corner_group_span_m, width) \
        if on("corner_groups") else (zones, [])
    # rule 7: tiny zones that touch become one, convex zone
    zones, tiny_merged = merge_tiny_zones(poly, zones, cfg) if on("tiny_touching") else (zones, [])
    # rule 8: a lone tiny zone merges into a neighbour, after a relink if needed
    zones, lone_merged = absorb_lone_tiny_zones(poly, zones, cfg) if on("tiny_lone") else (zones, [])
    # rule 9: very sharp zone corners are cut off; then rules 7 and 8 once more, rule 10: specks join a
    # neighbour, and the zone corners are tidied (snapped onto the outline, close ones merged once more, near-straight
    # ones straightened, collinear ones dropped). With repeat_until_stable the round runs again until the zones no
    # longer change
    sharp_cuts: list[LineString] = []
    sharp_whole: list[Polygon] = []
    specks_joined: list[Polygon] = []
    tidied: dict = {k: [] for k in TIDIED}
    rounds = 0
    while (on("sharp") or on("specks")) and rounds < (MAX_ROUNDS if cfg.repeat_until_stable else 1):
        before = sorted(z.wkb for z, _ in zones)
        if on("sharp"):
            zones, cuts, whole = cut_sharp_corners(zones, cfg)
            sharp_cuts += cuts
            sharp_whole += whole
            if cfg.repeat_tiny_after_sharp:
                zones, more = merge_tiny_zones(poly, zones, cfg)
                tiny_merged += more
                zones, more = absorb_lone_tiny_zones(poly, zones, cfg)
                lone_merged += more
        if on("specks") and cfg.join_specks:
            zones, joined = join_specks(zones, cfg)
            specks_joined += joined
        if on("tidy"):
            zones, done = tidy_corners(poly, zones, cfg)
            for k, v in done.items():
                tidied[k] += v
        rounds += 1
        if sorted(z.wkb for z, _ in zones) == before:
            break
    if abs(sum(z.area for z, _ in zones) - poly.area) >= 1e-3 * poly.area:
        raise PerimeterError("the zones do not tile the footprint")
    return PerimeterResult(poly=poly, config=cfg, corners=cs, wing_ends=wing_ends, core0=core0, band0=band0,
                           core=core2, band_line=band2, lines=lines, pairs=pairs, discarded=discarded, legs=legs,
                           notches=notches, narrow_parts=tips, zones=zones, points_merged=points_merged,
                           diagonals=diagonals, corner_groups=groups, tiny_merged=tiny_merged,
                           lone_merged=lone_merged, sharp_cuts=sharp_cuts, sharp_whole=sharp_whole,
                           specks_joined=specks_joined, wing_ends_restored=wing_ends_restored, rounds=rounds,
                           upto=upto, tidied=tidied, bay_corners=bay_corners, bays_restored=bays_restored,
                           short_walls=short_walls)
