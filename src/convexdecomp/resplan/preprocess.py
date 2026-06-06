from __future__ import annotations

import math
import warnings
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from convexdecomp.core.convexity import is_convex_polygon, raw_ring_key
from convexdecomp.resplan.plan_helpers import normalize_keys, rescale_plan

try:
    from shapely import make_valid as _make_valid
except Exception:
    try:
        from shapely.validation import make_valid as _make_valid
    except Exception:
        _make_valid = None

__all__ = [
    "annotate_plans_with_wall_thickness",
    "clean_raw_room_vertices",
    "clean_room_plan_geometries",
    "close_narrow_parallel_gaps",
    "count_plan_room_polygons",
    "count_room_vertices",
    "estimate_plan_wall_thickness_m",
    "explode_polygon_parts",
    "grow_rooms_by_stored_wall_thickness",
    "is_degenerate_polygon",
    "iter_plan_room_polygons",
    "open_concave_room_polygons",
    "offset_rooms_into_walls",
    "plan_room_polygons",
    "reduce_to_room_layer_plans",
    "reduce_to_room_layers",
    "scaled_room_polygons_above_area",
    "rescale_floorplans",
    "simplify_polygon_vertices",
    "split_room_plans",
]


def explode_polygon_parts(value: Any) -> List[Polygon]:
    if isinstance(value, Polygon):
        return [] if value.is_empty else [value]
    if isinstance(value, MultiPolygon):
        return [poly for poly in value.geoms if isinstance(poly, Polygon) and not poly.is_empty]
    if isinstance(value, (list, tuple)):
        out: List[Polygon] = []
        for item in value:
            if isinstance(item, BaseGeometry) or isinstance(item, (list, tuple)) or hasattr(item, "geoms"):
                out.extend(explode_polygon_parts(item))
        return out
    geoms = getattr(value, "geoms", None)
    if geoms is not None:
        out = []
        for item in geoms:
            out.extend(explode_polygon_parts(item))
        return out
    return []


def iter_plan_room_polygons(
    plan: Dict[str, Any],
    room_keys: Iterable[str],
    *,
    with_labels: bool = False,
) -> Iterator[Polygon] | Iterator[Tuple[str, Polygon]]:
    for room_key in room_keys:
        for poly in explode_polygon_parts(plan.get(room_key)):
            if with_labels:
                yield room_key, poly
            else:
                yield poly


def plan_room_polygons(plan: Dict[str, Any], room_keys: Iterable[str]) -> List[Polygon]:
    return list(iter_plan_room_polygons(plan, room_keys))


def count_plan_room_polygons(plan: Dict[str, Any], room_keys: Iterable[str]) -> int:
    return sum(1 for _ in iter_plan_room_polygons(plan, room_keys))


def count_room_vertices(dataset: Sequence[Dict[str, Any]], room_keys: Iterable[str]) -> int:
    return sum(
        len(list(poly.exterior.coords))
        for plan in dataset
        for poly in iter_plan_room_polygons(plan, room_keys)
    )


def scaled_room_polygons_above_area(
    plan: Dict[str, Any],
    room_keys: Iterable[str],
    scale_factor: float,
    min_area_m2: float,
) -> List[Polygon]:
    area_scale = float(scale_factor) * float(scale_factor)
    return [
        poly
        for poly in iter_plan_room_polygons(plan, room_keys)
        if poly.area * area_scale >= min_area_m2
    ]


