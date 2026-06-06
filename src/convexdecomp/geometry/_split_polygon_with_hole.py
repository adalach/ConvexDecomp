"""
split_polygon_with_hole.py

Tools to split polygons with holes using reflex vertices and extended lines.

Main entry points:
    - split_polygon_with_hole(poly_with_hole, ...)
        Split a polygon that has (at least) one interior ring (hole)
        into a few polygons, using reflex-vertex lines that intersect
        the FIRST hole. Designed for the single-hole case, but the split
        is applied to the entire polygon (with all interiors).

        Fallback: if fewer than two reflex-based lines can be found that
        cross the hole, use an extended line based on one hole edge as
        the cutting line.

    - split_polygon_all_holes(geom, ...)
        High-level function that repeatedly applies split_polygon_with_hole
        so that ALL holes are removed: we split one hole at a time and
        recurse on any piece that still has interiors.

Requires:
    shapely >= 1.8
"""

import math
from typing import List

from shapely.geometry import (
    Polygon,
    MultiPolygon,
    LineString,
    MultiLineString,
    Point,
)
from shapely.ops import split, unary_union
from shapely.geometry import JOIN_STYLE


# ---------------------------------------------------------------------------
# Basic ring / reflex helpers
# ---------------------------------------------------------------------------

def ring_area(coords):
    """Signed area of a closed ring; > 0 if CCW, < 0 if CW."""
    area = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        area += x1 * y2 - x2 * y1
    return area / 2.0


def is_reflex(prev_pt, pt, next_pt, ccw=True):
    """
    True if 'pt' is a reflex vertex for a polygon ring.

    For a CCW outer ring, a vertex is reflex if the cross product is negative
    (i.e. we turn right instead of left).
    """
    ax, ay = prev_pt
    bx, by = pt
    cx, cy = next_pt
    v1 = (bx - ax, by - ay)
    v2 = (cx - bx, cy - by)
    cross = v1[0] * v2[1] - v1[1] * v2[0]
    return cross < 0 if ccw else cross > 0


def find_reflex_vertices(coords):
    """
    Return indices of reflex vertices for a closed ring.

    coords: sequence of points with coords[0] == coords[-1]
    """
    ccw = ring_area(coords) > 0
    n = len(coords) - 1  # last equals first
    reflex_indices = []
    for i in range(n):
        prev_pt = coords[(i - 1) % n]
        pt = coords[i]
        next_pt = coords[(i + 1) % n]
        if is_reflex(prev_pt, pt, next_pt, ccw=ccw):
            reflex_indices.append(i)
    return reflex_indices


# ---------------------------------------------------------------------------
# Lines from reflex vertices
# ---------------------------------------------------------------------------

def create_edge_lines_from_reflex_vertices(outer_coords, reflex_ids, poly, scale_factor=5.0):
    """
    For each reflex vertex index in reflex_ids, create extended lines along
    the two incident edges.

    Returns:
        list of (reflex_idx, LineString)
    """
    lines_with_idx = []

    # polygon bounding box to decide how far to extend the lines
    minx, miny, maxx, maxy = poly.bounds
    bbox_size = max(maxx - minx, maxy - miny)
    extend_len = bbox_size * scale_factor

    n = len(outer_coords) - 1  # last == first

    for idx in reflex_ids:
        p = outer_coords[idx]
        p_prev = outer_coords[(idx - 1) % n]
        p_next = outer_coords[(idx + 1) % n]

        for neighbor in (p_prev, p_next):
            dx = neighbor[0] - p[0]
            dy = neighbor[1] - p[1]
            length = math.hypot(dx, dy)
            if length == 0:
                continue
            ux = dx / length
            uy = dy / length

            # extend in both directions from the vertex
            p1 = (p[0] - ux * extend_len, p[1] - uy * extend_len)
            p2 = (p[0] + ux * extend_len, p[1] + uy * extend_len)
            line = LineString([p1, p2])
            lines_with_idx.append((idx, line))

    return lines_with_idx


