"""Footprints as BEM4AI Stage 3 selects them: the mapping cleanup, the measurements the checks read, the fingerprint.

The functions and column names follow BEM4AI's ``helpers/building_sampling.py`` and
``helpers/review_log.py``, so a footprint table of either pipeline reads the same way:

| Columns | Made by |
| --- | --- |
| ``n_osm_vertices``, ``cleanup_filled_m2``, ``cleanup_cut_m2`` (`CLEANUP_COLUMNS`) | `clean_footprints`: the exterior vertices of the outline it was given, the area of the holes it filled and of the parts it cut off |
| ``n_footprint_parts``, ``n_footprint_holes``, ``n_footprint_vertices``, ``footprint_width_m``, ``footprint_length_m``, ``footprint_area_m2``, ``min_corner_angle_deg``, ``footprint_circularity`` (`FOOTPRINT_SHAPE_COLUMNS`) | `footprint_shapes` |
| ``thin_part_share`` | `thin_part_shares`: the share of the area in parts narrower than the thinnest zone (ConvexDecomp's own) |
| ``footprint_fingerprint`` | `footprint_fingerprint` of the outline before the cleanup |

Every function works on footprints in metres. BEM4AI fingerprints its footprints in WGS84 degrees;
a footprint imported from BEM4AI keeps that fingerprint, and a footprint that comes without one is
fingerprinted here in local metres.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from convexdecomp.osm.normalize import count_exterior_vertices, count_polygon_holes, normalize_to_polygons

__all__ = [
    "CLEANUP_COLUMNS",
    "FOOTPRINT_GRID_M",
    "FOOTPRINT_SHAPE_COLUMNS",
    "FootprintCleanup",
    "clean_footprints",
    "footprint_fingerprint",
    "footprint_shape",
    "footprint_shapes",
    "make_geometry_valid",
    "polygon_parts",
    "thin_part_shares",
]

#: The grid a footprint in metres is fingerprinted on (BEM4AI's ``BUILDING_GRID_M``).
FOOTPRINT_GRID_M = 0.01

#: What `clean_footprints` reports per footprint.
CLEANUP_COLUMNS = ("n_osm_vertices", "cleanup_filled_m2", "cleanup_cut_m2")
#: What `footprint_shapes` measures per footprint.
FOOTPRINT_SHAPE_COLUMNS = ("n_footprint_parts", "n_footprint_holes", "n_footprint_vertices", "footprint_width_m",
                           "footprint_length_m", "footprint_area_m2", "min_corner_angle_deg", "footprint_circularity")

# A change smaller than this (m2) is floating-point noise of the shrink and expand.
_CLEANUP_NOISE_M2 = 0.01


def make_geometry_valid(geom: Any, *, drop_invalid: bool = False) -> Any:
    """Repair an invalid geometry with ``shapely.make_valid``, which keeps every lobe of a self-intersecting ring.

    ``buffer(0)`` is the fallback. With ``drop_invalid=True`` an empty or unrepairable geometry
    becomes None instead of being passed through unchanged.
    """
    if geom is None or getattr(geom, "is_empty", True):
        return None if drop_invalid else geom
    try:
        if geom.is_valid:
            return geom
    except Exception:  # noqa: BLE001 - fall through to the repair attempts
        pass
    try:
        repaired = shapely.make_valid(geom)
        if repaired is not None and not repaired.is_empty:
            return repaired
    except Exception:  # noqa: BLE001 - fall back to the buffer trick
        pass
    try:
        repaired = geom.buffer(0)
        if repaired is not None and not repaired.is_empty:
            return repaired
    except Exception:  # noqa: BLE001
        pass
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


# ------------------------------------------------------------------------------------------------- cleanup
@dataclass(frozen=True)
class FootprintCleanup:
    """The mapping artefacts `clean_footprints` removes from a footprint.

    No room fits into a part of a footprint narrower than `thin_part_max_width_m`: such a part is
    cut off (a spike, a leg, a strip along a wall), and a hole that narrow everywhere is filled. A
    piece the cut separates from the rest is dropped when it is smaller than `min_piece_area_m2`
    and kept otherwise, which leaves the footprint several polygons. A vertex within
    `collinear_tolerance_m` of the outline without it is dropped. The values are section
    ``footprint_cleanup`` of ``config/checks.json`` and, for the pieces, ``footprint.area_min_m2``.
    """

    thin_part_max_width_m: float
    collinear_tolerance_m: float
    min_piece_area_m2: float

    @classmethod
    def from_thresholds(cls, thresholds) -> FootprintCleanup:
        """The cleanup the thresholds of ``config/checks.json`` (`Rules.thresholds`) describe."""
        section = thresholds["footprint_cleanup"]
        return cls(float(section["thin_part_max_width_m"]), float(section["collinear_tolerance_m"]),
                   float(thresholds["footprint"]["area_min_m2"]))


def _is_polygon(geometry) -> bool:
    return geometry is not None and not geometry.is_empty and geometry.geom_type == "Polygon"


def _exterior_vertices(shapes: np.ndarray, polygon: np.ndarray) -> np.ndarray:
    counts = np.zeros(len(shapes), dtype=int)
    for k in np.flatnonzero(~polygon):
        counts[k] = count_exterior_vertices(shapes[k])
    if polygon.any():
        rings = shapely.get_exterior_ring(shapes[polygon])
        counts[polygon] = np.maximum(shapely.get_num_coordinates(rings) - 1, 0)
    return counts


def _removed_area(shapes: np.ndarray, opened: np.ndarray) -> np.ndarray:
    """The area of each footprint outside its opening; NaN where the opening is empty or GEOS fails.

    An opening with the footprint's area and bounds and no more vertices is the footprint itself,
    and counts 0 without the costlier difference.
    """
    removed = np.zeros(len(shapes))
    same = ((np.abs(shapely.area(shapes) - shapely.area(opened)) < 1e-4)
            & (np.abs(shapely.bounds(shapes) - shapely.bounds(opened)).max(axis=1) < 1e-4)
            & (shapely.get_num_coordinates(opened) <= shapely.get_num_coordinates(shapes)))
    rest = np.flatnonzero(~same & ~shapely.is_empty(opened))
    try:
        removed[rest] = shapely.area(shapely.difference(shapes[rest], opened[rest]))
    except shapely.errors.GEOSException:
        for j in rest:
            try:
                removed[j] = float(shapely.area(shapely.difference(shapes[j], opened[j])))
            except shapely.errors.GEOSException:
                removed[j] = np.nan
    return np.where(shapely.is_empty(opened), np.nan, removed)


def _without_thin_parts(shape: Polygon, opened: BaseGeometry, cleanup: FootprintCleanup):
    """`shape` within its opening `opened`, without the pieces below `cleanup.min_piece_area_m2`; None on failure."""
    try:
        kept = shapely.intersection(shape, opened)
    except shapely.errors.GEOSException:
        return None
    parts = sorted((part for part in polygon_parts(kept) if part.area > _CLEANUP_NOISE_M2),
                   key=lambda part: part.area, reverse=True)
    if not parts:
        return None
    pieces = [part for part in parts if part.area >= cleanup.min_piece_area_m2] or parts[:1]
    return pieces[0] if len(pieces) == 1 else shapely.MultiPolygon(pieces)


def clean_footprints(shapes_m, cleanup: FootprintCleanup) -> tuple[np.ndarray, pd.DataFrame]:
    """Footprints in metres without the mapping artefacts of `cleanup`, and what was removed.

    Only polygons are cleaned, in three steps: the holes narrower than
    `cleanup.thin_part_max_width_m` everywhere are filled; every part of the outline narrower than
    that is cut off (shrunk and expanded by half the width with mitre joins, which restores every
    other corner, and kept within the outline), with the pieces of the cut below
    `cleanup.min_piece_area_m2`; then the vertices within `cleanup.collinear_tolerance_m` of the
    outline without them are dropped. A step that would empty the footprint, leave it invalid or
    that GEOS cannot compute leaves it as it was. A footprint no step changes is returned as the
    same object.

    Returns the footprints and a table of `CLEANUP_COLUMNS`, one row per footprint.
    """
    shapes = np.asarray(list(shapes_m), dtype=object)
    out = shapes.copy()
    n = len(shapes)
    filled, cut = np.zeros(n), np.zeros(n)
    polygon = np.array([_is_polygon(shape) for shape in shapes], dtype=bool)
    half = cleanup.thin_part_max_width_m / 2.0

    for k in np.flatnonzero(polygon & (shapely.get_num_interior_rings(shapes) > 0)):
        rings = list(shapes[k].interiors)
        holes = np.array([Polygon(ring) for ring in rings], dtype=object)
        thin = shapely.is_empty(shapely.buffer(holes, -half, join_style="mitre"))
        if thin.any():
            out[k] = Polygon(shapes[k].exterior, [ring for ring, narrow in zip(rings, thin) if not narrow])
            filled[k] = float(shapely.area(holes[thin]).sum())

    positions = np.flatnonzero(polygon)
    if len(positions):
        opened = shapely.buffer(shapely.buffer(out[positions], -half, join_style="mitre"), half, join_style="mitre")
        removed = _removed_area(out[positions], opened)
        for j in np.flatnonzero(removed > _CLEANUP_NOISE_M2):
            k = positions[j]
            kept = _without_thin_parts(out[k], opened[j], cleanup)
            if kept is not None and kept.is_valid:
                cut[k] = float(out[k].area - kept.area)
                out[k] = kept

        simple = shapely.simplify(out[positions], cleanup.collinear_tolerance_m, preserve_topology=True)
        fewer = (shapely.get_num_coordinates(simple) < shapely.get_num_coordinates(out[positions]))
        for j in np.flatnonzero(fewer & shapely.is_valid(simple) & ~shapely.is_empty(simple)):
            out[positions[j]] = simple[j]

    report = pd.DataFrame({"n_osm_vertices": _exterior_vertices(shapes, polygon), "cleanup_filled_m2": filled,
                           "cleanup_cut_m2": cut})
    return out, report


# ----------------------------------------------------------------------------------------------- measuring
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


def thin_part_shares(geometries_m, width_m: float) -> np.ndarray:
    """The share of each footprint's area in parts narrower than `width_m`.

    What an opening by half the width (shrink and expand with mitre joins) removes, over the area:
    0 for a footprint every part of which is at least `width_m` wide. NaN where there is no area.
    """
    shapes = np.asarray(list(geometries_m), dtype=object)
    area = np.array([float(shape.area) if shape is not None else 0.0 for shape in shapes])
    half = width_m / 2.0
    opened = shapely.buffer(shapely.buffer(shapes, -half, join_style="mitre"), half, join_style="mitre")
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(area > 0, _removed_area_safe(shapes, opened) / area, np.nan)
    return np.clip(share, 0.0, 1.0)


def _removed_area_safe(shapes: np.ndarray, opened: np.ndarray) -> np.ndarray:
    """`_removed_area`, with an empty opening (the whole footprint thinner than the width) removing everything."""
    removed = _removed_area(shapes, opened)
    empty = shapely.is_empty(opened) | np.array([shape is None for shape in opened])
    return np.where(empty, shapely.area(shapes), removed)
