"""The measurements the footprint checks read, and the fingerprint that names a footprint, as BEM4AI has them.

The column names follow BEM4AI's ``helpers/building_sampling.py`` and ``helpers/review_log.py``, so
a checks table of either pipeline reads the same way:

| Columns | Made by |
| --- | --- |
| ``n_footprint_parts``, ``n_footprint_holes``, ``n_footprint_vertices``, ``footprint_width_m``, ``footprint_length_m``, ``footprint_area_m2``, ``min_corner_angle_deg``, ``footprint_circularity`` (`FOOTPRINT_SHAPE_COLUMNS`) | `footprint_shapes` |
| ``n_thin_holes`` | `thin_hole_counts`: holes narrower than a width everywhere |
| ``footprint_fingerprint`` | `footprint_fingerprint` of the outline |

Every function works on footprints in metres. BEM4AI fingerprints its footprints in WGS84 degrees;
a footprint imported from BEM4AI keeps that fingerprint, and a footprint that comes without one is
fingerprinted here in metres.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from convexdecomp.osm.normalize import count_exterior_vertices, count_polygon_holes, normalize_to_polygons

__all__ = [
    "FOOTPRINT_GRID_M",
    "FOOTPRINT_SHAPE_COLUMNS",
    "footprint_fingerprint",
    "footprint_shape",
    "footprint_shapes",
    "make_geometry_valid",
    "polygon_parts",
    "thin_hole_counts",
]

#: The grid a footprint in metres is fingerprinted on (BEM4AI's ``BUILDING_GRID_M``).
FOOTPRINT_GRID_M = 0.01
#: What `footprint_shapes` measures per footprint.
FOOTPRINT_SHAPE_COLUMNS = ("n_footprint_parts", "n_footprint_holes", "n_footprint_vertices", "footprint_width_m",
                           "footprint_length_m", "footprint_area_m2", "min_corner_angle_deg", "footprint_circularity")


def make_geometry_valid(geom: Any, *, drop_invalid: bool = False) -> Any:
    """Repair an invalid geometry with ``shapely.make_valid`` (``buffer(0)`` as the fallback)."""
    if geom is None or getattr(geom, "is_empty", True):
        return None if drop_invalid else geom
    try:
        if geom.is_valid:
            return geom
    except Exception:  # noqa: BLE001 - fall through to the repair attempts
        pass
    for repair in (shapely.make_valid, lambda g: g.buffer(0)):
        try:
            repaired = repair(geom)
            if repaired is not None and not repaired.is_empty:
                return repaired
        except Exception:  # noqa: BLE001
            continue
    return None if drop_invalid else geom


def polygon_parts(geom: Any) -> list[Polygon]:
    """The polygons of a geometry with a positive area (none for an empty or non-polygonal one)."""
    return normalize_to_polygons(geom)


def footprint_fingerprint(geometry: BaseGeometry, grid: float = FOOTPRINT_GRID_M) -> str:
    """A hash of a footprint on a grid, as BEM4AI's review log keys its decisions (16 hex characters)."""
    if not isinstance(geometry, BaseGeometry) or geometry.is_empty:
        data = b""
    else:
        try:
            snapped = shapely.set_precision(geometry, grid)
        except shapely.errors.GEOSException:  # an invalid geometry: fingerprint its repaired form
            snapped = shapely.set_precision(make_geometry_valid(geometry), grid)
        data = shapely.to_wkb(shapely.normalize(snapped), byte_order=1)
    digest = hashlib.sha256()
    digest.update(len(data).to_bytes(8, "little"))
    digest.update(data)
    return digest.hexdigest()[:16]


def _is_polygon(geometry) -> bool:
    return geometry is not None and not geometry.is_empty and geometry.geom_type == "Polygon"


def _min_rotated_rect_dims(geom) -> tuple[float, float]:
    """The short and long side of a geometry's minimum rotated rectangle (NaN when it has none)."""
    try:
        with np.errstate(invalid="ignore", divide="ignore"):
            rectangle = shapely.oriented_envelope(geom)
    except Exception:  # noqa: BLE001
        return math.nan, math.nan
    if rectangle is None or rectangle.geom_type != "Polygon":
        return math.nan, math.nan
    ring = list(rectangle.exterior.coords)
    sides = sorted(length for length in (math.hypot(ring[i + 1][0] - ring[i][0], ring[i + 1][1] - ring[i][1])
                                         for i in range(4)) if length > 0.0)
    return (float(sides[0]), float(sides[-1])) if sides else (math.nan, math.nan)


