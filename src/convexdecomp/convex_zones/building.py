"""One building's convex zones: every zone of its perimeter decomposed by the engine, and the fallback for a zone the
engine cannot cut.

:func:`decompose_zones` takes the zones a perimeter gives (``(polygon, kind)`` pairs, as
:attr:`convexdecomp.corner_perimeter.PerimeterResult.zones` holds them: kinds ``zone``, ``thin`` and ``core``) and
decomposes each of them as it is: a convex zone comes back as one part, the others are cut. When a piece cannot be cut,
:func:`merge_failed_pieces` merges it with neighbours, and the whole footprint is the last resort
(:class:`~.settings.ZoneConfig`'s fallback section). :func:`decompose_pieces` is the same with the engine call and the
fallback given directly: a caller that stores or times every piece's search passes its own ``decompose``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from convexdecomp.osm.decompose import decompose_polygon_with_stats, extract_polygons

from .settings import ZoneConfig, engine_config

#: the engine call: a polygon's search result, as ``decompose_polygon_with_stats`` gives it
Decompose = Callable[[Polygon], dict[str, Any]]


@dataclass
class Piece:
    """A polygon given to the engine: one of the building's zones, a union of several (``merged``) or a polygon of
    the whole footprint (``footprint``), with the search's result."""

    polygon: Polygon
    kind: str
    #: the building's zones it is made of (their positions in :attr:`BuildingZones.zones`)
    sources: tuple[int, ...]
    stats: dict[str, Any]
    #: the search's time (the engine's own ``time_seconds`` when it measures it)
    seconds: float = 0.0

    @property
    def convex(self) -> bool:
        """Whether the engine found a convex decomposition."""
        return bool(self.stats["fully_convex_best"])

    @property
    def parts(self) -> list[Polygon]:
        """The convex parts (none when the engine found no decomposition)."""
        return list(self.stats["best_variant"]) if self.convex else []


