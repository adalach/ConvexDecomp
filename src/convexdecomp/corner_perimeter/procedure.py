"""The corner-ray perimeter of one footprint: which corners cast rays, the band and the corner rays, rules 1 to 10,
the tidy step, and the zones.

:func:`corner_ray_perimeter` runs the whole procedure, or stops after one of :data:`STAGES`, and returns a
:class:`PerimeterResult` with the zones and every intermediate line and record that the figures and previews draw.
:data:`STEPS` lists the steps with their rule numbers and the functions that do them; :func:`_perimeter` calls them
in the order they run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import shapely
from shapely.geometry import LineString, MultiPoint, Polygon

from .core import narrow_core_to_lines
from .geometry import Line
from .lines import (cast_reflex_rays_again, end_merged_rays_on_band, join_opposite_rays, merge_meeting_rays, reattach,
                    stop_rays)
from .rays import cast, corners, offset_band, to_band
from .settings import CornerRayConfig
from .sources import RaySources, bay_caps, casting_corners, ray_sources
from .tidy import tidy_corners
from .zones import (PerimeterError, cut_sharp_corners, form_zones, join_specks, merge_close_points,
                    merge_lone_thin_zones, merge_touching_thin_zones, narrow_arms, narrow_zones_to_diagonals,
                    zone_counts)


@dataclass(frozen=True)
class Step:
    """One step of the procedure: its stage name (for ``upto`` and ``skip``), its rule number (empty for the steps
    without one), what it does, and where it is done."""

    key: str
    rule: str
    title: str
    code: str


#: the steps in the order of their rule numbers (the order ``upto`` counts in); :func:`_perimeter` decides rule 2
#: before rule 1 and splits rule 3 around it, since rule 2 needs the rays as cast and rule 1 the rays as cut
STEPS = (
    Step("band", "", "Band", "rays.offset_band"),
    Step("rays", "", "Corner rays (wing ends, bays and short walls decide which corners cast)",
         "sources.ray_sources, rays.cast, rays.to_band"),
    Step("merge_rays", "1", "Rays that meet merge", "lines.merge_meeting_rays"),
    Step("opposite", "2", "Close opposite rays become one line", "lines.join_opposite_rays"),
    Step("discard", "3", "Rays that reach nothing", "lines.stop_rays, lines.cast_reflex_rays_again"),
    Step("narrow_core", "4", "Narrow core becomes lines", "core.narrow_core_to_lines"),
    Step("close_points", "5", "Close points become one", "zones.merge_close_points"),
    Step("diagonals", "6", "Narrow zones give way to a diagonal", "zones.narrow_zones_to_diagonals"),
    Step("corner_groups", "5 again", "Close points, longer reach", "zones.merge_close_points"),
    Step("thin_touching", "7", "Touching thin zones merge", "zones.merge_touching_thin_zones"),
    Step("thin_lone", "8", "Lone thin zones merge", "zones.merge_lone_thin_zones"),
    Step("sharp", "9", "Sharp corners are cut off", "zones.cut_sharp_corners"),
    Step("specks", "10", "Specks join a neighbour", "zones.join_specks"),
    Step("tidy", "", "Tidy zone corners", "tidy.tidy_corners"),
)
#: the stages, as ``upto`` and ``skip`` take them
STAGES = tuple(s.key for s in STEPS)
#: what each stage is, in words
STAGE_TITLES = {s.key: f"Rule {s.rule}: {s.title[0].lower()}{s.title[1:]}" if s.rule else s.title for s in STEPS}
#: what the tidy step records
TIDIED = ("snapped", "merged_again", "straightened", "collinear_dropped")
#: rules 9, 7, 8, 10 and the tidy step repeat at most this often (``repeat_until_stable``)
MAX_ROUNDS = 8
#: the zones must tile the footprint: no gap and no overlap larger than this share of its area
TILE_TOL = 1e-6
#: what the wing-end and bay checks count as bad: thin, non-convex and sharp zones and specks
BAD = ("thin", "not_convex", "sharp_20", "specks")


@dataclass
class PerimeterResult:
    """What the procedure made of one footprint.

    ``zones`` are ``(polygon, kind)`` pairs that tile the footprint: kind ``zone`` (a perimeter zone), ``thin`` (a
    perimeter zone narrower than ``min_zone_width_m``) or ``core``. The other fields are what each step made, in the
    order the steps run.
    """

    poly: Polygon
    config: CornerRayConfig
    upto: str | None = None
    zones: list[tuple[Polygon, str]] = field(default_factory=list)
    # which corners cast rays
    corners: list = field(default_factory=list)          # (point, direction, interior angle, ring) of each ray
    wing_ends: list[np.ndarray] = field(default_factory=list)          # wing-end corners without rays
    wing_ends_restored: list[np.ndarray] = field(default_factory=list)  # given their rays back by the check
    bay_corners: list[np.ndarray] = field(default_factory=list)        # bay corners without rays
    bays_restored: list[np.ndarray] = field(default_factory=list)      # given their rays back by the check
    short_walls: list[dict] = field(default_factory=list)              # short walls that cast one ray
    # the band, the rays and rules 1 to 3
    offset_core: Any = None                              # the core as offset
    offset_band_line: Any = None                         # its boundary
    lines: list[Line] = field(default_factory=list)      # rays, merged rays and corner-to-corner lines
    opposite_pairs: list = field(default_factory=list)   # rule 2: (corner i, corner j, line, gap)
    rays_to_band: list[LineString] = field(default_factory=list)  # rule 1: merged rays' parts along the band line
    discarded: list[int] = field(default_factory=list)   # rule 3: indices into corners
    # rule 4
    legs: list[dict] = field(default_factory=list)
    notches: list[dict] = field(default_factory=list)
    narrow_parts: list[dict] = field(default_factory=list)  # whole parts, tips and passages
    core: Any = None                                     # the core after rule 4
    band_line: Any = None                                # core outline and the lines the narrow core became
    # rules 5 to 10 and the tidy step
    points_merged: list[np.ndarray] = field(default_factory=list)   # rule 5
    diagonals: list[LineString] = field(default_factory=list)       # rule 6
    corner_groups: list[np.ndarray] = field(default_factory=list)   # rule 5 again
    thin_merged: list[Polygon] = field(default_factory=list)        # rule 7
    lone_merged: list[tuple[Polygon, bool]] = field(default_factory=list)  # rule 8: (zone, after a relink)
    sharp_cuts: list[LineString] = field(default_factory=list)      # rule 9
    sharp_whole: list[Polygon] = field(default_factory=list)        # rule 9: triangles handed over whole
    specks_joined: list[Polygon] = field(default_factory=list)      # rule 10: the zones specks joined
    tidied: dict = field(default_factory=dict)  # tidy: snapped, merged_again, straightened, collinear_dropped
    rounds: int = 0                             # rounds of rules 9, 7, 8, 10 and the tidy step

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
                "wing_end_corners": len(self.wing_ends), "wing_ends_restored": len(self.wing_ends_restored),
                "bay_corners": len(self.bay_corners), "bay_corners_restored": len(self.bays_restored),
                "short_walls": len(sw), "short_wall_steps": sum(w["kind"] == "step" for w in sw),
                "short_wall_chamfers": sum(w["kind"] == "chamfer" for w in sw), "short_wall_corners": dropped,
                "short_wall_to_band": sum(w["to_band"] for w in sw), "short_wall_teeth": sum(w["tooth"] for w in sw),
                "merged_rays": sum(ln.kind == "merged_ray" for ln in self.lines),
                "merged_rays_to_band": len(self.rays_to_band),
                "corner_lines": len(self.opposite_pairs),
                "rays_discarded": len(self.discarded), "rays_cast_again": sum(ln.again for ln in self.lines),
                "legs": len(legs), "notches": len(self.notches),
                "narrow_parts": len(self.narrow_parts) + len(self.legs) - len(legs),
                "points_merged": len(self.points_merged), "diagonals": len(self.diagonals),
                "corner_groups": len(self.corner_groups), "thin_merged": len(self.thin_merged),
                "lone_merged": len(self.lone_merged), "lone_relinked": sum(r for _, r in self.lone_merged),
                "sharp_cuts": len(self.sharp_cuts), "sharp_whole": len(self.sharp_whole),
                "specks_joined": len(self.specks_joined), "narrow_arms": len(narrow_arms(self.zones)),
                **{k: len(self.tidied.get(k, [])) for k in TIDIED}}


def corner_ray_perimeter(poly: Polygon, config: CornerRayConfig | None = None, upto: str | None = None,
                         skip: tuple[str, ...] = ()) -> PerimeterResult:
    """The perimeter zones and core of a footprint (a valid polygon in metres, courtyards allowed), or the state
    after the stage ``upto`` (one of :data:`STAGES`), without the stages in ``skip``.

    The wing ends, bays and short walls decide which corners cast rays (:func:`~.sources.ray_sources`). With
    ``wing_end_check`` and ``bay_check``, the whole procedure first runs and then tries the wing ends and bays again
    with their rays (:func:`_check_ray_sources`); a stage ``upto`` is drawn with the wing ends and bays it kept.

    Raises :class:`~.zones.PerimeterError` if the zones would not tile the footprint (never seen on the 990
    footprints of the test sample)."""
    cfg = config or CornerRayConfig()
    if upto is not None and upto not in STAGES:
        raise ValueError(f"upto must be one of {', '.join(STAGES)}, not {upto!r}")
    unknown = set(skip) - set(STAGES)
    if unknown:
        raise ValueError(f"unknown stages to skip: {', '.join(sorted(unknown))}")
    cs = corners(poly, cfg.min_turn_deg)
    sources = ray_sources(poly, cs, cfg)
    rayless, restored, checked = _check_ray_sources(poly, cfg, skip, cs, sources)
    if checked is not None and upto is None:
        return checked
    return _perimeter(poly, cfg, upto, skip, cs, sources, rayless, restored)


def _check_ray_sources(poly: Polygon, cfg: CornerRayConfig, skip: tuple[str, ...], cs: list, sources: RaySources):
    """The wing-end and bay checks: every wing end whose wing (the hull of its end wall and side walls) touches a
    thin zone of the whole procedure's result (``wing_end_check``), and every bay while the result keeps a thin,
    non-convex or sharp zone or a speck (``bay_check``), is tried again with its rays, one after another; with more
    than one bay left, finally all bays together. A try is kept when the footprint's thin, non-convex and sharp
    zones and specks (:data:`BAD`) go down. Returns the corners that cast no ray, those given their rays back, and
    the whole procedure's result with them (None when there is nothing to try)."""
    tries = [((k, m), MultiPoint([cs[i][0] for i in (h, k, m, n)]).convex_hull.buffer(0.1))
             for h, k, m, n in sources.wing_ends] if cfg.wing_end_check else []
    tries += [(inner, None) for _, inner, _ in sources.bays] if cfg.bay_check else []
    rayless, restored = set(sources.rayless), []
    if not tries:
        return rayless, restored, None

    def bad(r: PerimeterResult) -> int:
        c = zone_counts(r.zones)
        return sum(c[k] for k in BAD)

    result = _perimeter(poly, cfg, None, skip, cs, sources, rayless, restored)
    for give_back, wing in tries:
        if wing is not None and not any(kind == "thin" and z.intersects(wing) for z, kind in result.zones):
            continue
        if wing is None and bad(result) == 0:
            continue
        trial = _perimeter(poly, cfg, None, skip, cs, sources, rayless - set(give_back), restored + list(give_back))
        if bad(trial) < bad(result):
            result, rayless, restored = trial, rayless - set(give_back), restored + list(give_back)
    bay_set = {i for _, inner, _ in sources.bays for i in inner}
    left = sorted(rayless & bay_set)
    if cfg.bay_check and left and len(sources.bays) > 1 and bad(result) > 0:
        trial = _perimeter(poly, cfg, None, skip, cs, sources, rayless - set(left), restored + left)
        if bad(trial) < bad(result):
            result, rayless, restored = trial, rayless - set(left), restored + left
    return rayless, restored, result


