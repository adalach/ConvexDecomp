"""
osm_convex_decomposer.py

Convex decomposition of 2D polygons (e.g. OSM building parts) using
recursive cuts from reflex vertices.

Design:
- Polygon-centric: this module does NOT know about buildings, perimeters, etc.
- You call `decompose_polygon_with_stats(poly, cfg)` once per Polygon.
- Decomposition is modelled as a search in "state space" where a state is
  a list of current pieces [P1, P2, ...]. We refine states by splitting
  one non-convex piece at a time.
- A cut runs from a reflex vertex along a continued edge (or the interior
  bisector) to the first boundary point it reaches, and splits the piece's
  vertex list there (convexdecomp.geometry.cuts). Every cut gives exactly two
  pieces whose areas add up to the piece's, so a decomposition always covers
  its polygon exactly. A piece with a hole is opened by two such chords from
  the hole's vertices (convexdecomp.geometry.openings); each way of opening it
  is a child state like any other cut.

Key ideas:
- We explore the state space with a persistent adaptive beam search.
- Each state is canonicalised to a key (multiset of polygon keys) so that
  if two different split sequences lead to the same configuration, we
  process it only once (state dedup).
- At each refinement step we choose ONE non-convex piece and split it
  at a small number of promising reflex vertices.
- Reflex vertices are ordered by how close their *interior* angle is to
  270° (a strong “quarter-turn” reflex). Equivalently, we sort by
  concavity = interior_angle - 180° being close to 90°.
- We cap the number of distinct final variants at max_variants_per_polygon.

Public API:
    from osm_convex_decomposer import (
        ConvexDecompositionConfig,
        extract_polygons,
        decompose_polygon_with_stats,
        decompose_polygon_best,
    )
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import shapely
from shapely.geometry import Polygon, MultiPolygon
from shapely.geometry.base import BaseGeometry

from convexdecomp.geometry.cuts import cut_along_diagonal, cut_along_ray, inward_bisector
from convexdecomp.geometry.holes import holeless_polygons
from convexdecomp.geometry.openings import hole_openings
from convexdecomp.core.search import (
    AdaptiveSearchConfig,
    DecompositionSearchConfig,
    ExpandedState,
    StateExpansionResult,
    run_polygon_state_search,
    score_polygon_partition,
)
from convexdecomp.core.convexity import is_convex_polygon as _shared_is_convex_polygon

__all__ = [
    "ConvexDecompositionConfig",
    "extract_polygons",
    "decompose_polygon_with_stats",
    "decompose_polygon_best",
]

# ---------------------------------------------------------------------------
# Global tuning constants
# ---------------------------------------------------------------------------

# Smallest piece a cut may produce. A cut that would leave a smaller piece is
# refused, never accepted without it: dropping the crumb would leave a gap.
MIN_PART_AREA: float = 0.5


def _default_osm_adaptive_search() -> AdaptiveSearchConfig:
    # OSM perimeter remainders often need many sequential splits; keep the
    # beam narrow by default and spend budget on depth first.
    return AdaptiveSearchConfig(
        enabled=True,
        initial_depth=4,
        initial_width=3,
        max_depth=24,
        max_width=4,
        depth_step=1,
        width_step=1,
    )


# ---------------------------------------------------------------------------
# Configuration dataclass ("knobs")
# ---------------------------------------------------------------------------

@dataclass
class ConvexDecompositionConfig:
    """
    Parameters controlling the convex decomposition search.

    Attributes
    ----------
    search_depth : int
        Maximum recursion depth (max number of split steps) for exploring
        the state space.
    search_width : int
        Maximum number of open states kept in the active beam before the
        remaining scored states are parked for later widening.
    max_variants_per_polygon : int
        Hard cap on the number of distinct fully convex decomposition variants
        we are willing to keep for a single polygon. Only states where all
        pieces are convex count toward this cap.
    max_failed_states : Optional[int]
        Hard cap on the number of unsuccessful terminal states (dead-ends
        where at least one piece is non-convex and we either hit the depth
        limit or have no further splits). If None, no explicit limit is
        applied to failed states.
    min_area_m2 : float
        Area threshold for "too small" zones in the final result stats
        (used only for has_small_zone).
    weight_parts : float
        Weight for penalising many parts in the scoring function.
    weight_compactness : float
        Weight for rewarding compact shapes in the scoring function.
    min_concavity_deg : float
        Minimum concavity (in degrees) for a vertex to be treated as a
        meaningful reflex. Smaller concavities are considered "almost straight".
    max_reflex_per_node : Optional[int]
        Extra cap on how many reflex vertices per polygon we consider at a node.
        If None, defaults to max(2 * search_width, 8).
    cut_snap_vertex_tolerance_m : float
        A vertex the cut passes within this distance of, before the boundary
        point the cut would end at, takes the cut instead, so no hairline
        sliver is left beside it.
    adaptive_search : AdaptiveSearchConfig
        Optional outer retry schedule that starts from a smaller search budget
        and increases depth first, then width, until the first successful
        parameter setting is found.
    score_fn : callable or None
        Score of a list of pieces (lower is better) used everywhere the search
        compares states and picks the final variant, e.g. a
        ``convexdecomp.core.scores.PartitionScore``. None uses `weight_parts`
        and `weight_compactness` with the area-weighted compactness.
    beam_ranking : str
        How the beam orders states: ``"reflex_first"`` (non-convex pieces and
        reflex vertices first, the score breaks ties) or ``"score_first"``.
    bisector_always : bool
        Offer the cut along the bisector of the interior angle at every reflex
        vertex, next to the two carried-on edges. Otherwise the bisector is only
        tried where neither edge gives a cut. At a shallow bend both edges run
        almost along the piece and leave slivers, while the bisector cuts across.
    min_corner_deg : float
        Smallest corner a cut may create, in degrees; 0 turns the rule off. A cut
        creates corners only at its two ends (for a hole opening, at the ends of
        both chords), so the polygon's own sharp corners never block a cut. At a
        reflex vertex where neither carried-on edge passes, the bisector is tried
        instead. When no cut of a piece passes, all of them are allowed for that
        piece (marked with the fallback label "sharp_corner"), so the rule never
        makes a decomposition fail.
    track_time : bool
        If True, decompose_polygon_with_stats measures wall-clock time per
        polygon and adds a "time_seconds" field to the result dict.
    """

    search_depth: int = 24
    search_width: int = 4
    max_variants_per_polygon: int = 64
    max_failed_states: Optional[int] = None
    min_area_m2: float = 2.0
    weight_parts: float = 1.0
    weight_compactness: float = 20.0
    min_concavity_deg: float = 2.0
    max_reflex_per_node: Optional[int] = None
    track_time: bool = False
    use_bisector_dead_end_fallback: bool = True
    cut_snap_vertex_tolerance_m: float = 0.1
    adaptive_search: AdaptiveSearchConfig = field(default_factory=_default_osm_adaptive_search)
    score_fn: Optional[Callable[[Sequence[Polygon]], float]] = None
    beam_ranking: str = "reflex_first"
    bisector_always: bool = False
    min_corner_deg: float = 0.0

    def __post_init__(self) -> None:
        if self.max_reflex_per_node is None:
            self.max_reflex_per_node = max(int(self.search_width) * 2, 8)



# ---------------------------------------------------------------------------
# Small caches (convexity + polygon key for states)
# ---------------------------------------------------------------------------

# Cache canonical polygon keys for states keyed by raw ring key
_RING_SEPARATOR: Tuple[float, float] = (math.inf, math.inf)
_POLY_KEY_CACHE: Dict[Tuple[Tuple[float, float], ...], Tuple[Tuple[float, float], ...]] = {}


# ---------------------------------------------------------------------------
# Basic geometry helpers
# ---------------------------------------------------------------------------

def _polygon_orientation(coords: Sequence[Tuple[float, float]]) -> bool:
    """
    Return True if ring is CCW, False if CW.
    """
    area2 = 0.0
    n = len(coords)
    for i in range(n):
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]
        area2 += x1 * y2 - y1 * x2
    return area2 > 0.0


def _raw_ring_key(poly: Polygon, ndigits: int = 6) -> Tuple[Tuple[float, float], ...]:
    """
    Raw key for a polygon's rings (before normalisation), holes after the
    exterior, each behind a separator point. Used only for caching.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return tuple()
    key = [(round(x, ndigits), round(y, ndigits)) for (x, y) in poly.exterior.coords]
    for ring in poly.interiors:
        key.append(_RING_SEPARATOR)
        key.extend((round(x, ndigits), round(y, ndigits)) for (x, y) in ring.coords)
    return tuple(key)