def create_extended_line_from_hole_edge(hole_coords, poly, scale_factor=5.0):
    """
    Fallback: choose one edge of the hole (typically the longest),
    and create a long cutting line collinear with that edge.

    Returns:
        LineString or None if no valid edge exists.
    """
    if len(hole_coords) < 2:
        return None

    # bounding box to decide extension length
    minx, miny, maxx, maxy = poly.bounds
    bbox_size = max(maxx - minx, maxy - miny)
    extend_len = bbox_size * scale_factor

    # choose the longest edge of the hole (excluding closing segment if repeated)
    n = len(hole_coords) - 1  # assume last == first
    best_i = None
    best_len = 0.0
    for i in range(n):
        x1, y1 = hole_coords[i]
        x2, y2 = hole_coords[(i + 1) % n]
        dx = x2 - x1
        dy = y2 - y1
        seg_len = math.hypot(dx, dy)
        if seg_len > best_len:
            best_len = seg_len
            best_i = i

    if best_i is None or best_len == 0:
        return None

    x1, y1 = hole_coords[best_i]
    x2, y2 = hole_coords[(best_i + 1) % n]

    # direction along the edge
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
        return None

    ux = dx / length
    uy = dy / length

    # use the midpoint of the edge as anchor
    mx = 0.5 * (x1 + x2)
    my = 0.5 * (y1 + y2)

    p1 = (mx - ux * extend_len, my - uy * extend_len)
    p2 = (mx + ux * extend_len, my + uy * extend_len)

    return LineString([p1, p2])


# ---------------------------------------------------------------------------
# Cleaning: shrink + expand with straight (mitre) corners
# ---------------------------------------------------------------------------

def clean_polygon(poly: Polygon, tol: float) -> Polygon:
    """
    Erode and dilate polygon (negative then positive buffer)
    using straight (mitre) corners to remove skinny legs and noise.

    IMPORTANT: We only call this on polygons that we consider "final"
    (usually with no holes), to avoid accidentally killing holes.
    """
    shrunk = poly.buffer(-tol, join_style=JOIN_STYLE.mitre)

    if shrunk.is_empty:
        return poly

    if shrunk.geom_type == "MultiPolygon":
        shrunk = unary_union(shrunk)

    cleaned = shrunk.buffer(tol, join_style=JOIN_STYLE.mitre)
    cleaned = cleaned.buffer(0, join_style=JOIN_STYLE.mitre)

    return cleaned


# ---------------------------------------------------------------------------
# Reflex-based piece classification
# ---------------------------------------------------------------------------

def count_reflex_vertices_in_piece(piece: Polygon, reflex_points, tol=1e-6) -> int:
    """
    Count how many of the reflex_points lie on the boundary of 'piece'.

    We consider a point to be on the boundary if distance <= tol.
    """
    cnt = 0
    for x, y in reflex_points:
        p = Point(x, y)
        if piece.exterior.distance(p) <= tol:
            cnt += 1
    return cnt




# ---------------------------------------------------------------------------
# Main splitting function (single-hole aware, uses FIRST hole)
# ---------------------------------------------------------------------------

