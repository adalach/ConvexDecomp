"""Record types and boundary vocabulary shared by the zone provenance helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from shapely.geometry import LineString, Polygon

__all__ = [
    "BOUNDARY_KINDS",
    "VIRTUAL_BOUNDARY_KINDS",
    "BOUNDARY_KIND_LABELS",
    "PartRecord",
    "InterfaceRecord",
]

#: Vocabulary used for every emitted boundary sub-segment.
#:
#: The kind is decided purely by comparing the provenance chain of the two parts
#: that share the sub-segment, so the same vocabulary applies to a single room,
#: a single building, or a whole neighborhood.
BOUNDARY_KINDS: Tuple[str, ...] = (
    "exterior",           # no neighbouring part; lies on the source zone boundary
    "artificial_split",   # neighbour is a sibling part of the same source zone
    "zoning_split",       # neighbour belongs to another zone of the same space
    "space_boundary",     # neighbour belongs to another space of the same container
    "container_boundary", # neighbour belongs to another container (e.g. party wall)
    "unmatched",          # no neighbour and not on the source zone boundary
)

#: Kinds that correspond to a wall which does not physically exist in the source
#: geometry. ``artificial_split`` is purely an artifact of convex decomposition;
#: ``zoning_split`` is a deliberate thermal-zoning cut such as perimeter/interior.
VIRTUAL_BOUNDARY_KINDS: Tuple[str, ...] = ("artificial_split", "zoning_split")

BOUNDARY_KIND_LABELS: Dict[str, str] = {
    "exterior": "outer boundary of the decomposed source zone",
    "artificial_split": "convex-decomposition cut inside one source zone",
    "zoning_split": "deliberate zoning cut between zones of one space",
    "space_boundary": "boundary between two different spaces",
    "container_boundary": "boundary between two different containers",
    "unmatched": "no neighbour found and not on the source boundary",
}


@dataclass(frozen=True)
class PartRecord:
    """One convex part together with the provenance chain it came from.

    The chain is ``container -> space -> zone -> part`` and is intentionally
    generic:

    - ``container_id``: the enclosing dataset entity, e.g. one ResPlan floorplan
      or one OSM building.
    - ``space_id``: one physical space instance. Two bedrooms in the same plan
      must get two different ``space_id`` values even though they share a label.
    - ``zone_id``: the polygon that was actually handed to the decomposer. It
      equals the space when no zoning stage runs, and identifies the perimeter
      or interior piece when it does.
    - ``part_id``: one convex output part.
    """

    part_id: str
    container_id: str
    space_id: str
    zone_id: str
    geometry: Polygon
    part_index: int = 0
    space_label: Optional[str] = None
    zone_role: Optional[str] = None
    source_zone_geometry: Optional[Polygon] = None
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InterfaceRecord:
    """One boundary sub-segment of one convex part.

    Records are emitted per part, so a shared wall appears twice, once from each
    side. ``interface_key`` is identical for both sides, which makes it easy to
    collapse the table to unique walls or to join the two halves.
    """

    interface_id: str
    interface_key: str
    part_id: str
    container_id: str
    space_id: str
    zone_id: str
    boundary_kind: str
    geometry: LineString
    length: float
    neighbor_part_id: Optional[str] = None
    neighbor_space_id: Optional[str] = None
    neighbor_zone_id: Optional[str] = None
    neighbor_container_id: Optional[str] = None
    same_space_group: bool = False
    is_virtual: bool = False
    is_ambiguous: bool = False