def is_convex_polygon(poly: Polygon, tol: float = 0.0) -> bool:
    """
    Thin wrapper around the shared strict convexity helper.
    """
    return _shared_is_convex_polygon(poly, tol=tol)


def normalize_polygon(
    poly: Polygon,
    snap_tol: float = 1e-6,
    collinear_tol: float = 1e-10,
) -> Polygon:
    """
    Snap very close vertices and remove almost-collinear vertices on the exterior ring.
    Holes are kept as they are; this cleaner is aimed at perimeter-style shapes.

    Returns a cleaned Polygon; if cleaning fails or changes the polygon too much,
    returns the original polygon.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return poly

    coords = list(poly.exterior.coords)
    if len(coords) <= 4:  # triangle + closing coord
        return poly

    # 1) Snap very close vertices on the original ring
    snapped: List[Tuple[float, float]] = []
    for x, y in coords:
        if not snapped:
            snapped.append((x, y))
            continue
        x_prev, y_prev = snapped[-1]
        dx, dy = x - x_prev, y - y_prev
        if dx * dx + dy * dy < snap_tol * snap_tol:
            continue
        snapped.append((x, y))

    if len(snapped) < 3:
        return poly
    if snapped[0] != snapped[-1]:
        snapped.append(snapped[0])

    # 2) Remove almost-collinear interior vertices
    def _is_collinear(p0, p1, p2) -> bool:
        (x0, y0), (x1, y1), (x2, y2) = p0, p1, p2
        v1x, v1y = x1 - x0, y1 - y0
        v2x, v2y = x2 - x1, y2 - y1
        cross = v1x * v2y - v1y * v2x
        return abs(cross) < collinear_tol

    ring = snapped[:-1]
    n = len(ring)
    if n < 3:
        return poly

    cleaned: List[Tuple[float, float]] = []
    for i in range(n):
        p_prev = ring[(i - 1) % n]
        p_curr = ring[i]
        p_next = ring[(i + 1) % n]
        if _is_collinear(p_prev, p_curr, p_next):
            continue
        cleaned.append(p_curr)

    if len(cleaned) < 3:
        return poly

    # 3) Merge very close neighbours again
    dedup: List[Tuple[float, float]] = []
    for x, y in cleaned:
        if not dedup:
            dedup.append((x, y))
            continue
        x_prev, y_prev = dedup[-1]
        dx, dy = x - x_prev, y - y_prev
        if dx * dx + dy * dy < snap_tol * snap_tol:
            continue
        dedup.append((x, y))

    if len(dedup) < 3:
        return poly
    if dedup[0] != dedup[-1]:
        dedup.append(dedup[0])

    try:
        cleaned_poly = Polygon(dedup, [list(ring.coords) for ring in poly.interiors])
        if not cleaned_poly.is_valid or cleaned_poly.is_empty:
            return poly
        if poly.area > 0:
            rel_err = abs(cleaned_poly.area - poly.area) / poly.area
            if rel_err > 1e-4:
                return poly
        return cleaned_poly
    except Exception:
        return poly


def _ring_canonical_key(
    coords: Sequence[Tuple[float, float]],
    ndigits: int = 3,
) -> Tuple[Tuple[float, float], ...]:
    """
    Canonical key for a ring:
    - round coords
    - ignore closing point
    - invariant to rotation and orientation
    """
    pts = [(round(x, ndigits), round(y, ndigits)) for (x, y) in coords[:-1]]
    m = len(pts)
    if m == 0:
        return tuple()

    def rotations(seq):
        for i in range(len(seq)):
            yield tuple(seq[i:] + seq[:i])

    seq_fwd = pts
    seq_rev = list(reversed(pts))

    candidates = list(rotations(seq_fwd)) + list(rotations(seq_rev))
    return min(candidates)


def _poly_key_for_state(poly: Polygon, ndigits: int = 3) -> Tuple[Tuple[float, float], ...]:
    """
    Canonical key for a polygon when used inside a state.

    - Normalize polygon (snap + drop collinear vertices).
    - Drop very small polygons (area < MIN_PART_AREA).
    - Canonicalise exterior ring w.r.t. rotation/orientation; holes follow in
      sorted order, each behind a separator point, so a piece with a hole
      never shares a key with the same outline without it.
    - Results are cached by the raw ring key for speed.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return tuple()
    if poly.area < MIN_PART_AREA:
        return tuple()

    raw_key = _raw_ring_key(poly)
    if raw_key in _POLY_KEY_CACHE:
        return _POLY_KEY_CACHE[raw_key]

    norm = normalize_polygon(poly)
    if norm.is_empty or norm.area < MIN_PART_AREA:
        key: Tuple[Tuple[float, float], ...] = tuple()
    else:
        coords = list(norm.exterior.coords)
        key = _ring_canonical_key(coords, ndigits=ndigits)
        for hole_key in sorted(_ring_canonical_key(list(r.coords), ndigits=ndigits) for r in norm.interiors):
            key = key + (_RING_SEPARATOR,) + hole_key

    _POLY_KEY_CACHE[raw_key] = key
    return key