def _exterior_figures(polygons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The sharpest corner of each polygon's outline (degrees, inside the building) and its circularity.

    The circularity is ``4 pi A / P**2`` of the outline (holes filled): 1 for a circle, 0.79 for a
    square. Polygons without an outline get NaN for both.
    """
    n = len(polygons)
    sharpest, circularity = np.full(n, np.nan), np.full(n, np.nan)
    if not n:
        return sharpest, circularity
    coordinates, owner = shapely.get_coordinates(shapely.get_exterior_ring(polygons), return_index=True)
    if not len(coordinates):
        return sharpest, circularity
    closing = np.r_[owner[1:] != owner[:-1], True]
    points, owner = coordinates[~closing], owner[~closing]
    first = np.searchsorted(owner, owner, side="left")
    last = np.searchsorted(owner, owner, side="right") - 1
    index = np.arange(len(points))
    previous, following = np.where(index == first, last, index - 1), np.where(index == last, first, index + 1)
    incoming, outgoing = points - points[previous], points[following] - points
    turn = np.degrees(np.arctan2(incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0],
                                 (incoming * outgoing).sum(axis=1)))
    signed = np.bincount(owner, weights=(points[:, 0] * points[following, 1] - points[following, 0] * points[:, 1]) / 2,
                         minlength=n)
    inside = np.where(signed[owner] > 0, 180.0 - turn, 180.0 + turn)
    np.fmin.at(sharpest, owner, inside)
    perimeter = np.bincount(owner, weights=np.hypot(*outgoing.T), minlength=n)
    with np.errstate(divide="ignore", invalid="ignore"):
        circularity = np.where(perimeter > 0, 4 * np.pi * np.abs(signed) / perimeter ** 2, np.nan)
    present = np.bincount(owner, minlength=n) >= 3
    return np.where(present, sharpest, np.nan), np.where(present, circularity, np.nan)


def footprint_shape(geometry_m: BaseGeometry) -> dict:
    """What the footprint checks measure on one footprint in metres (`FOOTPRINT_SHAPE_COLUMNS`).

    ``n_footprint_parts`` (separate polygons), ``n_footprint_holes``, ``n_footprint_vertices``
    (exterior rings), ``footprint_width_m`` and ``footprint_length_m`` (short and long side of the
    minimum rotated rectangle), ``footprint_area_m2``, ``min_corner_angle_deg`` (the sharpest corner
    of the outline, measured inside the building) and ``footprint_circularity`` (of the largest part
    when there are several).
    """
    width, length = _min_rotated_rect_dims(geometry_m) if geometry_m is not None else (math.nan, math.nan)
    parts = np.asarray(sorted(polygon_parts(geometry_m), key=lambda part: part.area, reverse=True), dtype=object)
    sharpest, circularity = _exterior_figures(parts)
    return {
        "n_footprint_parts": len(parts),
        "n_footprint_holes": count_polygon_holes(geometry_m),
        "n_footprint_vertices": count_exterior_vertices(geometry_m),
        "footprint_width_m": width,
        "footprint_length_m": length,
        "footprint_area_m2": float(geometry_m.area) if geometry_m is not None else math.nan,
        "min_corner_angle_deg": float(np.nanmin(sharpest)) if len(parts) and not np.isnan(sharpest).all() else math.nan,
        "footprint_circularity": float(circularity[0]) if len(parts) else math.nan,
    }


def footprint_shapes(geometries_m) -> pd.DataFrame:
    """`footprint_shape` of many footprints in metres, one row per geometry, polygons measured with array operations."""
    geometries = np.asarray(list(geometries_m), dtype=object)
    n = len(geometries)
    out = {column: np.full(n, np.nan) for column in FOOTPRINT_SHAPE_COLUMNS}
    polygon = np.array([_is_polygon(shape) for shape in geometries], dtype=bool)
    for k in np.flatnonzero(~polygon):
        for column, value in footprint_shape(geometries[k]).items():
            out[column][k] = value
    positions = np.flatnonzero(polygon)
    if len(positions):
        shapes = geometries[positions]
        area = shapely.area(shapes)
        part = area > 0.0
        out["footprint_area_m2"][positions] = area
        out["n_footprint_parts"][positions] = part.astype(float)
        out["n_footprint_holes"][positions] = np.where(part, shapely.get_num_interior_rings(shapes), 0)
        out["n_footprint_vertices"][positions] = np.where(
            part, np.maximum(shapely.get_num_coordinates(shapely.get_exterior_ring(shapes)) - 1, 0), 0)
        sharpest, circularity = _exterior_figures(shapes)
        out["min_corner_angle_deg"][positions] = np.where(part, sharpest, np.nan)
        out["footprint_circularity"][positions] = np.where(part, circularity, np.nan)
        for k in positions:
            out["footprint_width_m"][k], out["footprint_length_m"][k] = _min_rotated_rect_dims(geometries[k])
    frame = pd.DataFrame(out)
    for column in ("n_footprint_parts", "n_footprint_holes", "n_footprint_vertices"):
        frame[column] = frame[column].fillna(0).astype(int)
    return frame


def thin_hole_counts(geometries_m, width_m: float) -> np.ndarray:
    """How many holes of each footprint are narrower than `width_m` everywhere (vanish when shrunk by half of it)."""
    counts = np.zeros(len(geometries_m), dtype=int)
    for k, geom in enumerate(geometries_m):
        holes = [Polygon(ring) for part in polygon_parts(geom) for ring in part.interiors]
        if holes:
            shrunk = shapely.buffer(np.asarray(holes, dtype=object), -width_m / 2.0, join_style="mitre")
            counts[k] = int(shapely.is_empty(shrunk).sum())
    return counts
