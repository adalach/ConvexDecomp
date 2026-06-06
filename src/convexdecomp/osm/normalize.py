"""
Geometry normalization helpers for sampled OSM building footprints.

The goal here is stricter than the lightweight snapping in the upstream
sampler: remove near-duplicate and almost-collinear exterior vertices so that
simple shapes are represented by simple polygons before perimeter zoning and
convex decomposition.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

try:
    from shapely.validation import make_valid
except Exception:  # pragma: no cover - older Shapely fallback
    make_valid = None


@dataclass(frozen=True)
class FootprintNormalizationConfig:
    simplify_tolerance_m: float = 0.2
    snap_tolerance_m: float = 0.05
    min_edge_length_m: float = 0.75
    collinear_distance_tol_m: float = 0.3
    collinear_angle_tol_deg: float = 10.0
    area_change_ratio_tol: float = 0.03
    area_change_m2_tol: float = 3.0
    max_passes: int = 8


@dataclass(frozen=True)
class LinearArtifactFilterConfig:
    hard_min_width_m: float = 2.0
    hard_min_aspect_ratio: float = 8.0
    review_min_width_m: float = 3.0
    review_min_aspect_ratio: float = 6.0


def _safe_make_valid(geom: BaseGeometry | None) -> BaseGeometry | None:
    if geom is None or getattr(geom, "is_empty", True):
        return None
    try:
        if geom.is_valid:
            return geom
    except Exception:
        pass

    if make_valid is not None:
        try:
            return make_valid(geom)
        except Exception:
            pass

    try:
        return geom.buffer(0)
    except Exception:
        return None


def normalize_to_polygons(geom: BaseGeometry | None) -> list[Polygon]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 0 else []
    polygons: list[Polygon] = []
    for part in getattr(geom, "geoms", []):
        polygons.extend(normalize_to_polygons(part))
    return polygons


def _union_polygonal(parts: list[Polygon]) -> BaseGeometry | None:
    if not parts:
        return None
    merged = unary_union(parts)
    if getattr(merged, "is_empty", True):
        return None
    return merged


def _segment_length(a: tuple[float, float], b: tuple[float, float]) -> float:
    return float(math.hypot(b[0] - a[0], b[1] - a[1]))


def _minimum_rotated_rectangle_dims(poly: Polygon) -> tuple[float, float]:
    if not isinstance(poly, Polygon) or poly.is_empty or not math.isfinite(float(poly.area)) or poly.area <= 0:
        return float("nan"), float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            rectangle = poly.minimum_rotated_rectangle
        except Exception:
            return float("nan"), float("nan")
    coords = list(rectangle.exterior.coords)[:-1]
    if len(coords) != 4:
        return float("nan"), float("nan")
    lengths = [
        _segment_length(coords[idx], coords[(idx + 1) % 4])
        for idx in range(4)
    ]
    return float(min(lengths)), float(max(lengths))


def _point_line_distance(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return _segment_length(p, a)
    t = ((px - ax) * dx + (py - ay) * dy) / denom
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return float(math.hypot(px - proj_x, py - proj_y))


def _straight_angle_deviation_deg(
    prev_pt: tuple[float, float],
    curr_pt: tuple[float, float],
    next_pt: tuple[float, float],
) -> float:
    v1x = prev_pt[0] - curr_pt[0]
    v1y = prev_pt[1] - curr_pt[1]
    v2x = next_pt[0] - curr_pt[0]
    v2y = next_pt[1] - curr_pt[1]
    n1 = math.hypot(v1x, v1y)
    n2 = math.hypot(v2x, v2y)
    if n1 <= 1e-12 or n2 <= 1e-12:
        return 180.0
    cosang = max(-1.0, min(1.0, (v1x * v2x + v1y * v2y) / (n1 * n2)))
    angle = math.degrees(math.acos(cosang))
    return abs(180.0 - angle)


def _dedup_ring_points(
    points: list[tuple[float, float]],
    *,
    snap_tolerance_m: float,
) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for pt in points:
        if not deduped:
            deduped.append(pt)
            continue
        if _segment_length(deduped[-1], pt) <= snap_tolerance_m:
            continue
        deduped.append(pt)
    if len(deduped) >= 2 and _segment_length(deduped[0], deduped[-1]) <= snap_tolerance_m:
        deduped = deduped[:-1]
    return deduped


def _normalize_ring(
    coords: list[tuple[float, float]],
    *,
    cfg: FootprintNormalizationConfig,
) -> list[tuple[float, float]] | None:
    points = _dedup_ring_points(list(coords[:-1]), snap_tolerance_m=cfg.snap_tolerance_m)
    if len(points) < 3:
        return None

    for _ in range(cfg.max_passes):
        if len(points) < 3:
            return None
        changed = False
        cleaned: list[tuple[float, float]] = []
        n_points = len(points)

        for idx in range(n_points):
            prev_pt = points[(idx - 1) % n_points]
            curr_pt = points[idx]
            next_pt = points[(idx + 1) % n_points]

            len_prev = _segment_length(prev_pt, curr_pt)
            len_next = _segment_length(curr_pt, next_pt)
            distance = _point_line_distance(curr_pt, prev_pt, next_pt)
            angle_dev = _straight_angle_deviation_deg(prev_pt, curr_pt, next_pt)

            drop_collinear = (
                distance <= cfg.collinear_distance_tol_m
                and angle_dev <= cfg.collinear_angle_tol_deg
            )
            drop_tiny_edge = (
                min(len_prev, len_next) <= cfg.min_edge_length_m
                and distance <= cfg.collinear_distance_tol_m
            )

            if drop_collinear or drop_tiny_edge:
                changed = True
                continue

            cleaned.append(curr_pt)

        cleaned = _dedup_ring_points(cleaned, snap_tolerance_m=cfg.snap_tolerance_m)
        if len(cleaned) < 3:
            return None
        points = cleaned
        if not changed:
            break

    return points + [points[0]]


def _normalize_polygon(poly: Polygon, *, cfg: FootprintNormalizationConfig) -> Polygon:
    exterior = _normalize_ring(list(poly.exterior.coords), cfg=cfg)
    if exterior is None:
        return poly

    holes: list[list[tuple[float, float]]] = []
    for interior in poly.interiors:
        ring = _normalize_ring(list(interior.coords), cfg=cfg)
        if ring is None:
            continue
        hole_poly = Polygon(ring)
        if hole_poly.area <= 0:
            continue
        holes.append(ring)

    candidate = Polygon(exterior, holes)
    candidate = _safe_make_valid(candidate)
    polygons = normalize_to_polygons(candidate)
    if len(polygons) != 1:
        return poly
    return polygons[0]


def normalize_footprint_geometry(
    geom: BaseGeometry | None,
    cfg: FootprintNormalizationConfig | None = None,
) -> BaseGeometry | None:
    cfg = cfg or FootprintNormalizationConfig()
    geom = _safe_make_valid(geom)
    if geom is None or getattr(geom, "is_empty", True):
        return None

    polygons = normalize_to_polygons(geom)
    if not polygons:
        return None

    normalized_parts: list[Polygon] = []
    for poly in polygons:
        original = poly
        candidate = poly
        if cfg.simplify_tolerance_m > 0:
            try:
                candidate = poly.simplify(cfg.simplify_tolerance_m, preserve_topology=True)
            except Exception:
                candidate = poly
        candidate = _safe_make_valid(candidate) or original
        candidate_polys = normalize_to_polygons(candidate)
        if len(candidate_polys) != 1:
            normalized_parts.append(original)
            continue

        normalized = _normalize_polygon(candidate_polys[0], cfg=cfg)
        area_tol = max(cfg.area_change_m2_tol, cfg.area_change_ratio_tol * max(original.area, 1.0))
        if abs(normalized.area - original.area) > area_tol:
            normalized_parts.append(original)
            continue
        normalized_parts.append(normalized)

    merged = _union_polygonal(normalized_parts)
    return _safe_make_valid(merged)


def count_exterior_vertices(geom: BaseGeometry | None) -> int:
    total = 0
    for poly in normalize_to_polygons(geom):
        total += max(0, len(poly.exterior.coords) - 1)
    return total


def count_polygon_holes(geom: BaseGeometry | None) -> int:
    total = 0
    for poly in normalize_to_polygons(geom):
        total += len(poly.interiors)
    return total


def normalize_buildings_gdf(
    gdf: gpd.GeoDataFrame,
    *,
    geometry_col: str = "geometry",
    cfg: FootprintNormalizationConfig | None = None,
    align_cfg: Any | None = None,
) -> gpd.GeoDataFrame:
    cfg = cfg or FootprintNormalizationConfig()
    out = gdf.copy()
    out["geometry_before_norm"] = out[geometry_col]
    out["n_vertices_before_norm"] = out[geometry_col].apply(count_exterior_vertices)
    out[geometry_col] = out[geometry_col].apply(lambda geom: normalize_footprint_geometry(geom, cfg=cfg))
    out["n_vertices_after_norm"] = out[geometry_col].apply(count_exterior_vertices)
    out["vertex_delta_norm"] = out["n_vertices_after_norm"] - out["n_vertices_before_norm"]
    out["area_before_norm_m2"] = out["geometry_before_norm"].apply(lambda geom: float(geom.area) if geom is not None else float("nan"))
    out["area_after_norm_m2"] = out[geometry_col].apply(lambda geom: float(geom.area) if geom is not None else float("nan"))
    out["area_delta_norm_m2"] = out["area_after_norm_m2"] - out["area_before_norm_m2"]

    if align_cfg is not None:
        from convexdecomp.osm.axis_alignment import align_buildings_gdf

        out["geometry_before_align"] = out[geometry_col]
        out["area_before_align_m2"] = out["geometry_before_align"].apply(
            lambda geom: float(geom.area) if geom is not None else float("nan")
        )
        out = align_buildings_gdf(out, geometry_col=geometry_col, cfg=align_cfg)
    return out


def annotate_linear_artifact_flags(
    gdf: gpd.GeoDataFrame,
    *,
    geometry_col: str = "geometry",
    cfg: LinearArtifactFilterConfig | None = None,
) -> gpd.GeoDataFrame:
    cfg = cfg or LinearArtifactFilterConfig()
    out = gdf.copy()

    widths: list[float] = []
    lengths: list[float] = []
    aspects: list[float] = []
    rect_fills: list[float] = []

    for geom in out[geometry_col]:
        polygons = normalize_to_polygons(geom)
        if len(polygons) != 1:
            widths.append(float("nan"))
            lengths.append(float("nan"))
            aspects.append(float("nan"))
            rect_fills.append(float("nan"))
            continue

        poly = polygons[0]
        width, length = _minimum_rotated_rectangle_dims(poly)
        area = float(poly.area)
        aspect = (length / width) if width > 1e-9 else float("inf")
        rect_fill = (area / (width * length)) if width > 1e-9 and length > 1e-9 else float("nan")

        widths.append(width)
        lengths.append(length)
        aspects.append(aspect)
        rect_fills.append(rect_fill)

    out["artifact_mrr_width_m"] = widths
    out["artifact_mrr_length_m"] = lengths
    out["artifact_aspect_ratio"] = aspects
    out["artifact_rect_fill"] = rect_fills
    out["is_linear_artifact_hard"] = (
        out["artifact_mrr_width_m"].lt(cfg.hard_min_width_m)
        & out["artifact_aspect_ratio"].gt(cfg.hard_min_aspect_ratio)
    )
    out["is_linear_artifact_review"] = (
        out["artifact_mrr_width_m"].lt(cfg.review_min_width_m)
        & out["artifact_aspect_ratio"].gt(cfg.review_min_aspect_ratio)
        & ~out["is_linear_artifact_hard"]
    )
    return out