# ---------------------------------------------------------------------------
# Reflex detection (ordered by "closeness" to 90° concavity)
# ---------------------------------------------------------------------------

def find_reflex_vertices_scored(
    poly: Polygon,
    cfg: ConvexDecompositionConfig,
    tol: float = 1e-9,
) -> List[Tuple[int, float]]:
    """
    Return list of (vertex_index, concavity_rad) for reflex vertices on the exterior ring.

    concavity_rad = π - exterior_angle (0 for straight, larger = more reflex).

    Heuristic:
    - Only keep reflex vertices with concavity ≥ cfg.min_concavity_deg,
      where concavity_deg = interior_angle_deg - 180°.
    - Order them by how close their interior angle is to 270°:
      equivalently, we sort by |concavity_deg - 90°| ascending.
      Vertices whose interior angle is closest to 270° are tried first.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return []

    coords = list(poly.exterior.coords)[:-1]
    n = len(coords)
    if n <= 3:
        return []

    ccw = _polygon_orientation(coords)
    reflex_scored: List[Tuple[int, float]] = []

    min_concavity_rad = math.radians(cfg.min_concavity_deg)
    target_concavity_rad = math.pi / 2.0  # 90 degrees

    for i in range(n):
        x0, y0 = coords[i - 1]
        x1, y1 = coords[i]
        x2, y2 = coords[(i + 1) % n]

        v1x, v1y = x1 - x0, y1 - y0
        v2x, v2y = x2 - x1, y2 - y1
        cross = v1x * v2y - v1y * v2x

        if abs(cross) <= tol:
            continue

        is_reflex = (ccw and cross < -tol) or ((not ccw) and cross > tol)
        if not is_reflex:
            continue

        # vectors pointing into the vertex
        v_in1 = (x0 - x1, y0 - y1)
        v_in2 = (x2 - x1, y2 - y1)
        len1 = math.hypot(*v_in1)
        len2 = math.hypot(*v_in2)
        if len1 <= tol or len2 <= tol:
            continue

        u1 = (v_in1[0] / len1, v_in1[1] / len1)
        u2 = (v_in2[0] / len2, v_in2[1] / len2)
        dot = max(-1.0, min(1.0, u1[0] * u2[0] + u1[1] * u2[1]))
        exterior_ang = math.acos(dot)  # in (0, π)
        concavity = math.pi - exterior_ang  # 0 = straight, larger = more reflex

        if concavity < min_concavity_rad:
            continue

        reflex_scored.append((i, concavity))

    # Order by "closeness" to 90 degrees concavity
    reflex_scored.sort(key=lambda t: abs(t[1] - target_concavity_rad))

    max_r = int(cfg.max_reflex_per_node) if cfg.max_reflex_per_node is not None else max(int(cfg.search_width), 1)

    if len(reflex_scored) > max_r:
        reflex_scored = reflex_scored[:max_r]

    return reflex_scored


# ---------------------------------------------------------------------------
# Cuts (exact chords from convexdecomp.geometry.cuts; no overlay involved)
# ---------------------------------------------------------------------------

def cut_polygon_at_vertex(
    poly: Polygon,
    vidx: int,
    cfg: ConvexDecompositionConfig,
    use_prev: bool = True,
) -> List[Polygon]:
    """
    Cut at reflex vertex `vidx` along one of its edges carried on past the vertex.

    With `use_prev` the previous edge is continued, otherwise the next edge is
    continued backwards; at a reflex vertex both run into the polygon. The cut
    ends at the first boundary point it reaches, so it always gives exactly two
    pieces, whose areas add up to the polygon's.

    Returns the two pieces, or [poly] when there is no valid cut, including
    when a piece would be smaller than MIN_PART_AREA.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return [poly]

    coords = list(poly.exterior.coords)[:-1]
    n = len(coords)
    if n <= 3 or vidx < 0 or vidx >= n:
        return [poly]

    x1, y1 = coords[vidx]
    if use_prev:
        x0, y0 = coords[vidx - 1]
        direction = (x1 - x0, y1 - y0)
    else:
        x2, y2 = coords[(vidx + 1) % n]
        direction = (x1 - x2, y1 - y2)

    return cut_along_ray(
        poly,
        vidx,
        direction,
        snap_tol=cfg.cut_snap_vertex_tolerance_m,
        min_part_area=MIN_PART_AREA,
    )


