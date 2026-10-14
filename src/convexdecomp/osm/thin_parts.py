"""Thin parts: cutting off the parts of a footprint narrower than the thinnest zone, and measuring what that changed.

A shrink by half of ``width_m`` followed by an expand by as much (a morphological opening, with
mitre joins) removes every part narrower than ``width_m``: a stub, a leg, a strip along a wall, or
the wall between a courtyard and the outside. Applied to the whole outline, it also rebuilds every
convex curve from its offset, which drops vertices along the curve and can turn the corner next to
it; and it leaves pairs of vertices about 1e-15 m apart where a removed part met the wall.

`erase_thin_parts` therefore uses the opening only to find the thin parts: the pieces of the
footprint outside the opening. Those pieces are cut from the original outline, and everything else
keeps its own vertices (the opening rebuilds a curve without taking area off it, so a curve leaves no
piece). Specks of the cut below ``_SPECK_M2`` are numerical and dropped. What the step changed is
reported, so that the footprint check ``footprint_thin_parts`` can remove the footprints for which
it changed the building rather than cut a mapping artefact off: the share of the area it took off
(``thin_part_share``), and whether the result is still one polygon with the same holes
(``thin_part_topology_changed``). The cut is then passed through
`convexdecomp.osm.vertices.remove_redundant_vertices`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import shapely

from convexdecomp.osm.vertices import VertexCleanup, remove_redundant_vertices

__all__ = ["THIN_PART_COLUMNS", "erase_thin_parts"]

#: What `erase_thin_parts` reports per footprint.
THIN_PART_COLUMNS = ("thin_part_share", "thin_part_topology_changed", "thin_part_vertices_removed")

_GRID_M = 1e-6
# Pieces of a cut smaller than this (m2) are numerical specks, not parts of the building.
_SPECK_M2 = 0.01


def _polygons(geom, min_area: float = 0.0) -> list:
    return [part for part in shapely.get_parts(geom) if part.geom_type == "Polygon" and part.area > min_area]


def _thin_pieces(shape, opened):
    """The pieces of `shape` outside `opened` (None when there are none)."""
    try:
        outside = shapely.difference(shape, opened, grid_size=_GRID_M)
    except shapely.errors.GEOSException:
        return None
    pieces = _polygons(outside, min_area=1e-6)
    return shapely.union_all(pieces) if pieces else None


def erase_thin_parts(geometries, width_m: float, cleanup: VertexCleanup = VertexCleanup()) -> tuple[list, pd.DataFrame]:
    """Each footprint without its parts narrower than `width_m`, and what that changed (`THIN_PART_COLUMNS`).

    A footprint the cut splits or changes the holes of is returned as cut (several polygons, or the
    footprint itself when nothing would be left), with ``thin_part_topology_changed`` set, so that the
    check removes it.
    """
    shapes = np.asarray(list(geometries), dtype=object)
    half = width_m / 2.0
    opened = shapely.buffer(shapely.buffer(shapes, -half, join_style="mitre"), half, join_style="mitre")
    out, share, changed, removed = [], [], [], []
    for shape, result in zip(shapes, opened):
        area = float(shape.area)
        if result is None or result.is_empty:
            out.append(shape)
            share.append(1.0)
            changed.append(True)
            removed.append(0)
            continue
        thin = _thin_pieces(shape, result)
        if thin is None:
            out.append(shape)
            share.append(0.0)
            changed.append(False)
            removed.append(0)
            continue
        cut = shapely.difference(shape, thin, grid_size=_GRID_M)
        parts = _polygons(cut, min_area=_SPECK_M2)
        kept = parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)
        kept, n_removed = remove_redundant_vertices(kept, cleanup)
        holes_before = len(shape.interiors) if shape.geom_type == "Polygon" else -1
        out.append(kept)
        share.append(max(0.0, (area - float(kept.area)) / area) if area > 0 else 0.0)
        changed.append(len(parts) != 1 or sum(len(p.interiors) for p in parts) != holes_before)
        removed.append(n_removed)
    report = pd.DataFrame({"thin_part_share": share, "thin_part_topology_changed": changed,
                           "thin_part_vertices_removed": removed})
    return out, report
