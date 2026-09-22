"""Classify every boundary sub-segment of a set of convex parts.

Neighbouring convex parts rarely share an identical edge: a straight cut through
a polygon usually leaves one part with a long edge and the other with a shorter
collinear piece of it. The matcher therefore groups edges into collinear line
clusters, intersects them as 1D intervals, and splits every edge at the
resulting breakpoints so that each emitted sub-segment has at most one
neighbour.

Segments without a neighbour are only reported as ``exterior`` when they can be
shown to lie on the boundary of the source zone that was decomposed. Otherwise
they are reported as ``unmatched``, because the decomposition is not guaranteed
to produce a perfect planar tiling and silently calling a gap an outer wall
would be misleading downstream.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient

from convexdecomp.zones.records import (
    VIRTUAL_BOUNDARY_KINDS,
    InterfaceRecord,
    PartRecord,
)

__all__ = [
    "InterfaceConfig",
    "build_interfaces",
    "summarize_interfaces",
    "normalize_part_geometry",
]


@dataclass(frozen=True)
class InterfaceConfig:
    """Tolerances for the collinear-overlap matcher.

    All lengths are in the coordinate unit of the input geometry, which is
    metres for both case-study adapters.
    """

    #: Maximum perpendicular distance for two edges to count as collinear, and
    #: maximum distance from a segment midpoint to the source zone boundary.
    tol: float = 1e-6
    #: Maximum angle between two edge directions for them to count as parallel.
    angle_tol_deg: float = 0.05
    #: Sub-segments shorter than this are dropped instead of being reported.
    min_segment_length: float = 1e-9
    #: Decimal places used when rounding midpoints into the symmetric
    #: ``interface_key``. Must be coarse enough to survive the tolerance.
    key_ndigits: int = 6


def normalize_part_geometry(poly: Polygon) -> Polygon:
    """Return the polygon with a counter-clockwise exterior ring."""
    return orient(poly, sign=1.0)


def _edge_table(parts: Sequence[PartRecord], cfg: InterfaceConfig) -> List[Dict[str, Any]]:
    """Flatten every part exterior ring into individual directed edges."""
    edges: List[Dict[str, Any]] = []
    for part_idx, part in enumerate(parts):
        geom = part.geometry
        if not isinstance(geom, Polygon) or geom.is_empty:
            continue
        coords = list(normalize_part_geometry(geom).exterior.coords)
        for edge_idx in range(len(coords) - 1):
            p0 = np.asarray(coords[edge_idx], dtype=float)
            p1 = np.asarray(coords[edge_idx + 1], dtype=float)
            vec = p1 - p0
            length = float(np.hypot(*vec))
            if length <= cfg.min_segment_length:
                continue
            edges.append(
                {
                    "part_idx": part_idx,
                    "edge_idx": edge_idx,
                    "p0": p0,
                    "p1": p1,
                    "dir": vec / length,
                }
            )
    return edges


@dataclass
class _LineCluster:
    """A set of edges that all lie on one infinite line."""

    direction: np.ndarray
    origin: np.ndarray
    edge_indices: List[int]

    def project(self, point: np.ndarray) -> float:
        return float(np.dot(self.direction, point))

    def point_at(self, t: float) -> Tuple[float, float]:
        base = self.origin - self.direction * float(np.dot(self.direction, self.origin))
        pt = base + self.direction * t
        return float(pt[0]), float(pt[1])

    def perpendicular_distance(self, point: np.ndarray) -> float:
        normal = np.array([-self.direction[1], self.direction[0]], dtype=float)
        return abs(float(np.dot(normal, point - self.origin)))


def _cluster_edges(edges: Sequence[Dict[str, Any]], cfg: InterfaceConfig) -> List[_LineCluster]:
    """Group edges of one container into collinear line clusters.

    Architectural geometry has few distinct wall lines per container, so a
    linear scan over cluster representatives stays cheap while avoiding the
    bucket-boundary and angle-wraparound problems of a rounded hash key.
    """
    sin_tol = math.sin(math.radians(cfg.angle_tol_deg))
    clusters: List[_LineCluster] = []

    for index, edge in enumerate(edges):
        direction = edge["dir"]
        p0 = edge["p0"]
        placed = False
        for cluster in clusters:
            cross = abs(float(direction[0] * cluster.direction[1] - direction[1] * cluster.direction[0]))
            if cross > sin_tol:
                continue
            if cluster.perpendicular_distance(p0) > cfg.tol:
                continue
            if cluster.perpendicular_distance(edge["p1"]) > cfg.tol:
                continue
            cluster.edge_indices.append(index)
            placed = True
            break
        if not placed:
            clusters.append(_LineCluster(direction=direction.copy(), origin=p0.copy(), edge_indices=[index]))
    return clusters


def _classify(part: PartRecord, neighbor: PartRecord) -> str:
    if neighbor.container_id != part.container_id:
        return "container_boundary"
    if neighbor.space_id != part.space_id:
        return "space_boundary"
    if neighbor.zone_id != part.zone_id:
        return "zoning_split"
    return "artificial_split"


def _on_source_boundary(part: PartRecord, midpoint: Tuple[float, float], cfg: InterfaceConfig) -> Optional[bool]:
    source = part.source_zone_geometry
    if not isinstance(source, Polygon) or source.is_empty:
        return None
    return bool(source.boundary.distance(Point(midpoint)) <= cfg.tol)


def _interface_key(
    part: PartRecord,
    neighbor: Optional[PartRecord],
    midpoint: Tuple[float, float],
    cfg: InterfaceConfig,
) -> str:
    mid = f"{round(midpoint[0], cfg.key_ndigits)}_{round(midpoint[1], cfg.key_ndigits)}"
    if neighbor is None:
        return f"{part.part_id}|-|{mid}"
    left, right = sorted((part.part_id, neighbor.part_id))
    return f"{left}|{right}|{mid}"


def build_interfaces(
    parts: Sequence[PartRecord],
    cfg: InterfaceConfig | None = None,
) -> List[InterfaceRecord]:
    """Return one record per boundary sub-segment of every part.

    A shared wall yields two records, one per side, carrying the same
    ``interface_key``. Parts are only ever matched against other parts of the
    same ``container_id``, so a district-scale caller that wants neighbouring
    buildings compared must put them in one container and separate them by
    ``space_id``.
    """
    cfg = cfg or InterfaceConfig()
    parts = [part for part in parts if isinstance(part.geometry, Polygon) and not part.geometry.is_empty]
    if not parts:
        return []

    edges = _edge_table(parts, cfg)
    if not edges:
        return []

    by_container: Dict[str, List[int]] = {}
    for index, edge in enumerate(edges):
        by_container.setdefault(parts[edge["part_idx"]].container_id, []).append(index)

    records: List[InterfaceRecord] = []
    face_counters: Dict[str, int] = {}
    for container_edges in by_container.values():
        local_edges = [edges[i] for i in container_edges]
        for cluster in _cluster_edges(local_edges, cfg):
            _emit_cluster_records(cluster, local_edges, parts, cfg, records, face_counters)
    return records


def _emit_cluster_records(
    cluster: _LineCluster,
    edges: Sequence[Dict[str, Any]],
    parts: Sequence[PartRecord],
    cfg: InterfaceConfig,
    out: List[InterfaceRecord],
    face_counters: Dict[str, int],
) -> None:
    intervals = []
    for index in cluster.edge_indices:
        edge = edges[index]
        t_start = cluster.project(edge["p0"])
        t_end = cluster.project(edge["p1"])
        intervals.append((min(t_start, t_end), max(t_start, t_end), t_start <= t_end, index))

    for low, high, forward, index in intervals:
        edge = edges[index]
        part = parts[edge["part_idx"]]

        overlaps = []
        for other_low, other_high, _, other_index in intervals:
            if other_index == index:
                continue
            if parts[edges[other_index]["part_idx"]].part_id == part.part_id:
                continue
            start = max(low, other_low)
            end = min(high, other_high)
            if end - start > cfg.min_segment_length:
                overlaps.append((start, end, parts[edges[other_index]["part_idx"]]))

        breakpoints = {low, high}
        for start, end, _ in overlaps:
            breakpoints.add(start)
            breakpoints.add(end)
        ordered = sorted(breakpoints)

        for t_a, t_b in zip(ordered[:-1], ordered[1:]):
            if t_b - t_a <= cfg.min_segment_length:
                continue
            middle = 0.5 * (t_a + t_b)
            covering = [
                neighbor
                for start, end, neighbor in overlaps
                if start <= middle <= end
            ]
            covering.sort(key=lambda neighbor: neighbor.part_id)

            point_a = cluster.point_at(t_a)
            point_b = cluster.point_at(t_b)
            if not forward:
                point_a, point_b = point_b, point_a
            geometry = LineString([point_a, point_b])
            if geometry.length <= cfg.min_segment_length:
                continue
            midpoint = cluster.point_at(middle)

            neighbor = covering[0] if covering else None
            if neighbor is None:
                on_boundary = _on_source_boundary(part, midpoint, cfg)
                boundary_kind = "unmatched" if on_boundary is False else "exterior"
            else:
                boundary_kind = _classify(part, neighbor)

            index_in_part = face_counters.get(part.part_id, 0)
            face_counters[part.part_id] = index_in_part + 1
            out.append(
                InterfaceRecord(
                    interface_id=f"{part.part_id}/face_{index_in_part:04d}",
                    interface_key=_interface_key(part, neighbor, midpoint, cfg),
                    part_id=part.part_id,
                    container_id=part.container_id,
                    space_id=part.space_id,
                    zone_id=part.zone_id,
                    boundary_kind=boundary_kind,
                    geometry=geometry,
                    length=float(geometry.length),
                    neighbor_part_id=neighbor.part_id if neighbor else None,
                    neighbor_space_id=neighbor.space_id if neighbor else None,
                    neighbor_zone_id=neighbor.zone_id if neighbor else None,
                    neighbor_container_id=neighbor.container_id if neighbor else None,
                    same_space_group=bool(neighbor and neighbor.space_id == part.space_id),
                    is_virtual=boundary_kind in VIRTUAL_BOUNDARY_KINDS,
                    is_ambiguous=len(covering) > 1,
                )
            )


def summarize_interfaces(
    parts: Sequence[PartRecord],
    interfaces: Sequence[InterfaceRecord],
) -> Dict[str, Any]:
    """Aggregate counts and lengths per boundary kind, plus tiling diagnostics.

    ``n_unmatched`` and ``n_ambiguous`` are the two numbers worth watching: they
    say how far the decomposition output is from a clean planar tiling and
    whether ``InterfaceConfig.tol`` needs loosening for the dataset at hand.
    """
    kind_counts: Dict[str, int] = {}
    kind_lengths: Dict[str, float] = {}
    for record in interfaces:
        kind_counts[record.boundary_kind] = kind_counts.get(record.boundary_kind, 0) + 1
        kind_lengths[record.boundary_kind] = kind_lengths.get(record.boundary_kind, 0.0) + record.length

    space_ids = {part.space_id for part in parts}
    seen: Dict[str, int] = {}
    for part in parts:
        seen[part.space_id] = seen.get(part.space_id, 0) + 1
    split_spaces = {space_id for space_id, count in seen.items() if count > 1}

    return {
        "n_parts": len(parts),
        "n_spaces": len(space_ids),
        "n_split_spaces": len(split_spaces),
        "n_interfaces": len(interfaces),
        "n_virtual_interfaces": sum(1 for record in interfaces if record.is_virtual),
        "n_unmatched": kind_counts.get("unmatched", 0),
        "n_ambiguous": sum(1 for record in interfaces if record.is_ambiguous),
        "interface_counts_by_kind": kind_counts,
        "interface_length_by_kind": kind_lengths,
    }