def cut_polygon_at_vertex_bisector(
    poly: Polygon,
    vidx: int,
    cfg: ConvexDecompositionConfig,
) -> List[Polygon]:
    """
    Cut at vertex `vidx` along the bisector of the polygon's interior angle.

    This is a fallback for cases where continuing an incident edge either
    creates slivers or fails to progress. Like the edge cut, it ends at the
    first boundary point it reaches.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return [poly]

    coords = list(poly.exterior.coords)[:-1]
    n = len(coords)
    if n <= 3 or vidx < 0 or vidx >= n:
        return [poly]

    direction = inward_bisector(poly, vidx)
    if direction is None:
        return [poly]
    return cut_along_ray(
        poly,
        vidx,
        direction,
        snap_tol=cfg.cut_snap_vertex_tolerance_m,
        min_part_area=MIN_PART_AREA,
    )


def cut_polygon_at_vertex_pair(
    poly: Polygon,
    start_idx: int,
    end_idx: int,
) -> List[Polygon]:
    """
    Cut a polygon along the diagonal between two of its vertices.

    Returns [poly] when the diagonal does not run inside the polygon or a piece
    would be smaller than MIN_PART_AREA.
    """
    return cut_along_diagonal(poly, start_idx, end_idx, min_part_area=MIN_PART_AREA)


# ---------------------------------------------------------------------------
# State representation and scoring
# ---------------------------------------------------------------------------

def _state_key(pieces: List[Polygon]) -> Tuple[Tuple[Tuple[float, float], ...], ...]:
    """
    Canonical key for a state (unordered list of polygon pieces).

    - Each piece → canonical polygon key.
    - State key = sorted tuple of polygon keys.

    If any piece fails to produce a key, we consider the state invalid
    and return an empty tuple.
    """
    keys: List[Tuple[float, float]] = []
    for p in pieces:
        k = _poly_key_for_state(p)
        if not k:
            return tuple()
        keys.append(k)
    # sort keys for order-invariance
    return tuple(sorted(keys))


def _score_polys(
    polys: List[Polygon],
    w_parts: float,
    w_compact: float,
) -> float:
    return score_polygon_partition(
        polys,
        weight_parts=w_parts,
        weight_compactness=w_compact,
    )


def _new_corner_deg(parts: Sequence[Polygon]) -> float:
    """The smallest corner, in degrees, that a cut gave its pieces.

    The pieces of one cut touch only along the cut (or both chords of a hole
    opening), so a piece's corners the cut created are its vertices lying on
    another piece's boundary. A vertex is there even where the cut leaves the
    other piece straight, which drops the point from that piece's ring.
    """
    smallest = 180.0
    for i, part in enumerate(parts):
        coords = list(part.exterior.coords)[:-1]
        minx, miny, maxx, maxy = part.bounds
        eps = 1e-9 * max(maxx - minx, maxy - miny, 1.0)
        points = shapely.points(coords)
        on_cut = [False] * len(coords)
        for j, other in enumerate(parts):
            if j != i:
                near = shapely.distance(other.boundary, points) <= eps
                on_cut = [a or bool(b) for a, b in zip(on_cut, near)]
        ccw = part.exterior.is_ccw
        n = len(coords)
        for k, hit in enumerate(on_cut):
            if not hit:
                continue
            (ax, ay), (bx, by), (cx, cy) = coords[k - 1], coords[k], coords[(k + 1) % n]
            ux, uy, wx, wy = cx - bx, cy - by, ax - bx, ay - by
            angle = math.degrees(math.atan2(ux * wy - uy * wx, ux * wx + uy * wy)) % 360.0
            if not ccw:
                angle = (360.0 - angle) % 360.0
            smallest = min(smallest, angle)
    return smallest


def _sharp(parts: Sequence[Polygon], cfg: "ConvexDecompositionConfig") -> bool:
    """Whether a cut creates a corner smaller than `cfg.min_corner_deg`."""
    return cfg.min_corner_deg > 0.0 and _new_corner_deg(parts) < cfg.min_corner_deg


def _partition_score(polys: Sequence[Polygon], cfg: ConvexDecompositionConfig) -> float:
    """The configured score of a list of pieces (lower is better)."""
    if cfg.score_fn is not None:
        return float(cfg.score_fn(list(polys)))
    return _score_polys(list(polys), cfg.weight_parts, cfg.weight_compactness)


# ---------------------------------------------------------------------------
# Core state-space search for one polygon
# ---------------------------------------------------------------------------

def _enumerate_states_for_polygon(
    poly: Polygon,
    cfg: ConvexDecompositionConfig,
) -> Dict[str, Any]:
    """
    Explore the decomposition state space for a single polygon.

    Returns a list of variants; each variant is a list[Polygon] of parts,
    and **every returned variant is fully convex** (all pieces convex).

    Performance control:
    - Depth limit: cfg.search_depth.
    - A 'terminal' state is either:
        * a fully convex configuration (success), or
        * a non-convex configuration where we cannot or do not want to split
          further (no reflex vertices or depth limit reached).
    - We maintain two counters:
        * n_success: number of fully convex terminal states (variants).
        * n_failed:  number of non-convex terminal states (dead-ends).
    - We stop exploring when:
        * n_success >= cfg.max_variants_per_polygon, or
        * n_failed  >= cfg.max_failed_states (if not None).

    So:
    - Only fully convex terminal states are returned as variants and are used
      in scoring.
    - Unsuccessful terminal states are still "seen" and used to stop the
      search, but they are not returned or counted as variants.
    """
    if not isinstance(poly, Polygon) or poly.is_empty:
        return {
            "variants": [],
            "n_success": 0,
            "n_failed": 0,
            "n_depth_limit_dead_ends": 0,
            "n_no_reflex_dead_ends": 0,
            "n_min_area_dead_ends": 0,
            "n_states_seen": 0,
            "terminated_by": "invalid_or_empty_input",
        }

    # Normalise root once
    root = normalize_polygon(poly)
    if root.is_empty or root.area < MIN_PART_AREA:
        return {
            "variants": [],
            "n_success": 0,
            "n_failed": 0,
            "n_depth_limit_dead_ends": 0,
            "n_no_reflex_dead_ends": 0,
            "n_min_area_dead_ends": 0,
            "n_states_seen": 0,
            "terminated_by": "invalid_root",
        }

    def _expand_state(pieces: List[Polygon]) -> StateExpansionResult:
        result = StateExpansionResult()
        children_by_key: Dict[Any, ExpandedState] = {}
        best_idx: Optional[int] = None
        diagonal_fallback_idx: Optional[int] = None
        best_reflexes: List[Tuple[int, float]] = []
        best_area = -1.0
        diagonal_fallback_area = -1.0

        # The piece cut next is the largest one that is not convex: a piece with a
        # hole, or one with reflex vertices on its outer ring.
        for idx, candidate in enumerate(pieces):
            if not isinstance(candidate, Polygon) or candidate.is_empty or is_convex_polygon(candidate):
                continue
            if not candidate.interiors and candidate.area > diagonal_fallback_area:
                diagonal_fallback_idx = idx
                diagonal_fallback_area = candidate.area
            reflexes = find_reflex_vertices_scored(candidate, cfg)
            if not reflexes and not candidate.interiors:
                continue
            if candidate.area > best_area:
                best_area = candidate.area
                best_idx = idx
                best_reflexes = reflexes

        def _register_child(
            target_idx: int,
            parts: Sequence[Polygon],
            *,
            fallback_label: str | None = None,
        ) -> None:
            new_pieces = pieces[:target_idx] + list(parts) + pieces[target_idx + 1 :]
            child_key = _state_key(new_pieces)
            if not child_key:
                return
            child = ExpandedState(tuple(new_pieces), (fallback_label,) if fallback_label else ())
            existing = children_by_key.get(child_key)
            if existing is None or (existing.fallback_labels and not child.fallback_labels):
                children_by_key[child_key] = child

        if best_idx is not None and pieces[best_idx].interiors:
            # Every way of opening the hole with two chords is a child, like any other cut.
            target_poly = pieces[best_idx]
            min_area_pruned = False
            sharp_openings: List[List[Polygon]] = []
            for opening in hole_openings(
                target_poly,
                snap_tol=cfg.cut_snap_vertex_tolerance_m,
                min_part_area=MIN_PART_AREA,
            ):
                result.metrics.update(["search_split_attempts"])
                if any(part.area < cfg.min_area_m2 for part in opening.pieces):
                    min_area_pruned = True
                    continue
                if _sharp(opening.pieces, cfg):
                    sharp_openings.append(opening.pieces)
                    continue
                result.metrics.update(["search_successful_splits"])
                _register_child(best_idx, opening.pieces)
            if not children_by_key:
                # Every opening creates a corner below the limit: allow them all here.
                for parts in sharp_openings:
                    _register_child(best_idx, parts, fallback_label="sharp_corner")
            if children_by_key:
                result.children.extend(children_by_key.values())
                return result

            # No pair of chords opens the hole: every chord reaches another hole first,
            # or every opening leaves a piece below the minimum area. Split the piece
            # along its hole edges instead, as before the search knew openings.
            parts = [part for part in holeless_polygons(target_poly) if part.area > 0.0]
            if len(parts) >= 2:
                _register_child(best_idx, parts, fallback_label="hole_edge_split")
            if children_by_key:
                result.children.extend(children_by_key.values())
                return result
            result.dead_end_counts.update(["min_area_dead_end" if min_area_pruned else "no_reflex_dead_end"])
            return result

        if best_idx is None or not best_reflexes:
            min_area_pruned = False
            if diagonal_fallback_idx is not None:
                target_poly = pieces[diagonal_fallback_idx]
                coords = list(target_poly.exterior.coords)[:-1]
                diagonal_candidates: List[Tuple[bool, float, List[Polygon]]] = []

                for i in range(len(coords)):
                    for j in range(i + 1, len(coords)):
                        parts = cut_polygon_at_vertex_pair(target_poly, i, j)
                        if len(parts) != 2:
                            continue
                        if any(part.area < cfg.min_area_m2 for part in parts):
                            min_area_pruned = True
                            continue
                        all_convex_parts = all(is_convex_polygon(part) for part in parts)
                        score = _partition_score(parts, cfg)
                        diagonal_candidates.append((all_convex_parts, score, parts))

                if diagonal_candidates:
                    diagonal_candidates.sort(key=lambda item: (not item[0], item[1]))
                    max_diagonal_splits = max(2, cfg.search_width * 2)
                    for _, _, parts in diagonal_candidates[:max_diagonal_splits]:
                        _register_child(diagonal_fallback_idx, parts, fallback_label="diagonal_no_reflex")
                    if children_by_key:
                        result.children.extend(children_by_key.values())
                        return result

            result.dead_end_counts.update(["min_area_dead_end" if min_area_pruned else "no_reflex_dead_end"])
            return result

        target_poly = pieces[best_idx]
        min_area_pruned = False

        sharp_cuts: List[List[Polygon]] = []
        for vidx, _ in best_reflexes:
            passed_here = 0
            for use_prev in (True, False, None):
                if use_prev is None and not (cfg.bisector_always or (cfg.min_corner_deg > 0.0 and not passed_here)):
                    continue  # the bisector: always, or where neither edge passed the corner rule
                result.metrics.update(["search_split_attempts"])
                if use_prev is None:
                    parts = cut_polygon_at_vertex_bisector(target_poly, vidx, cfg)
                else:
                    parts = cut_polygon_at_vertex(target_poly, vidx, cfg, use_prev=use_prev)
                if len(parts) != 2:
                    continue
                if any(part.area < cfg.min_area_m2 for part in parts):
                    min_area_pruned = True
                    continue
                if _sharp(parts, cfg):
                    result.metrics.update(["sharp_corner_refusals"])
                    sharp_cuts.append(parts)
                    continue

                passed_here += 1
                result.metrics.update(["search_successful_splits"])
                _register_child(best_idx, parts)

        if not children_by_key:
            # Every cut creates a corner below the limit: allow them all for this piece.
            for parts in sharp_cuts:
                _register_child(best_idx, parts, fallback_label="sharp_corner")

        if children_by_key:
            result.children.extend(children_by_key.values())
            return result

        if cfg.use_bisector_dead_end_fallback:
            result.metrics.update(["bisector_fallback_attempts"])
            bisector_child_count_before = len(children_by_key)
            for vidx, _ in best_reflexes:
                parts = cut_polygon_at_vertex_bisector(target_poly, vidx, cfg)
                if len(parts) != 2:
                    continue
                if any(part.area < cfg.min_area_m2 for part in parts):
                    min_area_pruned = True
                    continue

                _register_child(best_idx, parts, fallback_label="bisector_dead_end")
            if len(children_by_key) > bisector_child_count_before:
                result.metrics.update(["bisector_fallback_successes"])

        if children_by_key:
            result.children.extend(children_by_key.values())
            return result

        result.dead_end_counts.update(["min_area_dead_end" if min_area_pruned else "no_reflex_dead_end"])
        return result

    search = run_polygon_state_search(
        [root],
        cfg=DecompositionSearchConfig(
            max_depth=cfg.search_depth,
            max_variants=cfg.max_variants_per_polygon,
            max_failed_states=cfg.max_failed_states,
        ),
        is_convex_piece=is_convex_polygon,
        state_key_fn=_state_key,
        expand_state_fn=_expand_state,
        adaptive_cfg=cfg.adaptive_search,
        default_width=cfg.search_width,
        state_score_fn=lambda pieces: _partition_score(pieces, cfg),
        min_piece_area=cfg.min_area_m2,
        ranking=cfg.beam_ranking,
    )
    dead_end_counts = search.get("dead_end_counts") or {}
    return {
        "variants": list(search.get("variants") or []),
        "n_success": int(search.get("n_success", 0)),
        "n_failed": int(search.get("n_failed", 0)),
        "n_depth_limit_dead_ends": int(dead_end_counts.get("depth_limit_dead_end", 0)),
        "n_no_reflex_dead_ends": int(dead_end_counts.get("no_reflex_dead_end", 0)),
        "n_min_area_dead_ends": int(dead_end_counts.get("min_area_dead_end", 0)),
        "n_states_seen": int(search.get("n_states_seen", 0)),
        "search_total_states": int(search.get("search_total_states", 0)),
        "search_max_depth": int(search.get("search_max_depth", 0)),
        "search_max_width": int(search.get("search_max_width", 0)),
        "search_split_attempts": int(search.get("search_split_attempts", 0)),
        "search_successful_splits": int(search.get("search_successful_splits", 0)),
        "bisector_fallback_attempts": int(search.get("bisector_fallback_attempts", 0)),
        "bisector_fallback_successes": int(search.get("bisector_fallback_successes", 0)),
        "terminated_by": str(search.get("terminated_by", "queue_exhausted")),
        "search_depth_used": search.get("search_depth_used"),
        "search_width_used": search.get("search_width_used"),
        "search_attempt_count": int(search.get("search_attempt_count", 0)),
        "search_attempt_history": list(search.get("search_attempt_history") or []),
    }

# ---------------------------------------------------------------------------
# Polygon-level public API
# ---------------------------------------------------------------------------

def decompose_polygon_with_stats(
    poly: Polygon,
    cfg: ConvexDecompositionConfig,
) -> Dict[str, Any]:
    """
    Run convex decomposition on a single polygon and return detailed statistics.

    Returns a dict with keys:
        - convex_success : bool
            True iff at least one fully convex decomposition variant was found.
        - n_variants : int
            Number of fully convex variants found (capped by cfg.max_variants_per_polygon).
        - best_variant : list[Polygon]
            Chosen configuration (fully convex if convex_success is True,
            otherwise best_variant returns an empty list and n_parts is 0).
        - fully_convex_best : bool
            True if best_variant is fully convex (i.e., convex_success is True).
        - has_small_zone : bool
            True if any part in best_variant has area < cfg.min_area_m2.
        - n_parts : int
            len(best_variant).
        - time_seconds : float
            Only if cfg.track_time is True.
        - search_terminated_by : str
            Why the search loop stopped.
        - n_failed_terminal_states : int
            Number of non-convex terminal dead-end states encountered.
        - n_depth_limit_dead_ends : int
            Failed terminal states caused by hitting `search_depth`.
        - n_no_reflex_dead_ends : int
            Failed terminal states where no further reflex-driven split was available.
        - n_min_area_dead_ends : int
            Failed terminal states where every available split branch created a
            sub-polygon below `cfg.min_area_m2`.
        - n_states_seen : int
            Number of unique decomposition states explored.
    """
    t0 = time.perf_counter() if getattr(cfg, "track_time", False) else None

    def _with_time(res: Dict[str, Any]) -> Dict[str, Any]:
        if t0 is not None:
            out = dict(res)
            out["time_seconds"] = time.perf_counter() - t0
            return out
        return res

    # Early exit: invalid / empty polygon
    if not isinstance(poly, Polygon) or poly.is_empty:
        return _with_time({
            "convex_success": False,
            "n_variants": 0,
            "best_variant": [],
            "fully_convex_best": False,
            "has_small_zone": False,
            "n_parts": 0,
            "search_terminated_by": "invalid_or_empty_input",
            "n_failed_terminal_states": 0,
            "n_depth_limit_dead_ends": 0,
            "n_no_reflex_dead_ends": 0,
            "n_min_area_dead_ends": 0,
            "n_states_seen": 0,
            "search_total_states": 0,
            "search_max_depth": 0,
            "search_max_width": 0,
            "search_split_attempts": 0,
            "search_successful_splits": 0,
            "bisector_fallback_attempts": 0,
            "bisector_fallback_successes": 0,
            "search_depth_used": None,
            "search_width_used": None,
            "search_attempt_count": 0,
            "search_attempt_history": [],
            "fallback_used": None,
        })

    # Early exit: polygon already convex → treat as one successful variant
    if is_convex_polygon(poly):
        return _with_time({
            "convex_success": True,
            "n_variants": 1,
            "best_variant": [poly],
            "fully_convex_best": True,
            "has_small_zone": poly.area < cfg.min_area_m2,
            "n_parts": 1,
            "search_terminated_by": "already_convex",
            "n_failed_terminal_states": 0,
            "n_depth_limit_dead_ends": 0,
            "n_no_reflex_dead_ends": 0,
            "n_min_area_dead_ends": 0,
            "n_states_seen": 1,
            "search_total_states": 1,
            "search_max_depth": 0,
            "search_max_width": 0,
            "search_split_attempts": 0,
            "search_successful_splits": 0,
            "bisector_fallback_attempts": 0,
            "bisector_fallback_successes": 0,
            "search_depth_used": None,
            "search_width_used": None,
            "search_attempt_count": 0,
            "search_attempt_history": [],
            "fallback_used": None,
        })

    # Enumerate ONLY fully convex variants.
    # Adaptive retries now preserve a live frontier instead of restarting.
    search = _enumerate_states_for_polygon(poly, cfg)
    variants = [record for record in search["variants"] if record.get("parts")]
    n_variants = len(variants)

    if n_variants == 0:
        # No fully convex decomposition found: report failure and fall back
        # to the original polygon as a single-piece configuration.
        return _with_time({
            "convex_success": False,
            "n_variants": 0,
            "best_variant": [],
            "fully_convex_best": False,
            "has_small_zone": False,
            "n_parts": 0,
            "search_terminated_by": search.get("terminated_by", "no_fully_convex_variant"),
            "n_failed_terminal_states": int(search.get("n_failed", 0)),
            "n_depth_limit_dead_ends": int(search.get("n_depth_limit_dead_ends", 0)),
            "n_no_reflex_dead_ends": int(search.get("n_no_reflex_dead_ends", 0)),
            "n_min_area_dead_ends": int(search.get("n_min_area_dead_ends", 0)),
            "n_states_seen": int(search.get("n_states_seen", 0)),
            "search_total_states": int(search.get("search_total_states", 0)),
            "search_max_depth": int(search.get("search_max_depth", 0)),
            "search_max_width": int(search.get("search_max_width", 0)),
            "search_split_attempts": int(search.get("search_split_attempts", 0)),
            "search_successful_splits": int(search.get("search_successful_splits", 0)),
            "bisector_fallback_attempts": int(search.get("bisector_fallback_attempts", 0)),
            "bisector_fallback_successes": int(search.get("bisector_fallback_successes", 0)),
            "search_depth_used": search.get("search_depth_used"),
            "search_width_used": search.get("search_width_used"),
            "search_attempt_count": int(search.get("search_attempt_count", 0)),
            "search_attempt_history": list(search.get("search_attempt_history") or []),
            "fallback_used": None,
        })

    # At this point, every variant in `variants` is fully convex by construction.
    def _pick_best(var_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        best_idx = 0
        best_score = float("inf")
        best_uses_fallback = True
        for i, var in enumerate(var_list):
            parts = var["parts"]
            score = _partition_score(parts, cfg)
            uses_fallback = bool(var.get("fallback_used"))
            if (score, uses_fallback) < (best_score, best_uses_fallback):
                best_score = score
                best_uses_fallback = uses_fallback
                best_idx = i
        return var_list[best_idx]

    best_variant_record = _pick_best(variants)
    best_variant = best_variant_record["parts"]
    has_small_zone = any(p.area < cfg.min_area_m2 for p in best_variant)

    return _with_time({
        "convex_success": True,
        "n_variants": n_variants,
        "best_variant": best_variant,
        "fully_convex_best": True,  # by definition, since all parts are convex
        "has_small_zone": has_small_zone,
        "n_parts": len(best_variant),
        "search_terminated_by": search.get("terminated_by", "queue_exhausted"),
        "n_failed_terminal_states": int(search.get("n_failed", 0)),
        "n_depth_limit_dead_ends": int(search.get("n_depth_limit_dead_ends", 0)),
        "n_no_reflex_dead_ends": int(search.get("n_no_reflex_dead_ends", 0)),
        "n_min_area_dead_ends": int(search.get("n_min_area_dead_ends", 0)),
        "n_states_seen": int(search.get("n_states_seen", 0)),
        "search_total_states": int(search.get("search_total_states", 0)),
        "search_max_depth": int(search.get("search_max_depth", 0)),
        "search_max_width": int(search.get("search_max_width", 0)),
        "search_split_attempts": int(search.get("search_split_attempts", 0)),
        "search_successful_splits": int(search.get("search_successful_splits", 0)),
        "bisector_fallback_attempts": int(search.get("bisector_fallback_attempts", 0)),
        "bisector_fallback_successes": int(search.get("bisector_fallback_successes", 0)),
        "search_depth_used": search.get("search_depth_used"),
        "search_width_used": search.get("search_width_used"),
        "search_attempt_count": int(search.get("search_attempt_count", 0)),
        "search_attempt_history": list(search.get("search_attempt_history") or []),
        "fallback_used": best_variant_record.get("fallback_used"),
    })


def decompose_polygon_best(poly: Polygon, cfg: ConvexDecompositionConfig) -> List[Polygon]:
    """
    Convenience wrapper: return only the best decomposition variant (list[Polygon]).
    """
    return decompose_polygon_with_stats(poly, cfg)["best_variant"]


# ---------------------------------------------------------------------------
# Generic polygon extraction helper (usable from notebooks)
# ---------------------------------------------------------------------------

def extract_polygons(value: Any) -> List[Polygon]:
    """
    Flatten any supported geometry container to a list of Polygon objects.

    Accepts:
      - Polygon
      - MultiPolygon
      - Geometry collections with .geoms
      - sequences (list/tuple) of any of the above

    This is useful in notebooks for turning:
        geometry, perimeter_parts, interior_geom, ...
    into lists of simple Polygon instances before calling
    `decompose_polygon_with_stats`.
    """
    polys: List[Polygon] = []

    if value is None:
        return polys

    if isinstance(value, Polygon):
        if not value.is_empty:
            polys.append(value)
        return polys

    if isinstance(value, MultiPolygon):
        polys.extend([p for p in value.geoms if isinstance(p, Polygon) and not p.is_empty])
        return polys

    if isinstance(value, (list, tuple)):
        for g in value:
            polys.extend(extract_polygons(g))
        return polys

    if isinstance(value, BaseGeometry) and hasattr(value, "geoms"):
        for g in value.geoms:
            polys.extend(extract_polygons(g))
        return polys

    return polys