def rescale_floorplans(
    floorplans: Sequence[Dict[str, Any]],
    scale_factors: Sequence[float],
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """Rescale floorplans into metric coordinates and preserve wall metadata."""
    rows: list[dict[str, Any]] = []
    out_plans: list[dict[str, Any]] = []

    for idx, (plan, scale_factor) in enumerate(zip(floorplans, scale_factors)):
        rescaled = rescale_plan(plan, scale_factor)
        wall_depth = plan.get("wall_depth")
        wall_depth_m = None if wall_depth is None else float(wall_depth) * float(scale_factor)

        out_plan = normalize_keys(rescaled)
        if wall_depth_m is not None:
            out_plan["wall_depth"] = wall_depth_m
        out_plans.append(out_plan)

        rows.append(
            {
                "plan_idx": idx,
                "plan_id": out_plan.get("id"),
                "scale_factor": float(scale_factor),
                "wall_depth_m": wall_depth_m,
            }
        )

    return out_plans, pd.DataFrame(rows)


def split_room_plans(
    room_plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    min_area_m2: float,
) -> list[dict[str, Any]]:
    """Explode multipart room geometries into per-room polygon lists."""
    split_floorplans: list[dict[str, Any]] = []
    for plan in room_plans:
        split_plan: dict[str, Any] = {"id": plan.get("id")}
        for key in room_keys:
            polys = [poly for poly in explode_polygon_parts(plan.get(key)) if poly.area >= min_area_m2]
            if polys:
                split_plan[key] = polys
        split_floorplans.append(split_plan)
    return split_floorplans


def reduce_to_room_layer_plans(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    keep_keys: Iterable[str] = ("id",),
) -> list[dict[str, Any]]:
    return reduce_to_room_layers(plans, room_keys, keep_keys=tuple(keep_keys))


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _vertex_tol_from_bounds(bounds, rel_tol: float, abs_tol: float) -> float:
    minx, miny, maxx, maxy = bounds
    scale = max(maxx - minx, maxy - miny, 1.0)
    return max(abs_tol, rel_tol * scale)


def _simplify_ring(coords, tol: float) -> List[Tuple[float, float]]:
    pts = list(coords)
    if not pts:
        return pts
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4:
        return pts

    deduped = [pts[0]]
    for point in pts[1:]:
        if _dist(point, deduped[-1]) > tol:
            deduped.append(point)
    if deduped[0] != deduped[-1]:
        deduped.append(deduped[0])
    if len(deduped) < 4:
        return pts

    work = deduped[:-1]
    changed = True
    while changed and len(work) > 3:
        changed = False
        keep = []
        n = len(work)
        for i in range(n):
            a, b, c = work[(i - 1) % n], work[i], work[(i + 1) % n]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            scale = max(_dist(a, b), _dist(b, c), 1.0)
            if abs(cross) > 1e-9 * scale:
                keep.append(b)
                continue

            changed = True
        if len(keep) < 3:
            return deduped
        work = keep

    if work and work[0] != work[-1]:
        work.append(work[0])
    return work if len(work) >= 4 else deduped


def simplify_polygon_vertices(poly: Polygon, tol: float = 1e-9) -> Polygon:
    if not isinstance(poly, Polygon) or poly.is_empty:
        return poly
    shell = _simplify_ring(poly.exterior.coords, tol)
    holes = []
    for ring in poly.interiors:
        hole = _simplify_ring(ring.coords, tol)
        if len(hole) >= 4:
            holes.append(hole)
    try:
        cleaned = Polygon(shell, holes)
        if not cleaned.is_valid:
            cleaned = cleaned.buffer(0)
        return cleaned if isinstance(cleaned, Polygon) and not cleaned.is_empty else poly
    except Exception:
        return poly


def is_degenerate_polygon(poly: Polygon, tol: float = 1e-9) -> bool:
    return poly.is_empty or (not poly.is_valid) or poly.area <= tol or poly.convex_hull.area <= tol


def _clean_raw_room_geometry(geom: Any, rel_tol: float, abs_tol: float) -> Tuple[Any, int]:
    if isinstance(geom, Polygon):
        tol = _vertex_tol_from_bounds(geom.bounds, rel_tol, abs_tol)
        before = len(list(geom.exterior.coords))
        cleaned = simplify_polygon_vertices(geom, tol=tol)
        after = len(list(cleaned.exterior.coords)) if isinstance(cleaned, Polygon) and not cleaned.is_empty else before
        return cleaned, max(before - after, 0)
    if isinstance(geom, MultiPolygon):
        cleaned_parts = []
        removed = 0
        for poly in geom.geoms:
            if not isinstance(poly, Polygon) or poly.is_empty:
                continue
            cleaned, delta = _clean_raw_room_geometry(poly, rel_tol, abs_tol)
            removed += delta
            if isinstance(cleaned, Polygon) and not cleaned.is_empty:
                cleaned_parts.append(cleaned)
        if not cleaned_parts:
            return geom, removed
        if len(cleaned_parts) == 1:
            return cleaned_parts[0], removed
        return MultiPolygon(cleaned_parts), removed
    return geom, 0


def clean_raw_room_vertices(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    rel_tol: float = 1e-5,
    abs_tol: float = 1e-6,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    cleaned_plans: List[Dict[str, Any]] = []
    changed_room_geometries = 0
    before_vertices = count_room_vertices(plans, room_keys)

    for plan in plans:
        out = normalize_plan(dict(plan)) if normalize_plan is not None else dict(plan)
        for room_key in room_keys:
            cleaned_geom, removed_vertices = _clean_raw_room_geometry(out.get(room_key), rel_tol, abs_tol)
            if removed_vertices > 0:
                changed_room_geometries += 1
            out[room_key] = cleaned_geom
        cleaned_plans.append(out)

    after_vertices = count_room_vertices(cleaned_plans, room_keys)
    stats = {
        "before_vertices": int(before_vertices),
        "after_vertices": int(after_vertices),
        "removed_vertices": int(before_vertices - after_vertices),
        "changed_room_geometries": int(changed_room_geometries),
    }
    return cleaned_plans, stats


def _safe_buffer(geom: BaseGeometry, distance: float, *, join_style: Optional[int] = 2):
    if geom is None or not isinstance(geom, BaseGeometry) or geom.is_empty:
        return None
    kwargs = {}
    if join_style is not None:
        kwargs["join_style"] = join_style
    # Invalid intermediate room geometries are common here, so buffering should fail soft.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            return geom.buffer(distance, **kwargs)
        except Exception:
            return None


def repair_polygonal(geom: Any):
    if geom is None or not isinstance(geom, BaseGeometry) or geom.is_empty:
        return None
    work = geom
    if _make_valid is not None:
        try:
            work = _make_valid(work)
        except Exception:
            pass
    if isinstance(work, BaseGeometry) and not work.is_empty and not work.is_valid:
        buffered = _safe_buffer(work, 0.0, join_style=None)
        if buffered is not None:
            work = buffered
    parts = [poly for poly in explode_polygon_parts(work) if poly.area > 1e-6]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    try:
        return MultiPolygon(parts)
    except Exception:
        try:
            return repair_polygonal(unary_union(parts))
        except Exception:
            return parts[0]


def _polygonal_union(geoms: Iterable[Any]):
    parts: List[Polygon] = []
    for geom in geoms:
        repaired = repair_polygonal(geom)
        if repaired is not None:
            parts.extend(explode_polygon_parts(repaired))
    if not parts:
        return None
    try:
        return repair_polygonal(unary_union(parts))
    except Exception:
        if len(parts) == 1:
            return parts[0]
        try:
            return MultiPolygon(parts)
        except Exception:
            return parts[0]


def _polygonal_overlay(lhs: Any, rhs: Any, op: str):
    lhs = repair_polygonal(lhs)
    rhs = repair_polygonal(rhs)
    if lhs is None:
        return None
    if rhs is None:
        return lhs if op == "difference" else None
    try:
        result = getattr(lhs, op)(rhs)
    except Exception:
        lhs0 = _safe_buffer(lhs, 0.0, join_style=None)
        rhs0 = _safe_buffer(rhs, 0.0, join_style=None)
        if lhs0 is None or rhs0 is None:
            return lhs if op == "difference" else None
        try:
            result = getattr(lhs0, op)(rhs0)
        except Exception:
            return lhs if op == "difference" else None
    return repair_polygonal(result)


def _wall_part_thickness_m(poly: Polygon, min_thickness_m: float, max_thickness_m: float) -> Optional[float]:
    rect = poly.minimum_rotated_rectangle
    if not isinstance(rect, Polygon) or rect.is_empty:
        return None
    coords = np.asarray(rect.exterior.coords[:-1], dtype=float)
    if len(coords) < 4:
        return None
    edge_lengths = np.linalg.norm(np.roll(coords, -1, axis=0) - coords, axis=1)
    edge_lengths = edge_lengths[edge_lengths > 1e-9]
    if edge_lengths.size == 0:
        return None
    thickness = float(edge_lengths.min())
    if min_thickness_m <= thickness <= max_thickness_m:
        return thickness
    return None


def estimate_plan_wall_thickness_m(
    plan: Dict[str, Any],
    *,
    max_wall_parts: int = 10,
    min_thickness_m: float = 0.05,
    max_thickness_m: float = 1.0,
) -> Tuple[float, str]:
    meta_thickness = plan.get("wall_thickness_m_estimate")
    if meta_thickness is not None:
        try:
            meta_thickness = float(meta_thickness)
        except Exception:
            meta_thickness = None
    if meta_thickness is not None and min_thickness_m <= meta_thickness <= max_thickness_m:
        return meta_thickness, "wall_depth"

    wall_parts = explode_polygon_parts(plan.get("wall"))[:max_wall_parts]
    samples = [
        thickness
        for thickness in (
            _wall_part_thickness_m(poly, min_thickness_m, max_thickness_m)
            for poly in wall_parts
        )
        if thickness is not None
    ]
    if samples:
        return float(np.median(np.asarray(samples, dtype=float))), "sampled_walls"
    return 0.0, "missing"


def annotate_plans_with_wall_thickness(
    plans: Sequence[Dict[str, Any]],
    *,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    max_wall_parts: int = 10,
    min_thickness_m: float = 0.05,
    max_thickness_m: float = 1.0,
) -> Tuple[List[Dict[str, Any]], pd.DataFrame]:
    annotated_plans: List[Dict[str, Any]] = []
    rows = []

    for plan in plans:
        plan_out = normalize_plan(dict(plan)) if normalize_plan is not None else dict(plan)
        wall_thickness_m, thickness_source = estimate_plan_wall_thickness_m(
            plan_out,
            max_wall_parts=max_wall_parts,
            min_thickness_m=min_thickness_m,
            max_thickness_m=max_thickness_m,
        )
        plan_out["wall_thickness_m"] = float(wall_thickness_m)
        plan_out["wall_thickness_source"] = thickness_source
        annotated_plans.append(plan_out)
        rows.append(
            {
                "id": plan_out.get("id"),
                "wall_thickness_m": float(wall_thickness_m),
                "wall_thickness_source": thickness_source,
            }
        )

    return annotated_plans, pd.DataFrame(rows)


def reduce_to_room_layers(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    keep_keys: Iterable[str] = ("id", "inner", "wall_thickness_m", "wall_thickness_source"),
) -> List[Dict[str, Any]]:
    room_keys = tuple(room_keys)
    keep_keys = tuple(keep_keys)
    reduced_plans: List[Dict[str, Any]] = []

    for plan in plans:
        out = {}
        for key in keep_keys:
            value = plan.get(key)
            if key == "id":
                out[key] = value
            elif isinstance(value, BaseGeometry) and not value.is_empty:
                out[key] = value
            elif value is not None and key not in ("inner",):
                out[key] = value
        for room_key in room_keys:
            geom = plan.get(room_key)
            if isinstance(geom, BaseGeometry) and not geom.is_empty:
                out[room_key] = geom
            elif isinstance(geom, (list, tuple)):
                parts = [poly for poly in explode_polygon_parts(geom) if isinstance(poly, Polygon) and not poly.is_empty]
                if parts:
                    out[room_key] = parts if len(parts) > 1 else parts[0]
        reduced_plans.append(out)

    return reduced_plans


def _geom_area_total(geom: Any) -> float:
    return float(sum(poly.area for poly in explode_polygon_parts(geom)))


def _mitre_open_polygon(poly: Polygon, opening_tol_m: float) -> Optional[Polygon]:
    poly = repair_polygonal(poly)
    if poly is None or opening_tol_m <= 0:
        return poly

    shrunk = repair_polygonal(_safe_buffer(poly, -opening_tol_m))
    shrunk_parts = [part for part in explode_polygon_parts(shrunk) if isinstance(part, Polygon) and not part.is_empty]
    if len(shrunk_parts) != 1:
        return None

    reopened = repair_polygonal(_safe_buffer(shrunk_parts[0], opening_tol_m))
    reopened_parts = [part for part in explode_polygon_parts(reopened) if isinstance(part, Polygon) and not part.is_empty]
    if len(reopened_parts) != 1:
        return None
    return reopened_parts[0]


def _cleanup_geometry_changed(before: Polygon, after: Polygon, *, tol: float = 1e-8) -> bool:
    if before is after:
        return False
    if before is None or after is None:
        return True
    if abs(float(before.area) - float(after.area)) > tol:
        return True
    if len(before.exterior.coords) != len(after.exterior.coords):
        return True
    # Ring keys ignore object identity and normalize harmless coordinate noise from repair steps.
    return raw_ring_key(before) != raw_ring_key(after)


def open_concave_room_polygons(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    opening_tol_scale: float = 0.5,
    min_opening_tol_m: float = 0.05,
    max_opening_tol_m: float = 0.2,
    max_area_loss_ratio: float = 0.35,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, float | int]]:
    room_keys = tuple(room_keys)
    cleaned_plans: List[Dict[str, Any]] = []
    plans_changed = 0
    changed_room_geometries = 0
    rejected_split = 0
    rejected_area_loss = 0
    rejected_missing_tolerance = 0
    total_area_removed_m2 = 0.0

    for plan in plans:
        plan_in = normalize_plan(dict(plan)) if normalize_plan is not None else dict(plan)
        wall_thickness_m = float(plan_in.get("wall_thickness_m") or 0.0)
        opening_tol_m = min(max_opening_tol_m, max(min_opening_tol_m, opening_tol_scale * wall_thickness_m)) if wall_thickness_m > 0 else 0.0
        plan_changed = False
        out = {k: v for k, v in plan_in.items() if k not in room_keys and k != "graph"}

        for room_key in room_keys:
            cleaned_parts = []
            for poly in explode_polygon_parts(plan_in.get(room_key)):
                if not isinstance(poly, Polygon) or poly.is_empty:
                    continue
                candidate = poly
                if not is_convex_polygon(poly):
                    if opening_tol_m <= 0:
                        rejected_missing_tolerance += 1
                    else:
                        opened = _mitre_open_polygon(poly, opening_tol_m)
                        if opened is None:
                            rejected_split += 1
                        else:
                            area_before = float(poly.area)
                            area_after = float(opened.area)
                            area_loss_ratio = max(0.0, (area_before - area_after) / max(area_before, 1e-9))
                            if area_loss_ratio > max_area_loss_ratio:
                                rejected_area_loss += 1
                            else:
                                if _cleanup_geometry_changed(poly, opened):
                                    plan_changed = True
                                    changed_room_geometries += 1
                                    total_area_removed_m2 += max(0.0, area_before - area_after)
                                candidate = opened
                cleaned_parts.append(candidate)
            if cleaned_parts:
                out[room_key] = cleaned_parts if len(cleaned_parts) > 1 else cleaned_parts[0]

        if plan_changed:
            plans_changed += 1
        cleaned_plans.append(out)

    stats = {
        "plans_changed": int(plans_changed),
        "changed_room_geometries": int(changed_room_geometries),
        "rejected_split": int(rejected_split),
        "rejected_area_loss": int(rejected_area_loss),
        "rejected_missing_tolerance": int(rejected_missing_tolerance),
        "total_area_removed_m2": float(total_area_removed_m2),
        "opening_tol_scale": float(opening_tol_scale),
        "min_opening_tol_m": float(min_opening_tol_m),
        "max_opening_tol_m": float(max_opening_tol_m),
        "max_area_loss_ratio": float(max_area_loss_ratio),
    }
    return cleaned_plans, stats


def _grow_room_into_wall(poly: Polygon, inner_geom: Any, blocking_geom: Any, offset_m: float):
    poly = repair_polygonal(poly)
    if poly is None or offset_m <= 0:
        return poly

    grown = repair_polygonal(_safe_buffer(poly, offset_m))
    if grown is None:
        return poly

    growth = _polygonal_overlay(grown, poly, "difference")
    if growth is None:
        return poly
    if inner_geom is not None:
        growth = _polygonal_overlay(growth, inner_geom, "intersection")
        if growth is None:
            return poly
    if blocking_geom is not None:
        blocked = repair_polygonal(_safe_buffer(blocking_geom, offset_m))
        if blocked is not None:
            growth = _polygonal_overlay(growth, blocked, "difference")
            if growth is None:
                return poly
    expanded = _polygonal_union([poly, growth])
    return expanded if expanded is not None else poly


def grow_rooms_by_stored_wall_thickness(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], pd.DataFrame, int]:
    adjusted_plans: List[Dict[str, Any]] = []
    rows = []
    fallback_count = 0

    room_keys = tuple(room_keys)
    for plan in plans:
        plan_in = normalize_plan(dict(plan)) if normalize_plan is not None else dict(plan)
        wall_thickness_m = float(plan_in.get("wall_thickness_m") or 0.0)
        thickness_source = plan_in.get("wall_thickness_source", "missing")
        offset_m = 0.5 * wall_thickness_m if wall_thickness_m > 0 else 0.0
        inner_geom = repair_polygonal(plan_in.get("inner"))
        room_entries = [
            (room_key, poly)
            for room_key in room_keys
            for poly in (
                repair_polygonal(part)
                for part in explode_polygon_parts(plan_in.get(room_key))
            )
            if poly is not None
        ]

        plan_out = {k: v for k, v in plan_in.items() if k not in room_keys and k != "graph"}
        total_room_area_before = float(sum(poly.area for _, poly in room_entries))
        total_room_area_after = 0.0
        used_fallback = False

        try:
            expanded_by_key: Dict[str, List[Any]] = {room_key: [] for room_key in room_keys}
            for idx, (room_key, poly) in enumerate(room_entries):
                other_union = _polygonal_union(
                    other_poly
                    for j, (_, other_poly) in enumerate(room_entries)
                    if j != idx
                )
                expanded = _grow_room_into_wall(poly, inner_geom, other_union, offset_m)
                if expanded is None:
                    expanded = poly
                total_room_area_after += _geom_area_total(expanded)
                expanded_by_key[room_key].append(expanded)

            for room_key, geoms in expanded_by_key.items():
                merged = _polygonal_union(geoms)
                if merged is not None:
                    plan_out[room_key] = merged
        except Exception:
            used_fallback = True
            fallback_count += 1
            plan_out = dict(plan_in)
            total_room_area_after = total_room_area_before

        adjusted_plans.append(plan_out)
        rows.append(
            {
                "id": plan_in.get("id"),
                "wall_thickness_m": float(wall_thickness_m),
                "wall_thickness_source": thickness_source,
                "room_area_before_m2": total_room_area_before,
                "room_area_after_m2": total_room_area_after,
                "room_growth_area_m2": total_room_area_after - total_room_area_before,
                "offset_topology_fallback": used_fallback,
            }
        )

    return adjusted_plans, pd.DataFrame(rows), int(fallback_count)


