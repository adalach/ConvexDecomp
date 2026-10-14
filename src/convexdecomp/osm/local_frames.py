"""Footprints in their own local metric frame, and the cleanup BEM4AI Stage 4 gives a main footprint there.

Each footprint lives in a local frame: the metric CRS of its city (``metric_crs``), moved so that the
centre of its bounding box is the origin (``bbox_cx_m``, ``bbox_cy_m`` in that CRS). A table carries
one geometry column per step, as BEM4AI's ``buildings_m`` does:

| Column | Holds |
| --- | --- |
| ``geom_local_raw`` | the outline as it arrived, repaired |
| ``geom_local_clean`` | after the mapping cleanup (`convexdecomp.osm.footprints.clean_footprints`, BEM4AI Stage 3) |
| ``geom_local_preray`` | after normalization and edge alignment (`convexdecomp.osm.normalize`) |
| ``geom_local`` | after the thin parts are erased and the facade offsets closed: the footprint that is decomposed |

`sync_geometry` points the plain ``geometry`` column at whichever is current. The functions here are
those of BEM4AI's ``helpers/local_frames.py`` and ``helpers/utils.py`` for main footprints; the
neighbours, shading and windows of Stage 4 have no counterpart in ConvexDecomp.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import numpy as np
from shapely.affinity import translate
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

from convexdecomp.osm.footprints import make_geometry_valid, polygon_parts

__all__ = [
    "FacadeOffsetConfig",
    "clean_polygonal",
    "clean_polygonal_in_one_piece",
    "clean_ring_coords",
    "close_facade_offsets",
    "keep_polygons_min_area",
    "local_frame",
    "sync_geometry",
]


def local_frame(geometries_metric) -> tuple[list, np.ndarray, np.ndarray]:
    """Footprints in a metric CRS moved so that the centre of each one's bounding box is the origin.

    Returns the moved footprints and the centres (``bbox_cx_m``, ``bbox_cy_m``) they were moved from.
    """
    moved, cx, cy = [], [], []
    for geom in geometries_metric:
        minx, miny, maxx, maxy = geom.bounds
        x, y = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        moved.append(translate(geom, xoff=-x, yoff=-y))
        cx.append(x)
        cy.append(y)
    return moved, np.asarray(cx), np.asarray(cy)


def sync_geometry(gdf: gpd.GeoDataFrame, geom_col: str = "geom_local") -> gpd.GeoDataFrame:
    """Mirror a named geometry column into ``geometry`` and make it active (local frames have no CRS)."""
    gdf["geometry"] = gdf[geom_col]
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=None)


def keep_polygons_min_area(geom: Any, min_area: float) -> Polygon | MultiPolygon | None:
    """Drop polygonal parts below ``min_area``; None when nothing survives."""
    parts = [p for p in polygon_parts(geom) if p.area >= float(min_area) and p.is_valid]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


# ------------------------------------------------------------------------------------------- thin parts
def clean_polygonal(geom: Any, shrink: float, expand: float, min_area: float) -> Any:
    """Shrink then re-expand a footprint (mitre joins) to erase the parts thinner than twice ``shrink``.

    Any step that empties the geometry returns the input unchanged, so the cleanup can never lose a
    building.
    """
    if geom is None or getattr(geom, "is_empty", False):
        return geom
    out = geom
    if float(shrink) > 0.0:
        out = out.buffer(-float(shrink), join_style="mitre")
    if out is None or getattr(out, "is_empty", False):
        return geom
    if float(expand) > 0.0:
        out = out.buffer(float(expand), join_style="mitre")
    return keep_polygons_min_area(out, float(min_area)) or geom


def clean_polygonal_in_one_piece(
    geom: Any, shrink: float, expand: float, min_area: float, *, retry_scale: float = 0.25
) -> tuple[Any, str]:
    """`clean_polygonal` for a footprint that has to stay one polygon.

    The shrink step cuts through any part of a footprint narrower than about twice ``shrink``, and
    both halves would survive as separate polygons. When that happens the cleanup is repeated with
    ``shrink`` and ``expand`` scaled by ``retry_scale``; when that splits it too, the footprint is
    returned unchanged.

    Returns the geometry and how it was obtained (``cleanup_mode``): ``"cleaned"``,
    ``"cleaned_reduced"`` or ``"uncleaned"``.
    """
    out = clean_polygonal(geom, shrink, expand, min_area)
    if not isinstance(geom, Polygon) or isinstance(out, Polygon):
        return out, "cleaned"
    out = clean_polygonal(geom, shrink * retry_scale, expand * retry_scale, min_area)
    if isinstance(out, Polygon):
        return out, "cleaned_reduced"
    return geom, "uncleaned"


# --------------------------------------------------------------------------------------------- rings
def _angle_between_deg(vec_a, vec_b) -> float:
    a, b = np.asarray(vec_a, dtype=float), np.asarray(vec_b, dtype=float)
    norm_a, norm_b = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 180.0
    return float(math.degrees(math.acos(float(np.clip(np.dot(a / norm_a, b / norm_b), -1.0, 1.0)))))


def clean_ring_coords(coords, *, min_edge_len_m: float = 0.05, angle_tol_deg: float = 1.0):
    """Drop a ring's tiny edges and near-collinear vertices.

    Works on the closed coordinate list of one ring and returns a closed list. Removing a vertex
    changes its two neighbouring edges, so the pass restarts after every removal rather than walking
    the ring once. If cleaning would leave fewer than three points the original coordinates come back.
    """
    pts = [tuple(map(float, pt)) for pt in list(coords)[:-1]]
    if len(pts) < 3:
        return list(coords)
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        n_pts = len(pts)
        for idx in range(n_pts):
            prev_pt = np.asarray(pts[(idx - 1) % n_pts], dtype=float)
            cur_pt = np.asarray(pts[idx], dtype=float)
            next_pt = np.asarray(pts[(idx + 1) % n_pts], dtype=float)
            prev_edge, next_edge = cur_pt - prev_pt, next_pt - cur_pt
            if float(np.linalg.norm(prev_edge)) < float(min_edge_len_m):
                del pts[idx]
                changed = True
                break
            if float(np.linalg.norm(next_edge)) < float(min_edge_len_m):
                del pts[(idx + 1) % n_pts]
                changed = True
                break
            if _angle_between_deg(prev_edge, next_edge) <= float(angle_tol_deg):
                del pts[idx]
                changed = True
                break
    if len(pts) < 3:
        return list(coords)
    return [*pts, pts[0]]


# ------------------------------------------------------------------------------------------ facade offsets
@dataclass(frozen=True)
class FacadeOffsetConfig:
    """When two stretches of one facade are the same wall.

    OSM often draws one facade as two runs a few decimetres apart: a jog where the wall steps back
    by a hand's width, or two volumes of one building traced slightly out of line. Normalization and
    edge alignment leave offsets above 0.2 m alone, and the convex decomposition then cuts along the
    step and makes the strip between the two runs a zone as wide as the offset and as long as the wall.

    Edges whose outward normals agree within ``angle_tol_deg`` and whose lines are at most
    ``max_offset_m`` apart are moved onto the line of the longest of them. The angle tolerance is
    tight because a moved edge takes the longest edge's direction. Edges that face each other are
    never merged, so a thin part of a building is not collapsed. The last two fields are the guards:
    a ring that would need a larger move is left as it is.
    """

    max_offset_m: float = 0.5
    angle_tol_deg: float = 0.5
    #: Only an edge at least this long carries a line that others move onto.
    min_line_length_m: float = 1.0
    #: Lines closer than this are already one line, and a move this small is not counted.
    min_offset_m: float = 0.005
    #: No vertex may move further than this,
    max_vertex_shift_m: float = 1.0
    #: and the footprint area may change by at most this fraction.
    max_area_change_ratio: float = 0.05


def _close_ring_offsets(coords, cfg: FacadeOffsetConfig) -> tuple[list, int] | None:
    """Close the facade offsets of one ring oriented with the building on its left.

    Returns the new closed coordinate list and the number of edges moved, or None when a vertex
    would have to move further than the guard allows.
    """
    pts = [(float(x), float(y)) for x, y in list(coords)[:-1]]
    n = len(pts)
    if n < 4:
        return list(coords), 0

    # Every edge as a line: outward unit normal (the building is on the left) and offset.
    edges = []
    for i in range(n):
        (px, py), (qx, qy) = pts[i], pts[(i + 1) % n]
        length = math.hypot(qx - px, qy - py)
        if length < 1e-9:
            edges.append(None)
            continue
        nx, ny = (qy - py) / length, -(qx - px) / length
        edges.append({"n": (nx, ny), "rho": nx * px + ny * py, "length": length,
                      "mid": ((px + qx) / 2.0, (py + qy) / 2.0)})

    # Longest edges first: each one either joins a line already laid down or starts its own.
    cos_tol = math.cos(math.radians(cfg.angle_tol_deg))
    lines: list[dict] = []
    target: list[dict | None] = [None] * n
    n_moved = 0
    for i in sorted(range(n), key=lambda k: -(edges[k]["length"] if edges[k] else 0.0)):
        edge = edges[i]
        if edge is None:
            continue
        best, best_off = None, float("inf")
        for line in lines:
            if edge["n"][0] * line["n"][0] + edge["n"][1] * line["n"][1] < cos_tol:
                continue
            off = abs(line["n"][0] * edge["mid"][0] + line["n"][1] * edge["mid"][1] - line["rho"])
            if off <= cfg.max_offset_m and off < best_off:
                best, best_off = line, off
        if best is not None:
            target[i] = best
            n_moved += int(best_off > cfg.min_offset_m)
            continue
        own = {"n": edge["n"], "rho": edge["rho"]}
        if edge["length"] >= cfg.min_line_length_m:
            lines.append(own)
        target[i] = own

    if n_moved == 0:
        return list(coords), 0

    # Each vertex goes to where the lines of its two edges now cross.
    near_parallel = math.sin(math.radians(10.0))
    new_pts = []
    for i in range(n):
        p = pts[i]
        a, b = target[(i - 1) % n], target[i]
        if a is None and b is None:
            new_pts.append(p)
            continue
        if a is None or b is None:
            line = a if b is None else b
            d = line["n"][0] * p[0] + line["n"][1] * p[1] - line["rho"]
            new_pts.append((p[0] - d * line["n"][0], p[1] - d * line["n"][1]))
            continue
        det = a["n"][0] * b["n"][1] - a["n"][1] * b["n"][0]
        if abs(det) >= near_parallel:
            new_pts.append(((a["rho"] * b["n"][1] - b["rho"] * a["n"][1]) / det,
                            (a["n"][0] * b["rho"] - b["n"][0] * a["rho"]) / det))
        else:
            # Nearly straight here: drop onto the following edge's line.
            d = b["n"][0] * p[0] + b["n"][1] * p[1] - b["rho"]
            new_pts.append((p[0] - d * b["n"][0], p[1] - d * b["n"][1]))

    if max(math.dist(p, q) for p, q in zip(pts, new_pts)) > cfg.max_vertex_shift_m:
        return None
    # A jog's riser now has both ends on one line; drop it and any straight vertex.
    return clean_ring_coords(new_pts + [new_pts[0]], min_edge_len_m=0.01, angle_tol_deg=0.5), n_moved


def close_facade_offsets(geom, cfg: FacadeOffsetConfig | None = None) -> tuple[BaseGeometry, dict]:
    """Move facade stretches that are a few decimetres out of line onto one wall line.

    Works on a single ``Polygon``, its exterior and its holes. The result is kept only when it is
    still one valid polygon whose area changed by at most ``cfg.max_area_change_ratio``; otherwise the
    input comes back unchanged.

    Returns the geometry and a dict with ``status`` (``unchanged``, ``closed``, ``kept_vertex_shift``,
    ``kept_split`` or ``kept_area_change``; ``kept_*`` means a guard refused the change),
    ``edges_moved`` and ``area_change_m2``.
    """
    cfg = cfg or FacadeOffsetConfig()
    info = {"status": "unchanged", "edges_moved": 0, "area_change_m2": 0.0}
    if not isinstance(geom, Polygon) or geom.is_empty:
        return geom, info

    poly = orient(geom, 1.0)  # exterior counter-clockwise, holes clockwise: the building is always on the left
    rings = [poly.exterior.coords, *[ring.coords for ring in poly.interiors]]
    new_rings, n_moved = [], 0
    for ring in rings:
        result = _close_ring_offsets(ring, cfg)
        if result is None:
            return geom, {**info, "status": "kept_vertex_shift"}
        new_rings.append(result[0])
        n_moved += result[1]
    if n_moved == 0:
        return geom, info

    candidate = make_geometry_valid(Polygon(new_rings[0], new_rings[1:]), drop_invalid=True)
    parts = sorted(polygon_parts(candidate), key=lambda part: part.area, reverse=True)
    if not parts or sum(part.area for part in parts[1:]) > 0.01:
        return geom, {**info, "status": "kept_split"}
    closed = parts[0]
    area_change = float(closed.area - geom.area)
    if abs(area_change) > cfg.max_area_change_ratio * float(geom.area):
        return geom, {**info, "status": "kept_area_change", "area_change_m2": area_change}
    return closed, {"status": "closed", "edges_moved": n_moved, "area_change_m2": area_change}
