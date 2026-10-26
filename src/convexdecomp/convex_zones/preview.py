"""What a settings page draws and counts for one building: the zones before and after the decomposition, the numbers
that show what the engine and the fallback did, and the building's fate.

:func:`preview` runs :func:`~.building.decompose_zones` on the zones of a perimeter (or runs the corner-ray perimeter
first) and never raises: a building whose pieces cannot all be cut comes back with ``fate = {"status": "dropped",
"reason": "decomposition_failed", ...}``. The layers are shapely geometries in the footprint's own frame (metres); a
caller that sends them elsewhere converts them (WKB, GeoJSON). :func:`zone_records` and :func:`piece_records` give
the zones and the pieces as rows of long tables.
"""

from __future__ import annotations

import math
import time
from typing import Any

import shapely
from shapely.geometry import LineString, MultiLineString, Polygon

from convexdecomp.core.scores import minimum_width
from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter
from convexdecomp.corner_perimeter.geometry import is_convex

from .building import BuildingZones, Decompose, decompose_zones
from .settings import ZoneConfig

#: the layers drawn before and after the decomposition, in drawing order (later on top)
LAYERS = {
    "before": ("footprint", "perimeter_zones", "thin_zones", "core", "not_convex"),
    "after": ("perimeter_parts", "core_parts", "merged_parts", "footprint_parts", "failed_pieces", "zone_lines",
              "removed_lines", "cut_lines"),
}
#: what each layer is, in words
LAYER_TITLES = {
    "footprint": "Footprint",
    "perimeter_zones": "Perimeter zones",
    "thin_zones": "Zones thinner than the perimeter's zone width",
    "core": "Core",
    "not_convex": "Zones not convex (outline)",
    "perimeter_parts": "Convex zones of the perimeter zones",
    "core_parts": "Convex zones of the core",
    "merged_parts": "Convex zones of merged pieces",
    "footprint_parts": "Convex zones of the whole footprint",
    "failed_pieces": "Pieces without a decomposition",
    "zone_lines": "Boundaries between the perimeter's zones",
    "removed_lines": "Boundaries a merge or the whole footprint removed",
    "cut_lines": "Cuts of the decomposition",
}
#: the layer of each kind of zone before, and of each kind of piece after
BEFORE_LAYER = {"zone": "perimeter_zones", "thin": "thin_zones", "core": "core"}
AFTER_LAYER = {"zone": "perimeter_parts", "thin": "perimeter_parts", "core": "core_parts", "merged": "merged_parts",
               "footprint": "footprint_parts"}
#: zones narrower or smaller than these are counted (fixed, so that numbers of different settings compare)
NARROW_M = 2.0
SMALL_M2 = 5.0
#: the numbers of a preview, in order: (key, label, unit)
NUMBERS = (
    ("zones_in", "Zones from the perimeter", None),
    ("convex_in", "Zones convex already", None),
    ("failed_first", "Zones the engine could not cut", None),
    ("merges", "Merges", None),
    ("merged_share_pct", "Floor area in merged pieces", "%"),
    ("whole", "Whole footprint decomposed instead", None),
    ("failed", "Pieces left without a decomposition", None),
    ("out_of_time", "Fallback stopped by its time budget", None),
    ("zones", "Convex zones", None),
    ("cuts", "Cuts (each splits a piece in two)", None),
    ("min_width_m", "Narrowest zone", "m"),
    ("narrow", "Zones narrower than 2 m", None),
    ("min_area_m2", "Smallest zone", "m2"),
    ("small", "Zones under 5 m2", None),
    ("compactness", "Mean compactness of the zones", None),
    ("corner_rule_relaxed", "Pieces cut with the corner rule relaxed", None),
    ("searched_again", "Pieces searched deeper or wider", None),
    ("seconds_search", "Search time", "s"),
    ("seconds_discarded", "Search time of attempts not kept", "s"),
)
#: shared boundary shorter than this is not drawn
MIN_LINE_M = 1e-6