def _perimeter(poly: Polygon, cfg: CornerRayConfig, upto: str | None, skip: tuple[str, ...], cs: list,
               sources: RaySources, rayless: set[int], restored: list[int]) -> PerimeterResult:
    """The procedure, with the corners rayless (indices into the corners cs) casting no ray."""

    def on(stage: str) -> bool:
        return stage not in skip and (upto is None or STAGES.index(stage) <= STAGES.index(upto))

    bay_set = {i for _, inner, _ in sources.bays for i in inner}
    r = PerimeterResult(poly=poly, config=cfg, upto=upto,
                        wing_ends=[cs[k][0] for k in sorted(rayless - bay_set)],
                        wing_ends_restored=[cs[k][0] for k in restored if k not in bay_set],
                        bay_corners=[cs[k][0] for k in sorted(rayless & bay_set)],
                        bays_restored=[cs[k][0] for k in restored if k in bay_set],
                        short_walls=sources.short_walls.records)

    # band and corner rays
    casting = r.corners = casting_corners(cs, rayless, sources.short_walls)
    r.offset_core, r.offset_band_line = offset_band(poly, bay_caps(cs, sources.bays, rayless, cfg), cfg)
    rays, at_band = to_band([cast(poly, p, d) for p, d, _, _ in casting], r.offset_band_line)

    # rules 1 to 3, on the lines: rule 2 is decided on the rays as cast; then the rays stop where they meet and
    # rule 3 discards those that reach nothing; rule 1 merges the rays that meet; rule 3 casts reflex rays again
    r.opposite_pairs, corner_lines, keep, rays, on_corner_line = join_opposite_rays(poly, casting, rays, cfg,
                                                                                   on("opposite"))
    ray_lines, keep, discarded = stop_rays(casting, rays, keep, on_corner_line, at_band, cfg, on("discard"))
    lines = ray_lines + corner_lines
    lines += merge_meeting_rays(poly, casting, lines, keep, r.opposite_pairs, corner_lines, r.offset_band_line,
                                on("merge_rays"))
    reattach(lines, poly, r.offset_band_line)
    again, r.discarded = cast_reflex_rays_again(poly, casting, rays, lines, discarded, on_corner_line, at_band,
                                                r.offset_band_line, cfg)
    lines += again

    # rule 4: narrow core becomes lines
    core, spines, r.legs, r.notches, r.narrow_parts = narrow_core_to_lines(r.offset_core, lines, poly, cfg,
                                                                           on("narrow_core"))
    if on("merge_rays"):  # rule 1 once more: a merged ray along the band line ends on it
        band = shapely.union_all([core.boundary, *spines]) if not core.is_empty or spines else LineString()
        r.rays_to_band = end_merged_rays_on_band(lines, band, cfg.merged_ray_band_m)

    # the zones, and rules 5 to 8 on them
    zones, r.core, r.band_line, r.lines = form_zones(poly, core, spines, lines, cfg)
    width = cfg.min_zone_width_m
    if on("close_points"):
        zones, r.points_merged = merge_close_points(poly, zones, cfg.close_points_m, math.inf, width)
    if on("diagonals"):
        zones, r.diagonals = narrow_zones_to_diagonals(zones, cfg)
    if on("corner_groups"):
        zones, r.corner_groups = merge_close_points(poly, zones, cfg.corner_group_m, cfg.corner_group_span_m, width)
    if on("thin_touching"):
        zones, r.thin_merged = merge_touching_thin_zones(poly, zones, cfg)
    if on("thin_lone"):
        zones, r.lone_merged = merge_lone_thin_zones(poly, zones, cfg)

    # rules 9, 7, 8 and 10 and the tidy step, repeated until the zones no longer change
    r.zones = _final_rounds(poly, zones, cfg, on, r)
    union = shapely.union_all([z for z, _ in r.zones])
    gap, overlap = poly.symmetric_difference(union).area, sum(z.area for z, _ in r.zones) - union.area
    if max(gap, overlap) > TILE_TOL * poly.area:
        raise PerimeterError(f"the zones do not tile the footprint: {gap:.4f} m2 gap, {overlap:.4f} m2 overlap")
    return r


