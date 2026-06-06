from __future__ import annotations

import math
import warnings
from typing import Any, Dict

import numpy as np
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform

try:
    from shapely import make_valid as _make_valid
except Exception:
    try:
        from shapely.validation import make_valid as _make_valid
    except Exception:
        _make_valid = None

__all__ = [
    "area",
    "compute_scale_from_meta",
    "normalize_keys",
    "quantize_geometry",
    "quantize_plan",
    "rescale_plan",
]


def normalize_keys(plan: Dict[str, Any]) -> Dict[str, Any]:
    if "balacony" in plan and "balcony" not in plan:
        plan["balcony"] = plan.pop("balacony")
    return plan


def area(poly: Polygon | MultiPolygon) -> float:
    if isinstance(poly, Polygon):
        return 0.0 if poly.is_empty else float(poly.area)
    if isinstance(poly, MultiPolygon):
        if poly.is_empty or len(poly.geoms) == 0:
            return 0.0
        return float(sum(part.area for part in poly.geoms if part is not None and not part.is_empty))
    return 0.0


def compute_scale_from_meta(plan: Dict[str, Any]) -> float:
    meta_area = plan.get("area")
    inner = plan.get("inner")
    inner_area = area(inner) if inner is not None else 0.0
    if meta_area is None or meta_area <= 0 or inner_area <= 0:
        return 1.0
    return math.sqrt(float(meta_area) / float(inner_area))


def _explode_polygon_parts(value: Any) -> list[Polygon]:
    if isinstance(value, Polygon):
        return [] if value.is_empty else [value]
    if isinstance(value, MultiPolygon):
        return [poly for poly in value.geoms if isinstance(poly, Polygon) and not poly.is_empty]
    geoms = getattr(value, "geoms", None)
    if geoms is not None:
        out: list[Polygon] = []
        for geom in geoms:
            out.extend(_explode_polygon_parts(geom))
        return out
    return []


def _safe_buffer_zero(geom: BaseGeometry) -> BaseGeometry:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return geom.buffer(0)


def _repair_polygonal_geometry(geom: BaseGeometry) -> BaseGeometry:
    if not isinstance(geom, (Polygon, MultiPolygon)) or geom.is_empty:
        return geom

    work: BaseGeometry = geom
    if _make_valid is not None:
        try:
            work = _make_valid(work)
        except Exception:
            pass

    if isinstance(work, BaseGeometry) and not work.is_empty and not work.is_valid:
        try:
            work = _safe_buffer_zero(work)
        except Exception:
            pass

    parts = [poly for poly in _explode_polygon_parts(work) if poly.area > 1e-9]
    if not parts:
        return geom
    if len(parts) == 1:
        return parts[0]
    try:
        return MultiPolygon(parts)
    except Exception:
        return parts[0]


def rescale_plan(plan: Dict[str, Any], scale: float) -> Dict[str, Any]:
    scaled_plan = normalize_keys(dict(plan))
    for key, geom in list(scaled_plan.items()):
        if isinstance(geom, BaseGeometry) and not geom.is_empty:
            transformed = affinity.scale(geom, xfact=scale, yfact=scale, origin=(0.0, 0.0))
            scaled_plan[key] = _repair_polygonal_geometry(transformed)
    return scaled_plan


def quantize_geometry(geom: BaseGeometry, step: float) -> BaseGeometry:
    if geom is None or geom.is_empty:
        return geom

    inv = 1.0 / float(step)

    def _rounder(x, y, z=None):
        xr = np.rint(x * inv) / inv
        yr = np.rint(y * inv) / inv
        if z is None:
            return xr, yr
        zr = np.rint(z * inv) / inv
        return xr, yr, zr

    quantized = transform(_rounder, geom)
    return quantized if quantized.is_valid else quantized.buffer(0)


def quantize_plan(plan: Dict[str, Any], step: float) -> Dict[str, Any]:
    quantized_plan = normalize_keys(dict(plan))
    for key, geom in list(quantized_plan.items()):
        if isinstance(geom, BaseGeometry):
            quantized = quantize_geometry(geom, step)
            quantized_plan[key] = _repair_polygonal_geometry(quantized)
    return quantized_plan