def compactness(zone: Polygon) -> float:
    """Polsby-Popper compactness, 4 pi area / perimeter squared: 1 for a circle, 0.785 for a square (the score's)."""
    return 4.0 * math.pi * zone.area / zone.length ** 2 if zone.length > 0.0 else 0.0


def _shared_lines(polygons: list[Polygon]) -> list[LineString]:
    """The boundary each pair of the polygons shares, as lines."""
    if len(polygons) < 2:
        return []
    boundaries = shapely.boundary(polygons)
    left, right = shapely.STRtree(boundaries).query(boundaries, predicate="intersects")
    keep = left < right
    out = []
    for shared in shapely.intersection(boundaries[left[keep]], boundaries[right[keep]]):
        lines = [g for g in shapely.get_parts(shared) if isinstance(g, LineString) and g.length > MIN_LINE_M]
        if lines:
            merged = shapely.line_merge(MultiLineString(lines))
            out.extend(g for g in shapely.get_parts(merged) if g.length > MIN_LINE_M)
    return out


def lines(result: BuildingZones) -> dict[str, list[LineString]]:
    """The boundaries the building's zones keep (``zone_lines``, between pieces), those a merge or the whole
    footprint removed (``removed_lines``, between the zones inside one piece) and the cuts (``cut_lines``, between the
    parts of one piece)."""
    removed = []
    for piece in result.pieces:
        if len(piece.sources) > 1:
            removed += _shared_lines([result.zones[k][0] for k in piece.sources])
    return {
        "zone_lines": _shared_lines([p.polygon for p in result.pieces]),
        "removed_lines": removed,
        "cut_lines": [line for p in result.pieces for line in _shared_lines(p.parts)],
    }


def layers(result: BuildingZones) -> dict[str, list[Any]]:
    """The layers of :data:`LAYERS` for a result."""
    out: dict[str, list[Any]] = {name: [] for names in LAYERS.values() for name in names}
    out["footprint"] = [result.footprint] if result.footprint is not None else []
    for zone, kind in result.zones:
        if kind in BEFORE_LAYER:
            out[BEFORE_LAYER[kind]].append(zone)
            if kind != "core" and not is_convex(zone):
                out["not_convex"].append(zone.exterior)
    for piece in result.pieces:
        if piece.convex:
            out[AFTER_LAYER.get(piece.kind, "perimeter_parts")].extend(piece.parts)
        else:
            out["failed_pieces"].append(piece.polygon)
    return out | lines(result)


def numbers(result: BuildingZones) -> dict[str, Any]:
    """The numbers of :data:`NUMBERS` for a result."""
    zones = [part for part, _ in result.parts]
    compact = [compactness(z) for z in zones]
    widths = [minimum_width(z) for z in zones]
    floor = sum(z.area for z, _ in result.zones) or 1.0
    merged = sum(p.polygon.area for p in result.pieces if p.kind == "merged")
    return {
        "zones_in": len(result.zones),
        "convex_in": sum(is_convex(z) for z, _ in result.zones),
        "failed_first": sum(not p.convex for p in result.first),
        "merges": result.merges,
        "merged_share_pct": round(100.0 * merged / floor, 1),
        "whole": int(result.fallback == "whole"),
        "failed": len(result.failed),
        "out_of_time": int(result.out_of_time),
        "zones": len(zones),
        "cuts": sum(len(p.parts) - 1 for p in result.pieces if p.convex),
        "min_width_m": round(min(widths), 2) if widths else None,
        "narrow": sum(w < NARROW_M for w in widths),
        "min_area_m2": round(min(z.area for z in zones), 2) if zones else None,
        "small": sum(z.area < SMALL_M2 for z in zones),
        "compactness": round(sum(compact) / len(compact), 3) if compact else None,
        "corner_rule_relaxed": sum("sharp_corner" in (p.stats.get("fallback_used") or "") for p in result.pieces),
        "searched_again": sum(int(p.stats.get("search_attempt_count") or 0) > 1 for p in result.pieces),
        "seconds_search": round(result.seconds, 3),
        "seconds_discarded": round(result.discarded_seconds, 3),
    }


