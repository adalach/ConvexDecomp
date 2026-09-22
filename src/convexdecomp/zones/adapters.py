"""Build provenance records from the two case-study adapters, or from any pipeline.

``build_part_records`` is the dataset-agnostic entry point: hand it one entry per
polygon that was sent to the decomposer, together with the convex parts that came
back, and it produces the ``container -> space -> zone -> part`` chain used by the
interface matcher. The ResPlan and OSM helpers below are thin mappings onto it.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence

import pandas as pd
from shapely.geometry import Polygon

from convexdecomp.core.convexity import is_convex_polygon
from convexdecomp.zones.records import PartRecord

__all__ = [
    "ZoneSource",
    "build_part_records",
    "resplan_part_records",
    "osm_part_records",
]


class ZoneSource(dict):
    """One decomposed source zone.

    Expected keys:

    - ``container_id``: enclosing entity, e.g. a floorplan or a building.
    - ``space_id``: the physical space the zone belongs to.
    - ``zone_id``: the polygon handed to the decomposer.
    - ``parts``: the convex parts that came back.
    - ``geometry`` (optional): the source zone polygon, used to tell a genuine
      outer wall apart from a segment the matcher simply failed to pair.
    - ``space_label`` / ``zone_role`` (optional): free-text descriptors.
    - ``attributes`` (optional): extra columns copied onto every part.
    """


def build_part_records(sources: Iterable[Mapping[str, Any]]) -> List[PartRecord]:
    """Turn decomposed source zones into flat part records.

    A zone whose decomposition produced no parts is still represented, by its own
    source polygon marked as failed, so the export never silently loses geometry.
    """
    records: List[PartRecord] = []
    for source in sources:
        container_id = str(source["container_id"])
        space_id = str(source["space_id"])
        zone_id = str(source["zone_id"])
        source_geom = source.get("geometry")
        source_geom = source_geom if isinstance(source_geom, Polygon) and not source_geom.is_empty else None

        parts = [
            poly
            for poly in (source.get("parts") or [])
            if isinstance(poly, Polygon) and not poly.is_empty
        ]
        failed = bool(source.get("decomposition_failed", False))
        if not parts:
            if source_geom is None:
                continue
            parts = [source_geom]
            failed = True

        base_attributes: Dict[str, Any] = dict(source.get("attributes") or {})
        for part_index, poly in enumerate(parts):
            attributes = dict(base_attributes)
            attributes["decomposition_failed"] = failed
            attributes["is_convex"] = bool(is_convex_polygon(poly))
            records.append(
                PartRecord(
                    part_id=f"{zone_id}/part_{part_index:02d}",
                    container_id=container_id,
                    space_id=space_id,
                    zone_id=zone_id,
                    geometry=poly,
                    part_index=part_index,
                    space_label=source.get("space_label"),
                    zone_role=source.get("zone_role"),
                    source_zone_geometry=source_geom,
                    attributes=attributes,
                )
            )
    return records


def resplan_part_records(
    room_search_records: Sequence[Mapping[str, Any]],
) -> List[PartRecord]:
    """Build part records from the ResPlan per-room search records.

    Each record describes one room *instance*, identified by its ``room_key`` and
    its index inside that key's polygon list, so two bedrooms in one plan stay two
    separate spaces. ResPlan has no zoning stage, so each space holds exactly one
    zone and every interface inside a space is an ``artificial_split``.
    """
    sources: List[Dict[str, Any]] = []
    for record in room_search_records:
        container_id = str(record.get("plan_id"))
        room_key = str(record.get("room_key"))
        room_index = int(record.get("part_index", 0))
        space_id = f"{container_id}/{room_key}/{room_index:02d}"
        sources.append(
            {
                "container_id": container_id,
                "space_id": space_id,
                "zone_id": space_id,
                "space_label": room_key,
                "zone_role": "room",
                "geometry": record.get("geometry"),
                "parts": record.get("output_parts") or [],
                "decomposition_failed": not bool(record.get("success", True)),
                "attributes": {
                    "room_instance_index": room_index,
                    "source_is_convex": bool(record.get("input_is_convex", False)),
                },
            }
        )
    return build_part_records(sources)


def osm_part_records(polygon_results_gdf: pd.DataFrame) -> List[PartRecord]:
    """Build part records from the OSM per-polygon decomposition results.

    An OSM footprint is one physical envelope, so the whole building is a single
    space and the perimeter, interior, trapezoid and corner pieces are zones
    inside it. Interfaces between those pieces therefore come out as
    ``zoning_split`` while cuts inside one piece come out as
    ``artificial_split``.
    """
    if polygon_results_gdf is None or len(polygon_results_gdf) == 0:
        return []

    sources: List[Dict[str, Any]] = []
    for _, row in polygon_results_gdf.iterrows():
        sample_id = str(row["sample_id"])
        sources.append(
            {
                "container_id": sample_id,
                "space_id": sample_id,
                "zone_id": str(row["zone_id"]),
                "space_label": "building",
                "zone_role": row.get("zone_role"),
                "geometry": row.get("geometry"),
                "parts": list(row.get("best_variant_parts") or []),
                "decomposition_failed": not bool(row.get("fully_convex_best", False)),
                "attributes": {
                    "polygon_idx": int(row.get("polygon_idx", 0) or 0),
                },
            }
        )
    return build_part_records(sources)
