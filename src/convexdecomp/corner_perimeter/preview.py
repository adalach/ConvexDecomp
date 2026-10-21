"""What a settings page draws and counts for one building: the layers before and after the perimeter, the numbers
that show what the rules did, and the building's fate.

:func:`preview` runs :func:`~.procedure.corner_ray_perimeter` and never raises: a footprint the procedure cannot
zone comes back with ``fate = {"status": "dropped", "reason": "perimeter_failed", ...}``. The layers are shapely
geometries in the footprint's own frame (metres); a caller that sends them elsewhere converts them (WKB, GeoJSON).
:func:`zone_records` gives the zones as rows of a long table.
"""

from __future__ import annotations

import time
from typing import Any

from shapely.geometry import MultiPoint, Point, Polygon

from convexdecomp.core.scores import minimum_width

from .geometry import is_convex
from .procedure import PerimeterResult, corner_ray_perimeter
from .settings import CornerRayConfig

#: the layers drawn before and after the perimeter, in drawing order (later on top)
LAYERS = {
    "before": ("footprint",),
    "after": ("perimeter_zones", "thin_zones", "core", "not_convex", "band_line", "rays", "merged_rays",
              "corner_lines", "wing_ends"),
}
#: what each layer is, in words
LAYER_TITLES = {
    "footprint": "Footprint",
    "perimeter_zones": "Perimeter zones",
    "thin_zones": "Zones thinner than the zone width",
    "core": "Core",
    "not_convex": "Zones not convex (outline)",
    "band_line": "Band line (with the lines the narrow core became)",
    "rays": "Corner rays",
    "merged_rays": "Rule 1: merged rays",
    "corner_lines": "Rule 2: corner-to-corner lines",
    "wing_ends": "Wing-end corners without rays",
}
#: the numbers of a preview, in order: (key, label, unit)
NUMBERS = (
    ("corners", "Corners", None),
    ("wing_end_corners", "Wing-end corners without rays", None),
    ("zones", "Perimeter zones", None),
    ("core_parts", "Core parts", None),
    ("core_share_pct", "Core share of the floor area", "%"),
    ("thin", "Zones thinner than the zone width", None),
    ("not_convex", "Zones not convex", None),
    ("sharp_20", "Zone corners under 20 degrees", None),
    ("specks", "Zones under 0.5 m2", None),
    ("min_width_m", "Narrowest perimeter zone", "m"),
    ("merged_rays", "Rule 1: merged rays", None),
    ("corner_lines", "Rule 2: corner-to-corner lines", None),
    ("rays_discarded", "Rule 3: rays discarded", None),
    ("legs", "Rule 4: legs to lines", None),
    ("notches", "Rule 4: notches filled", None),
    ("narrow_parts", "Rule 4: narrow parts to lines", None),
    ("points_merged", "Rule 5: groups of close points", None),
    ("diagonals", "Rule 6: narrow zones replaced by a diagonal", None),
    ("corner_groups", "Rule 5 again: corner groups", None),
    ("tiny_merged", "Rule 7: groups of tiny zones merged", None),
    ("lone_merged", "Rule 8: lone tiny zones merged", None),
    ("sharp_cuts", "Rule 9: sharp corners cut", None),
)


def layers(result: PerimeterResult) -> dict[str, list[Any]]:
    """The layers of :data:`LAYERS` for a result."""
    zones = result.zones
    return {
        "footprint": [result.poly],
        "perimeter_zones": [z for z, k in zones if k == "zone"],
        "thin_zones": [z for z, k in zones if k == "thin"],
        "core": [z for z, k in zones if k == "core"],
        "not_convex": [z.exterior for z, k in zones if k != "core" and not is_convex(z)],
        "band_line": [] if result.band_line.is_empty else [result.band_line],
        "rays": [ln.geom for ln in result.lines if ln.kind == "ray"],
        "merged_rays": [ln.geom for ln in result.lines if ln.kind == "apex"],
        "corner_lines": [ln.geom for ln in result.lines if ln.kind == "merged"],
        "wing_ends": [MultiPoint([Point(p) for p in result.wing_ends])] if result.wing_ends else [],
    }


def numbers(result: PerimeterResult) -> dict[str, Any]:
    """The numbers of :data:`NUMBERS` for a result."""
    counts = result.counts()
    pz = result.perimeter_zones
    core_area = sum(z.area for z in result.core_parts)
    return {key: counts.get(key) for key, _, _ in NUMBERS} | {
        "core_share_pct": round(100.0 * core_area / result.poly.area, 1) if result.poly.area else 0.0,
        "min_width_m": round(min(minimum_width(z) for z in pz), 2) if pz else None,
    }


def preview(poly: Polygon, config: CornerRayConfig | None = None, upto: str | None = None) -> dict[str, Any]:
    """One building's preview: ``layers`` (:data:`LAYERS`, shapely geometries), ``numbers`` (:data:`NUMBERS`),
    ``fate`` (kept, or dropped with the reason ``perimeter_failed`` and the error), ``seconds`` and, when kept,
    ``result`` (the :class:`~.procedure.PerimeterResult`)."""
    t0 = time.perf_counter()
    try:
        result = corner_ray_perimeter(poly, config, upto=upto)
    except Exception as exc:  # noqa: BLE001 - a footprint the procedure cannot zone is a fate, not a crash
        return {"layers": {"footprint": [poly]}, "numbers": {}, "result": None,
                "fate": {"status": "dropped", "reason": "perimeter_failed",
                         "detail": {"error": f"{type(exc).__name__}: {exc}"[:200]}},
                "seconds": round(time.perf_counter() - t0, 4)}
    return {"layers": layers(result), "numbers": numbers(result), "result": result, "fate": {"status": "kept"},
            "seconds": round(time.perf_counter() - t0, 4)}


def zone_records(result: PerimeterResult) -> list[dict[str, Any]]:
    """The zones as rows: ``zone`` (its number), ``kind`` (zone, thin or core), ``area_m2``, ``min_width_m``,
    ``convex`` and ``geometry``."""
    return [{"zone": n, "kind": k, "area_m2": round(z.area, 3), "min_width_m": round(minimum_width(z), 3),
             "convex": is_convex(z), "geometry": z} for n, (z, k) in enumerate(result.zones)]


__all__ = ["LAYERS", "LAYER_TITLES", "NUMBERS", "layers", "numbers", "preview", "zone_records"]
