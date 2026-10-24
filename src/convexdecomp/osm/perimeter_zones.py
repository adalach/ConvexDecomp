"""
Corner-ray perimeter zones for a table of OSM building footprints.

Runs :func:`convexdecomp.corner_perimeter.corner_ray_perimeter` on every
footprint and stores its zones in the columns the decomposition helpers of
:mod:`convexdecomp.osm.diagnostics` read: the perimeter zones in
``perimeter_parts`` (with their roles, ``perimeter_zone`` or ``thin_zone``, in
``perimeter_part_roles``) and the core parts in ``interior_geom``. Every zone
then goes to the convex decomposition as it is: convex zones come back as one
part, the others are cut. A footprint the procedure cannot zone keeps
``perimeter_defined = False`` and is decomposed whole.
"""

from __future__ import annotations

import time
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely import union_all
from shapely.geometry import MultiPolygon, Polygon

from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter
from convexdecomp.corner_perimeter.geometry import is_convex

#: the columns :func:`build_corner_perimeter_for_gdf` adds
RESULT_COLUMNS = (
    "perimeter_defined",
    "perimeter",
    "perimeter_only",
    "perimeter_reason",
    "perimeter_parts",
    "perimeter_part_roles",
    "perimeter_geom",
    "interior_geom",
    "n_perimeter_zones",
    "n_thin_zones",
    "n_not_convex_zones",
    "n_core_parts",
    "perimeter_seconds",
    "perimeter_error",
)
ROLES = {"zone": "perimeter_zone", "thin": "thin_zone"}


def _polygonal(parts: list[Polygon]) -> Polygon | MultiPolygon | None:
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def corner_perimeter_record(geom: Polygon, config: CornerRayConfig) -> dict[str, Any]:
    """The perimeter columns of one footprint."""
    t0 = time.perf_counter()
    try:
        result = corner_ray_perimeter(geom, config)
    except Exception as exc:  # noqa: BLE001 - a footprint the procedure cannot zone is decomposed whole
        return {
            "perimeter_defined": False, "perimeter": False, "perimeter_only": False,
            "perimeter_reason": "perimeter_failed", "perimeter_parts": [], "perimeter_part_roles": [],
            "perimeter_geom": None, "interior_geom": None, "n_perimeter_zones": 0, "n_thin_zones": 0,
            "n_not_convex_zones": 0, "n_core_parts": 0, "perimeter_seconds": time.perf_counter() - t0,
            "perimeter_error": f"{type(exc).__name__}: {exc}"[:200],
        }
    zones = [(z, k) for z, k in result.zones if k != "core"]
    core = [z for z, k in result.zones if k == "core"]
    return {
        "perimeter_defined": True,
        "perimeter": bool(core),
        "perimeter_only": not core,
        "perimeter_reason": "perimeter_with_core" if core else "perimeter_only_no_core",
        "perimeter_parts": [z for z, _ in zones],
        "perimeter_part_roles": [ROLES[k] for _, k in zones],
        "perimeter_geom": union_all([z for z, _ in zones]) if zones else None,
        "interior_geom": _polygonal(core),
        "n_perimeter_zones": len(zones),
        "n_thin_zones": sum(k == "thin" for _, k in zones),
        "n_not_convex_zones": sum(not is_convex(z) for z, _ in zones),
        "n_core_parts": len(core),
        "perimeter_seconds": time.perf_counter() - t0,
        "perimeter_error": None,
    }


def build_corner_perimeter_for_gdf(
    gdf: gpd.GeoDataFrame,
    *,
    geometry_col: str = "geometry",
    config: CornerRayConfig | None = None,
) -> gpd.GeoDataFrame:
    """gdf with the corner-ray perimeter of every footprint in :data:`RESULT_COLUMNS` (earlier perimeter columns
    of those names are replaced)."""
    config = config or CornerRayConfig()
    out = gdf.drop(columns=list(RESULT_COLUMNS), errors="ignore")
    records = [corner_perimeter_record(geom, config) for geom in out[geometry_col]]
    return out.join(pd.DataFrame(records, index=out.index))


def summarize_corner_perimeter(gdf: pd.DataFrame) -> dict[str, Any]:
    """Building and zone counts of :func:`build_corner_perimeter_for_gdf`'s result."""
    defined = gdf["perimeter_defined"].fillna(False).astype(bool)
    return {
        "n_total": int(len(gdf)),
        "n_with_core": int(gdf["perimeter"].fillna(False).astype(bool).sum()),
        "n_perimeter_only": int(gdf["perimeter_only"].fillna(False).astype(bool).sum()),
        "n_failed": int((~defined).sum()),
        "n_perimeter_zones": int(gdf["n_perimeter_zones"].sum()),
        "n_thin_zones": int(gdf["n_thin_zones"].sum()),
        "n_buildings_with_thin_zones": int((gdf["n_thin_zones"] > 0).sum()),
        "n_not_convex_zones": int(gdf["n_not_convex_zones"].sum()),
        "n_buildings_with_not_convex_zones": int((gdf["n_not_convex_zones"] > 0).sum()),
        "n_core_parts": int(gdf["n_core_parts"].sum()),
        "seconds_total": float(gdf["perimeter_seconds"].sum()),
        "seconds_max": float(gdf["perimeter_seconds"].max()) if len(gdf) else 0.0,
        "reason_counts": gdf["perimeter_reason"].fillna("missing").value_counts().to_dict(),
    }


__all__ = ["RESULT_COLUMNS", "build_corner_perimeter_for_gdf", "corner_perimeter_record",
           "summarize_corner_perimeter"]