def offset_rooms_into_walls(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    max_wall_parts: int = 10,
    min_thickness_m: float = 0.05,
    max_thickness_m: float = 1.0,
) -> Tuple[List[Dict[str, Any]], pd.DataFrame, int]:
    annotated_plans, _thickness_df = annotate_plans_with_wall_thickness(
        plans,
        normalize_plan=normalize_plan,
        max_wall_parts=max_wall_parts,
        min_thickness_m=min_thickness_m,
        max_thickness_m=max_thickness_m,
    )
    room_only_plans = reduce_to_room_layers(annotated_plans, room_keys)
    return grow_rooms_by_stored_wall_thickness(
        room_only_plans,
        room_keys,
        normalize_plan=normalize_plan,
    )


def clean_room_plan_geometries(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    simplify_tol: float = 1e-9,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    cleaned_dataset: List[Dict[str, Any]] = []
    degenerate_rooms_removed = 0
    before_vertices = count_room_vertices(plans, room_keys)
    room_keys = tuple(room_keys)

    for plan in plans:
        work = normalize_plan(dict(plan)) if normalize_plan is not None else dict(plan)
        out: Dict[str, Any] = {}
        for key, value in work.items():
            if key == "graph":
                continue
            if key == "id" or key not in room_keys:
                out[key] = value
                continue

            cleaned_parts = []
            for poly in explode_polygon_parts(value):
                simplified = simplify_polygon_vertices(poly, tol=simplify_tol)
                for part in explode_polygon_parts(simplified):
                    if is_degenerate_polygon(part, tol=simplify_tol):
                        degenerate_rooms_removed += 1
                        continue
                    cleaned_parts.append(part)
            if cleaned_parts:
                out[key] = cleaned_parts
        cleaned_dataset.append(out)

    after_vertices = count_room_vertices(cleaned_dataset, room_keys)
    stats = {
        "before_vertices": int(before_vertices),
        "after_vertices": int(after_vertices),
        "removed_vertices": int(before_vertices - after_vertices),
        "degenerate_rooms_removed": int(degenerate_rooms_removed),
    }
    return cleaned_dataset, stats


def _plan_room_instances(plan: Dict[str, Any], room_keys: Iterable[str]) -> List[Dict[str, Any]]:
    instances: List[Dict[str, Any]] = []
    for room_key in room_keys:
        for part_index, poly in enumerate(explode_polygon_parts(plan.get(room_key))):
            if isinstance(poly, Polygon) and not poly.is_empty:
                instances.append(
                    {
                        "label": room_key,
                        "part_index": part_index,
                        "geometry": poly,
                    }
                )
    return instances


def _instance_segments(instances: Sequence[Dict[str, Any]], *, tol: float = 1e-9) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    vertical: List[Dict[str, Any]] = []
    horizontal: List[Dict[str, Any]] = []
    for inst_idx, inst in enumerate(instances):
        coords = list(inst["geometry"].exterior.coords)
        for i in range(len(coords) - 1):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            if abs(x0 - x1) <= tol and abs(y1 - y0) > tol:
                vertical.append(
                    {
                        "inst_idx": inst_idx,
                        "coord": float(x0),
                        "start": float(min(y0, y1)),
                        "end": float(max(y0, y1)),
                    }
                )
            elif abs(y0 - y1) <= tol and abs(x1 - x0) > tol:
                horizontal.append(
                    {
                        "inst_idx": inst_idx,
                        "coord": float(y0),
                        "start": float(min(x0, x1)),
                        "end": float(max(x0, x1)),
                    }
                )
    return vertical, horizontal


def _segment_overlap(seg_a: Dict[str, Any], seg_b: Dict[str, Any]) -> Tuple[float, float, float]:
    start = max(seg_a["start"], seg_b["start"])
    end = min(seg_a["end"], seg_b["end"])
    return start, end, max(0.0, end - start)


def _safe_intersection_area(lhs: Any, rhs: Any) -> float:
    inter = _polygonal_overlay(lhs, rhs, "intersection")
    return float(sum(poly.area for poly in explode_polygon_parts(inter)))


def _candidate_strip(
    orientation: str,
    coord_a: float,
    coord_b: float,
    overlap_start: float,
    overlap_end: float,
):
    if orientation == "vertical":
        return box(min(coord_a, coord_b), overlap_start, max(coord_a, coord_b), overlap_end)
    return box(overlap_start, min(coord_a, coord_b), overlap_end, max(coord_a, coord_b))


def _polygon_compactness(poly: Polygon) -> float:
    perimeter = float(poly.length)
    if perimeter <= 1e-12:
        return 0.0
    return float((4.0 * math.pi * poly.area) / (perimeter * perimeter))


def _evaluate_gap_move(
    instances: Sequence[Dict[str, Any]],
    target_idx: int,
    strip: Polygon,
    *,
    tol: float = 1e-8,
    max_compactness_drop: float = 0.08,
):
    target_geom = repair_polygonal(instances[target_idx]["geometry"])
    if target_geom is None:
        return None
    other_union = _polygonal_union(
        inst["geometry"]
        for idx, inst in enumerate(instances)
        if idx != target_idx
    )
    expanded = _polygonal_union([target_geom, strip])
    if expanded is None:
        return None
    overlap_area = _safe_intersection_area(expanded, other_union)
    if overlap_area > tol:
        return None
    added_area = float(sum(poly.area for poly in explode_polygon_parts(expanded))) - float(
        sum(poly.area for poly in explode_polygon_parts(target_geom))
    )
    if added_area < strip.area - 1e-6:
        return None
    relative_change = strip.area / max(float(target_geom.area), 1e-9)
    compactness_before = _polygon_compactness(target_geom)
    compactness_after = _polygon_compactness(expanded)
    compactness_drop = max(0.0, compactness_before - compactness_after)
    perimeter_growth = max(0.0, float(expanded.length) - float(target_geom.length))
    if compactness_drop > max_compactness_drop:
        return None
    return {
        "new_geom": expanded,
        "relative_change": float(relative_change),
        "compactness_before": float(compactness_before),
        "compactness_after": float(compactness_after),
        "compactness_drop": float(compactness_drop),
        "perimeter_growth_m": float(perimeter_growth),
    }


def _choose_gap_candidate(
    instances: Sequence[Dict[str, Any]],
    reference_union: Any,
    *,
    max_gap_m: float,
    min_parallel_length_m: float,
    min_reference_overlap_ratio: float,
    max_compactness_drop: float,
) -> Optional[Dict[str, Any]]:
    occupied_union = _polygonal_union(inst["geometry"] for inst in instances)
    if occupied_union is None:
        return None

    vertical_segments, horizontal_segments = _instance_segments(instances)
    candidates: List[Dict[str, Any]] = []

    for orientation, segments in (("vertical", vertical_segments), ("horizontal", horizontal_segments)):
        if len(segments) < 2:
            continue
        segments = sorted(segments, key=lambda item: (item["coord"], item["start"], item["end"]))
        for i, seg_a in enumerate(segments):
            for seg_b in segments[i + 1:]:
                gap = seg_b["coord"] - seg_a["coord"]
                if gap <= 1e-9:
                    continue
                if gap > max_gap_m:
                    break
                if seg_a["inst_idx"] == seg_b["inst_idx"]:
                    continue
                overlap_start, overlap_end, overlap_len = _segment_overlap(seg_a, seg_b)
                if overlap_len < min_parallel_length_m:
                    continue
                strip = _candidate_strip(
                    orientation,
                    seg_a["coord"],
                    seg_b["coord"],
                    overlap_start,
                    overlap_end,
                )
                if strip.is_empty or strip.area <= 1e-9:
                    continue
                if _safe_intersection_area(strip, occupied_union) > 1e-8:
                    continue
                if reference_union is not None:
                    ref_overlap = _safe_intersection_area(strip, reference_union)
                    if ref_overlap <= max(1e-8, float(min_reference_overlap_ratio) * strip.area):
                        continue

                left_eval = _evaluate_gap_move(
                    instances,
                    seg_a["inst_idx"],
                    strip,
                    max_compactness_drop=max_compactness_drop,
                )
                right_eval = _evaluate_gap_move(
                    instances,
                    seg_b["inst_idx"],
                    strip,
                    max_compactness_drop=max_compactness_drop,
                )
                move_options = []
                if left_eval is not None:
                    move_options.append(
                        {
                            "target_idx": seg_a["inst_idx"],
                            **left_eval,
                        }
                    )
                if right_eval is not None:
                    move_options.append(
                        {
                            "target_idx": seg_b["inst_idx"],
                            **right_eval,
                        }
                    )
                if not move_options:
                    continue
                move = min(
                    move_options,
                    key=lambda item: (
                        item["compactness_drop"],
                        item["perimeter_growth_m"],
                        item["relative_change"],
                        strip.area,
                        gap,
                    ),
                )
                candidates.append(
                    {
                        "orientation": orientation,
                        "gap_m": float(gap),
                        "length_m": float(overlap_len),
                        "strip": strip,
                        "strip_area_m2": float(strip.area),
                        **move,
                    }
                )

    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item["compactness_drop"],
            item["perimeter_growth_m"],
            item["strip_area_m2"],
            item["relative_change"],
            item["gap_m"],
        ),
    )