def split_polygon_with_hole(
    poly_with_hole: Polygon,
    scale_factor: float = 5.0,
    dist_tol: float = 1e-6,
    buffer_tol: float = 0.5,   # kept for signature compatibility, not used here
) -> List[Polygon]:
    """
    Perform ONE split step on a polygon with at least one hole, with respect
    to the FIRST hole.

    This function does NOT clean the result with buffer; it only splits
    and does some merging. Cleaning is applied later in recursion once
    a piece has no holes left.

    Steps (normal path):
        1) Find reflex vertices on outer ring.
        2) Create extended lines along the incident edges.
        3) Keep ONLY the two shortest lines from vertex to that hole.
        4) Split original polygon with those lines.
        5) Keep pieces that touch at least 2 of the chosen reflex vertices,
           merge the rest.

    Fallback:
        If fewer than 2 such lines can be found, use one extended line
        based on an edge of the hole as the cutting line, split once,
        and return all resulting pieces as-is.

    Parameters
    ----------
    poly_with_hole : Polygon
        Input polygon. Must have at least one interior ring for splitting to occur.
    scale_factor : float
        How large to extend the edge lines relative to polygon size.
    dist_tol : float
        Distance tolerance for geometric tests.
    buffer_tol : float
        Ignored here; cleaning is done in recursive wrapper.

    Returns
    -------
    List[Polygon]
        List of resulting polygons (some may still have holes).
        If no meaningful split can be found, returns [poly_with_hole].
    """
    # If no hole at all → just return unchanged
    if len(poly_with_hole.interiors) == 0:
        return [poly_with_hole]

    # Use only the FIRST hole for line construction,
    # but splitting is applied to the entire polygon (all interiors).
    hole_coords = list(poly_with_hole.interiors[0].coords)
    hole_poly = Polygon(hole_coords)

    outer_coords = list(poly_with_hole.exterior.coords)
    reflex_ids = find_reflex_vertices(outer_coords)

    # If no reflex vertices, we cannot apply the reflex-based method directly
    use_reflex_method = bool(reflex_ids)

    crossing_info = []   # list of (reflex_idx, line, distance_vertex_to_hole)

    if use_reflex_method:
        # Build extended lines from those reflex vertices
        lines_with_idx = create_edge_lines_from_reflex_vertices(
            outer_coords, reflex_ids, poly_with_hole, scale_factor=scale_factor
        )

        # Find lines that intersect the hole and keep the two shortest
        for idx, ln in lines_with_idx:
            inter = ln.intersection(hole_poly)
            if inter.is_empty:
                continue

            if inter.geom_type not in (
                "Point", "MultiPoint",
                "LineString", "MultiLineString",
                "GeometryCollection", "Polygon", "MultiPolygon"
            ):
                continue

            vx, vy = outer_coords[idx]
            v_point = Point(vx, vy)
            dist = v_point.distance(inter)

            # skip lines with extremely tiny distances (numerical noise)
            if dist <= dist_tol:
                continue

            crossing_info.append((idx, ln, dist))

    # ----------------------------------------------------------------------
    # Decide: reflex-based split vs fallback hole-edge-based split
    # ----------------------------------------------------------------------
    if len(crossing_info) >= 2:
        # --- Reflex-based case (normal path) ---
        crossing_info_sorted = sorted(crossing_info, key=lambda t: t[2])
        best_two = crossing_info_sorted[:2]

        crossing_lines = [ln for idx, ln, dist in best_two]
        filtered_reflex_ids = sorted({idx for idx, ln, dist in best_two})
        filtered_reflex_points = [outer_coords[i] for i in filtered_reflex_ids]

        splitter = MultiLineString(crossing_lines)
        split_result = split(poly_with_hole, splitter)

        # keep only polygon pieces
        poly_pieces = [g for g in split_result.geoms if isinstance(g, Polygon)]
        if not poly_pieces:
            return [poly_with_hole]

        pieces_mp = MultiPolygon(poly_pieces)

        # Classify pieces:
        key_pieces = []
        other_pieces = []

        for geom in pieces_mp.geoms:
            n_reflex = count_reflex_vertices_in_piece(geom, filtered_reflex_points, tol=dist_tol)
            if n_reflex >= 2:
                key_pieces.append(geom)
            else:
                other_pieces.append(geom)

        # >>> NEW: if *no* piece contains both reflex vertices, fall back to hole-edge cut
        if not key_pieces:
            fallback_line = create_extended_line_from_hole_edge(
                hole_coords, poly_with_hole, scale_factor=scale_factor
            )
            if fallback_line is not None:
                fb_splitter = MultiLineString([fallback_line])
                fb_result = split(poly_with_hole, fb_splitter)
                fb_polys = [g for g in fb_result.geoms if isinstance(g, Polygon)]
                if fb_polys:
                    return fb_polys
            # if fallback also fails, at least return the original reflex-based pieces
            return poly_pieces
        # <<< END NEW BLOCK

        merged_others_polys = []
        if other_pieces:
            merged = unary_union(other_pieces)
            if isinstance(merged, Polygon):
                merged_others_polys = [merged]
            elif isinstance(merged, MultiPolygon):
                merged_others_polys = list(merged.geoms)
            else:
                merged_others_polys = [g for g in merged.geoms if isinstance(g, Polygon)]

        final_pieces = key_pieces + merged_others_polys
        return final_pieces

    # --- Fallback: not enough reflex-based lines at all; use hole edge as cut ---
    fallback_line = create_extended_line_from_hole_edge(
        hole_coords, poly_with_hole, scale_factor=scale_factor
    )
    if fallback_line is None:
        # nothing useful to cut with
        return [poly_with_hole]

    splitter = MultiLineString([fallback_line])
    split_result = split(poly_with_hole, splitter)

    poly_pieces = [g for g in split_result.geoms if isinstance(g, Polygon)]
    if not poly_pieces:
        return [poly_with_hole]

    # In fallback mode we just return all pieces untouched (no classification).
    return poly_pieces


def _as_polygons(geom) -> List[Polygon]:
    """
    Normalize a geometry to a flat list of Polygon objects.
    """
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if isinstance(g, Polygon)]
    raise TypeError(f"Expected Polygon or MultiPolygon, got {type(geom)}")





# ---------------------------------------------------------------------------
# Recursive splitting: remove ALL holes by repeated splitting
# ---------------------------------------------------------------------------