def _final_rounds(poly: Polygon, zones: list[tuple[Polygon, str]], cfg: CornerRayConfig, on, r: PerimeterResult):
    """Rule 9 (sharp corners are cut off), then rules 7 and 8 once more (``repeat_thin_after_sharp``), rule 10
    (specks join a neighbour, ``join_specks``) and the tidy step, as one round; with ``repeat_until_stable`` the round
    runs again until the zones no longer change, at most :data:`MAX_ROUNDS` times. The records go into r. Returns the
    zones."""
    r.tidied = {k: [] for k in TIDIED}
    while (on("sharp") or on("specks")) and r.rounds < (MAX_ROUNDS if cfg.repeat_until_stable else 1):
        before = sorted(z.wkb for z, _ in zones)
        if on("sharp"):
            zones, cuts, whole = cut_sharp_corners(zones, cfg)
            r.sharp_cuts += cuts
            r.sharp_whole += whole
            if cfg.repeat_thin_after_sharp:
                zones, merged = merge_touching_thin_zones(poly, zones, cfg)
                r.thin_merged += merged
                zones, merged = merge_lone_thin_zones(poly, zones, cfg)
                r.lone_merged += merged
        if on("specks") and cfg.join_specks:
            zones, joined = join_specks(zones, cfg)
            r.specks_joined += joined
        if on("tidy"):
            zones, done = tidy_corners(poly, zones, cfg)
            for k, v in done.items():
                r.tidied[k] += v
        r.rounds += 1
        if sorted(z.wkb for z, _ in zones) == before:
            break
    return zones