def preview(footprint: Polygon, zones: list[tuple[Polygon, str]] | None = None, config: ZoneConfig | None = None,
            *, perimeter: CornerRayConfig | None = None, decompose: Decompose | None = None) -> dict[str, Any]:
    """One building's preview: ``layers`` (:data:`LAYERS`, shapely geometries), ``numbers`` (:data:`NUMBERS`),
    ``fate`` (kept, with the fallback that made it; or dropped with the reason ``decomposition_failed``, whose detail
    says whether the fallback's time ran out, or ``decomposition_error``), ``seconds`` and ``result`` (the
    :class:`~.building.BuildingZones`, None on an error).

    ``zones`` are the perimeter's zones as ``(polygon, kind)`` pairs (``PerimeterResult.zones``, or the zones after
    edits by hand); without them the corner-ray perimeter of ``footprint`` is run first with ``perimeter``, and a
    footprint it cannot zone is decomposed whole (one zone of kind ``footprint``, with no fallback).
    """
    t0 = time.perf_counter()
    if zones is None:
        try:
            zones = list(corner_ray_perimeter(footprint, perimeter).zones)
        except Exception:  # noqa: BLE001 - the perimeter's own page reports why; the footprint is decomposed whole
            zones = [(footprint, "footprint")]
    try:
        result = decompose_zones(zones, config, footprint=footprint, decompose=decompose)
    except Exception as exc:  # noqa: BLE001 - a building the engine stops on is a fate, not a crash
        return {"layers": {"footprint": [footprint]}, "numbers": {}, "result": None,
                "fate": {"status": "dropped", "reason": "decomposition_error",
                         "detail": {"error": f"{type(exc).__name__}: {exc}"[:200]}},
                "seconds": round(time.perf_counter() - t0, 4)}
    if result.ok:
        fate = {"status": "kept", "fallback": result.fallback}
    else:
        fate = {"status": "dropped", "reason": "decomposition_failed",
                "detail": {"pieces": len(result.failed), "kinds": sorted({p.kind for p in result.failed}),
                           "out_of_time": result.out_of_time}}
    return {"layers": layers(result), "numbers": numbers(result), "result": result, "fate": fate,
            "seconds": round(time.perf_counter() - t0, 4)}


def zone_records(result: BuildingZones) -> list[dict[str, Any]]:
    """The convex zones as rows: ``zone`` (its number), ``piece`` (its piece's number), ``kind`` (the piece's: zone,
    thin, core, merged or footprint), ``sources`` (the perimeter's zones it lies in), ``area_m2``, ``min_width_m``,
    ``compactness`` and ``geometry``."""
    return [{"zone": n, "piece": k, "kind": result.pieces[k].kind, "sources": list(result.pieces[k].sources),
             "area_m2": round(z.area, 3), "min_width_m": round(minimum_width(z), 3),
             "compactness": round(compactness(z), 4), "geometry": z}
            for n, (z, k) in enumerate(result.parts)]


def piece_records(result: BuildingZones) -> list[dict[str, Any]]:
    """The pieces kept as rows: ``piece``, ``kind``, ``sources``, ``decomposed``, ``parts``, ``fallback_used`` (the
    engine's relaxed rules), ``attempts`` (searches, deeper or wider), ``dead_ends``, ``terminated_by``,
    ``seconds`` and ``geometry``."""
    return [{"piece": k, "kind": p.kind, "sources": list(p.sources), "decomposed": p.convex, "parts": len(p.parts),
             "fallback_used": p.stats.get("fallback_used"), "attempts": int(p.stats.get("search_attempt_count") or 0),
             "dead_ends": int(p.stats.get("n_failed_terminal_states") or 0),
             "terminated_by": p.stats.get("search_terminated_by"), "seconds": round(p.seconds, 4),
             "geometry": p.polygon}
            for k, p in enumerate(result.pieces)]


__all__ = ["AFTER_LAYER", "BEFORE_LAYER", "LAYERS", "LAYER_TITLES", "NARROW_M", "NUMBERS", "SMALL_M2", "compactness",
           "layers", "lines", "numbers", "piece_records", "preview", "zone_records"]