def _instances_to_plan(
    instances: Sequence[Dict[str, Any]],
    source_plan: Dict[str, Any],
    room_keys: Iterable[str],
    *,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    out = normalize_plan(dict(source_plan)) if normalize_plan is not None else dict(source_plan)
    for key in room_keys:
        out.pop(key, None)
    grouped: Dict[str, List[Polygon]] = {key: [] for key in room_keys}
    for inst in instances:
        grouped[inst["label"]].extend(explode_polygon_parts(inst["geometry"]))
    for key, polys in grouped.items():
        if polys:
            out[key] = polys
    return out


def close_narrow_parallel_gaps(
    plans: Sequence[Dict[str, Any]],
    reference_plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    max_gap_m: float = 0.35,
    min_parallel_length_m: float = 1.5,
    min_reference_overlap_ratio: float = 0.6,
    max_compactness_drop: float = 0.08,
    normalize_plan: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    max_examples: int = 6,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    room_keys = tuple(room_keys)
    reference_by_id = {plan.get("id"): plan for plan in reference_plans}
    cleaned_plans: List[Dict[str, Any]] = []
    changed_examples: List[Dict[str, Any]] = []
    plans_changed = 0
    gaps_closed = 0
    total_gap_area = 0.0

    for plan in plans:
        plan_id = plan.get("id")
        reference_plan = reference_by_id.get(plan_id, plan)
        reference_union = _polygonal_union(plan_room_polygons(reference_plan, room_keys))
        instances = _plan_room_instances(plan, room_keys)
        if not instances:
            cleaned_plans.append(plan)
            continue

        before_plan = None
        applied_moves: List[Dict[str, Any]] = []
        while True:
            candidate = _choose_gap_candidate(
                instances,
                reference_union,
                max_gap_m=max_gap_m,
                min_parallel_length_m=min_parallel_length_m,
                min_reference_overlap_ratio=min_reference_overlap_ratio,
                max_compactness_drop=max_compactness_drop,
            )
            if candidate is None:
                break
            if before_plan is None:
                before_plan = _instances_to_plan(
                    instances,
                    plan,
                    room_keys,
                    normalize_plan=normalize_plan,
                )
            instances[candidate["target_idx"]]["geometry"] = candidate["new_geom"]
            applied_moves.append(
                {
                    "gap_m": candidate["gap_m"],
                    "length_m": candidate["length_m"],
                    "strip_area_m2": candidate["strip_area_m2"],
                    "orientation": candidate["orientation"],
                    "compactness_before": candidate["compactness_before"],
                    "compactness_after": candidate["compactness_after"],
                    "perimeter_growth_m": candidate["perimeter_growth_m"],
                }
            )
            gaps_closed += 1
            total_gap_area += candidate["strip_area_m2"]

        if applied_moves:
            plans_changed += 1
            cleaned_plan = _instances_to_plan(
                instances,
                plan,
                room_keys,
                normalize_plan=normalize_plan,
            )
            if len(changed_examples) < max_examples:
                changed_examples.append(
                    {
                        "id": plan_id,
                        "before": before_plan,
                        "after": cleaned_plan,
                        "moves": applied_moves,
                    }
                )
            cleaned_plans.append(cleaned_plan)
        else:
            cleaned_plans.append(plan)

    stats = {
        "plans_changed": int(plans_changed),
        "gaps_closed": int(gaps_closed),
        "total_gap_area_m2": float(total_gap_area),
        "max_gap_m": float(max_gap_m),
        "min_parallel_length_m": float(min_parallel_length_m),
        "min_reference_overlap_ratio": float(min_reference_overlap_ratio),
        "max_compactness_drop": float(max_compactness_drop),
    }
    return cleaned_plans, stats, changed_examples