def _split_poly_rec(
    geom,
    scale_factor: float,
    dist_tol: float,
    buffer_tol: float,
    depth: int,
    max_depth: int,
) -> List[Polygon]:
    """
    Internal recursive helper: split geometry until it has no holes left
    or we reach max_depth.

    - If geom is a Polygon:
        * if no interiors → clean + return
        * else → do one split step with split_polygon_with_hole, then recurse
    - If geom is a MultiPolygon:
        * recurse on each component and concatenate results
    """

    # Safety against infinite loops
    if depth > max_depth:
        # Just clean whatever polygons we have and flatten
        if isinstance(geom, Polygon):
            return _as_polygons(clean_polygon(geom, buffer_tol))
        elif isinstance(geom, MultiPolygon):
            out: List[Polygon] = []
            for p in geom.geoms:
                out.extend(_as_polygons(clean_polygon(p, buffer_tol)))
            return out
        else:
            raise TypeError(f"Unsupported geometry type in _split_poly_rec at max_depth: {type(geom)}")

    # --- MultiPolygon: split each part independently ---
    if isinstance(geom, MultiPolygon):
        out: List[Polygon] = []
        for p in geom.geoms:
            out.extend(
                _split_poly_rec(
                    p,
                    scale_factor=scale_factor,
                    dist_tol=dist_tol,
                    buffer_tol=buffer_tol,
                    depth=depth,        # same depth for siblings
                    max_depth=max_depth,
                )
            )
        return out

    # --- Polygon path ---
    if not isinstance(geom, Polygon):
        raise TypeError(f"Unsupported geometry type in _split_poly_rec: {type(geom)}")

    poly = geom

    # Base case: no holes → clean + return (flattened)
    if len(poly.interiors) == 0:
        return _as_polygons(clean_polygon(poly, buffer_tol))

    # Try to split this polygon using the single-hole routine (one step)
    pieces = split_polygon_with_hole(
        poly,
        scale_factor=scale_factor,
        dist_tol=dist_tol,
        buffer_tol=buffer_tol,   # ignored inside
    )

    # If splitting failed (or effectively gave us back the same thing),
    # just clean+return once to avoid infinite recursion.
    if len(pieces) == 1:
        p0 = pieces[0]
        # If we didn't reduce the number of holes, we can't progress
        if len(p0.interiors) >= len(poly.interiors):
            return _as_polygons(clean_polygon(p0, buffer_tol))
        # Otherwise we did reduce holes, recurse further once more
        return _split_poly_rec(
            p0,
            scale_factor=scale_factor,
            dist_tol=dist_tol,
            buffer_tol=buffer_tol,
            depth=depth + 1,
            max_depth=max_depth,
        )

    # Recurse on each piece and flatten
    result: List[Polygon] = []
    for p in pieces:
        result.extend(
            _split_poly_rec(
                p,
                scale_factor=scale_factor,
                dist_tol=dist_tol,
                buffer_tol=buffer_tol,
                depth=depth + 1,
                max_depth=max_depth,
            )
        )
    return result



def split_polygon_all_holes(
    geom,
    scale_factor: float = 5.0,
    dist_tol: float = 1e-6,
    buffer_tol: float = 0.5,
    max_depth: int = 20,
) -> List[Polygon]:
    """
    High-level function: take a Polygon or MultiPolygon and keep splitting
    polygons along hole-targeted lines until no piece has holes left
    (or max_depth is reached).

    Internally calls split_polygon_with_hole repeatedly, one hole at a time.

    Parameters
    ----------
    geom : Polygon or MultiPolygon
        Input geometry.
    scale_factor : float
        Extension factor for reflex lines / fallback hole-edge lines.
    dist_tol : float
        Distance tolerance for geometric tests.
    buffer_tol : float
        Cleaning buffer distance (used only on final, no-hole polygons).
    max_depth : int
        Maximum recursion depth per polygon.

    Returns
    -------
    List[Polygon]
        List of polygons without interiors (holes removed by cutting),
        or with residual holes if they could not be removed within max_depth.
    """
    if geom is None or geom.is_empty:
        return []

    if not isinstance(geom, (Polygon, MultiPolygon)):
        raise TypeError(f"Unsupported geometry type for split_polygon_all_holes: {type(geom)}")

    return _split_poly_rec(
        geom,
        scale_factor=scale_factor,
        dist_tol=dist_tol,
        buffer_tol=buffer_tol,
        depth=0,
        max_depth=max_depth,
    )