@dataclass
class BuildingZones:
    """A building's decomposition: its zones as given, every piece of the first pass, and the pieces kept."""

    zones: list[tuple[Polygon, str]]
    #: one piece per zone, before any fallback
    first: list[Piece]
    #: the pieces kept: the first pass, or after the fallback
    pieces: list[Piece]
    #: ``None``, ``"merge"`` or ``"whole"``
    fallback: str | None = None
    merges: int = 0
    footprint: Polygon | MultiPolygon | None = None
    #: the time of every search the building needed, kept or not
    seconds: float = 0.0
    #: every search in the order it ran: (the kind of the piece searched, seconds)
    searches: list[tuple[str, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every piece kept has a convex decomposition."""
        return bool(self.pieces) and all(p.convex for p in self.pieces)

    @property
    def failed(self) -> list[Piece]:
        """The pieces kept without a decomposition."""
        return [p for p in self.pieces if not p.convex]

    @property
    def parts(self) -> list[tuple[Polygon, int]]:
        """The convex zones, each with the position of its piece in :attr:`pieces`."""
        return [(part, k) for k, p in enumerate(self.pieces) for part in p.parts]

    @property
    def discarded_seconds(self) -> float:
        """The time of the searches whose result the building did not keep (the fallback's attempts)."""
        return max(0.0, self.seconds - sum(p.seconds for p in self.pieces))


def _timed(decompose: Decompose, polygon: Polygon) -> tuple[dict[str, Any], float]:
    t0 = time.perf_counter()
    stats = decompose(polygon)
    seconds = stats.get("time_seconds")
    return stats, float(seconds) if seconds is not None else time.perf_counter() - t0


def shared_edge_m(a: Polygon, b: Polygon) -> float:
    """How much boundary two polygons share."""
    return float(a.boundary.intersection(b.boundary).length)


def merge_failed_pieces(
    pieces: Sequence[Piece],
    decompose: Decompose,
    *,
    max_share: float = 0.5,
    min_shared_edge_m: float = 0.05,
    searches: list[tuple[str, float]] | None = None,
) -> tuple[list[Piece] | None, int]:
    """Merge each piece the engine could not cut with a neighbour, until every piece is cut.

    The first failing piece is tried with its neighbours (pieces sharing at least ``min_shared_edge_m`` of edge),
    longest shared edge first: the first union that decomposes replaces the two pieces. When no union decomposes,
    the piece is merged with the neighbour it shares most edge with and tried again from there, as long as the merged
    piece stays within ``max_share`` of the building's area. Returns the new pieces (None when the merging gives up)
    and the number of merges; every search's time goes to ``searches``.
    """
    pieces = list(pieces)
    building_area = sum(p.polygon.area for p in pieces)
    merges = 0

    while True:
        failing = [k for k, p in enumerate(pieces) if not p.convex]
        if not failing:
            return pieces, merges
        i = failing[0]
        piece = pieces[i]
        neighbours = sorted(
            ((shared_edge_m(piece.polygon, other.polygon), j) for j, other in enumerate(pieces) if j != i),
            reverse=True,
        )
        neighbours = [(length, j) for length, j in neighbours if length >= min_shared_edge_m]
        tried: dict[int, tuple[Polygon, dict[str, Any], float]] = {}
        chosen = None
        for _, j in neighbours:
            union = unary_union([piece.polygon, pieces[j].polygon])
            if not isinstance(union, Polygon):
                continue
            stats, seconds = _timed(decompose, union)
            if searches is not None:
                searches.append(("merged", seconds))
            tried[j] = (union, stats, seconds)
            if stats["fully_convex_best"]:
                chosen = j
                break
        if chosen is None:
            # No neighbour helps on its own: take the one sharing most edge and go on from there, unless the merged
            # piece would hold too much of the building.
            if not neighbours or len(pieces) <= 2 or neighbours[0][1] not in tried:
                return None, merges
            chosen = neighbours[0][1]
            if tried[chosen][0].area > max_share * building_area:
                return None, merges
        union, stats, seconds = tried[chosen]
        merged = Piece(union, "merged", piece.sources + pieces[chosen].sources, stats, seconds)
        pieces = [p for k, p in enumerate(pieces) if k not in (i, chosen)] + [merged]
        merges += 1


def _zone_pairs(zones: Sequence[Polygon | tuple[Polygon, str]]) -> list[tuple[Polygon, str]]:
    return [z if isinstance(z, tuple) else (z, "zone") for z in zones]


def decompose_pieces(
    zones: Sequence[Polygon | tuple[Polygon, str]],
    decompose: Decompose,
    *,
    footprint: Polygon | MultiPolygon | None = None,
    merge: bool = True,
    max_share: float = 0.5,
    min_shared_edge_m: float = 0.05,
    whole: bool = True,
) -> BuildingZones:
    """Decompose every zone with ``decompose``; when a piece fails, merge (``merge``) and then decompose the whole
    footprint (``whole``; the union of the zones when ``footprint`` is None).

    A plain polygon among ``zones`` is a zone of kind ``zone``. The zones are searched, and merged, in their order
    with the core parts (kind ``core``) last, as ``OSM_2_Decomposition.ipynb`` takes them; a piece's ``sources`` are
    positions in ``zones`` as given. Zones that are all of kind ``footprint`` (no perimeter) get no fallback. When
    merging gives up and the whole footprint does not decompose either, the building keeps its first pass.
    """
    pairs = _zone_pairs(zones)
    order = sorted(range(len(pairs)), key=lambda k: pairs[k][1] == "core")
    searches: list[tuple[str, float]] = []
    first = []
    for k in order:
        polygon, kind = pairs[k]
        stats, seconds = _timed(decompose, polygon)
        searches.append((kind, seconds))
        first.append(Piece(polygon, kind, (k,), stats, seconds))
    result = BuildingZones(pairs, first, list(first), footprint=footprint, searches=searches)

    if result.ok or not first or all(kind == "footprint" for _, kind in pairs):
        result.seconds = sum(s for _, s in searches)
        return result
    if merge:
        merged, merges = merge_failed_pieces(first, decompose, max_share=max_share,
                                             min_shared_edge_m=min_shared_edge_m, searches=searches)
        if merged is not None:
            result.pieces, result.merges, result.fallback = merged, merges, "merge"
    if result.fallback is None and whole:
        outline = footprint if footprint is not None else unary_union([z for z, _ in pairs])
        polygons = extract_polygons(outline)
        candidates = []
        for polygon in polygons:
            stats, seconds = _timed(decompose, polygon)
            searches.append(("footprint", seconds))
            inside = tuple(k for k, (z, _) in enumerate(pairs) if polygon.covers(z.representative_point()))
            candidates.append(Piece(polygon, "footprint", inside, stats, seconds))
        if candidates and all(p.convex for p in candidates):
            result.pieces, result.fallback = candidates, "whole"
    result.seconds = sum(s for _, s in searches)
    return result


def decompose_zones(
    zones: Sequence[Polygon | tuple[Polygon, str]],
    config: ZoneConfig | None = None,
    *,
    footprint: Polygon | MultiPolygon | None = None,
    decompose: Decompose | None = None,
) -> BuildingZones:
    """A building's convex zones from its perimeter's zones, with the settings of ``config``
    (:func:`decompose_pieces`; ``decompose`` defaults to the engine with :func:`~.settings.engine_config`)."""
    config = config or ZoneConfig()
    if decompose is None:
        cfg = engine_config(config)

        def decompose(polygon: Polygon) -> dict[str, Any]:
            return decompose_polygon_with_stats(polygon, cfg)

    return decompose_pieces(zones, decompose, footprint=footprint, merge=config.merge_failed,
                            max_share=config.merge_max_share, min_shared_edge_m=config.min_shared_edge_m,
                            whole=config.whole_footprint)


__all__ = ["BuildingZones", "Decompose", "Piece", "decompose_pieces", "decompose_zones", "merge_failed_pieces",
           "shared_edge_m"]
