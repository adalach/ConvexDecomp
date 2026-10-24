"""Checks that every cut and every decomposition keeps the polygon's area.

Runs under pytest, or directly with ``python tests/test_cuts.py`` so the
repository keeps working without an extra dependency.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from shapely.geometry import Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.core.convexity import is_convex_polygon
from convexdecomp.geometry.cuts import (
    clean_ring,
    cut_along_diagonal,
    cut_along_ray,
    first_boundary_hit,
    inward_bisector,
)
from convexdecomp.geometry.holes import holeless_polygons
from convexdecomp.osm.decompose import ConvexDecompositionConfig, decompose_polygon_with_stats
from convexdecomp.resplan.decompose import _axis_cuts
from convexdecomp.resplan.preprocess import simplify_polygon_vertices

L_SHAPE = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
U_SHAPE = Polygon([(0, 0), (12, 0), (12, 10), (8, 10), (8, 4), (4, 4), (4, 10), (0, 10)])
# Three 2 m teeth on a 3 m base, with slots between them: the line y = 3 runs along the
# bottom of both slots and through the root of every tooth.
COMB = Polygon([(0, 0), (12, 0), (12, 8), (10, 8), (10, 3), (7, 3), (7, 8), (5, 8), (5, 3), (2, 3), (2, 8), (0, 8)])


def _area_kept(parts, poly):
    return math.isclose(sum(p.area for p in parts), poly.area, rel_tol=1e-9)


def _vertex(poly, xy):
    return [tuple(c) for c in list(poly.exterior.coords)[:-1]].index(xy)


def test_ray_cut_gives_two_pieces_that_keep_the_area():
    # The edge (10, 4) -> (4, 4) carried on past the reflex corner runs into the L.
    i = _vertex(L_SHAPE, (4.0, 4.0))
    parts = cut_along_ray(L_SHAPE, i, (-1.0, 0.0))
    assert len(parts) == 2
    assert _area_kept(parts, L_SHAPE)
    assert all(is_convex_polygon(p) for p in parts)
    assert sorted(round(p.area) for p in parts) == [24, 40]


def test_ray_cut_stops_at_the_first_wall_even_when_the_line_crosses_again():
    # The full line y = 3 would cut the comb into four pieces; the ray from (10, 3)
    # to the right stops at the outer wall and cuts off only the right tooth.
    i = _vertex(COMB, (10.0, 3.0))
    parts = cut_along_ray(COMB, i, (1.0, 0.0))
    assert len(parts) == 2
    assert _area_kept(parts, COMB)
    assert sorted(round(p.area) for p in parts) == [10, 56]


def test_ray_along_a_collinear_edge_ends_at_its_nearer_endpoint():
    # From (7, 3) leftwards the ray crosses the root of the middle tooth and meets the
    # boundary at (5, 3), where the next slot's bottom edge continues along the ray.
    coords = [tuple(c) for c in list(COMB.exterior.coords)[:-1]]
    i = coords.index((7.0, 3.0))
    hit = first_boundary_hit(coords, i, (-1.0, 0.0))
    assert hit.kind == "vertex" and coords[hit.index] == (5.0, 3.0)
    parts = cut_along_ray(COMB, i, (-1.0, 0.0))
    assert sorted(round(p.area) for p in parts) == [10, 56]


def test_ray_that_points_out_of_the_polygon_does_not_cut():
    i = _vertex(L_SHAPE, (10.0, 0.0))  # a convex corner
    assert cut_along_ray(L_SHAPE, i, (1.0, 1.0)) == [L_SHAPE]


def test_a_nearby_vertex_takes_the_cut():
    # The ray from the reflex corner (4, 4) to the right passes 5 cm below the
    # reflex corner (6, 4.05) before it reaches the wall x = 7.
    poly = Polygon([(0, 0), (10, 0), (10, 2), (7, 2), (7, 4.05), (6, 4.05), (6, 8), (4, 8), (4, 4), (0, 4)])
    i = _vertex(poly, (4.0, 4.0))
    parts = cut_along_ray(poly, i, (1.0, 0.0), snap_tol=0.1)
    assert len(parts) == 2
    assert _area_kept(parts, poly)
    ends = {tuple(c) for p in parts for c in p.exterior.coords}
    assert (6.0, 4.05) in ends  # the cut ran to the vertex, not past it


def test_a_cut_leaving_a_piece_below_the_minimum_is_refused():
    i = _vertex(L_SHAPE, (4.0, 4.0))
    assert cut_along_ray(L_SHAPE, i, (1.0, 0.0), min_part_area=30.0) == [L_SHAPE]


def test_bisector_points_into_the_polygon():
    i = _vertex(L_SHAPE, (4.0, 4.0))
    dx, dy = inward_bisector(L_SHAPE, i)
    assert math.isclose(dx, -math.sqrt(0.5)) and math.isclose(dy, -math.sqrt(0.5))
    assert _area_kept(cut_along_ray(L_SHAPE, i, (dx, dy)), L_SHAPE)


def test_diagonal_inside_cuts_and_diagonal_outside_does_not():
    inside = cut_along_diagonal(L_SHAPE, _vertex(L_SHAPE, (4.0, 4.0)), _vertex(L_SHAPE, (0.0, 0.0)))
    assert len(inside) == 2 and _area_kept(inside, L_SHAPE)
    outside = cut_along_diagonal(L_SHAPE, _vertex(L_SHAPE, (10.0, 4.0)), _vertex(L_SHAPE, (4.0, 10.0)))
    assert outside == [L_SHAPE]


def test_polygons_with_holes_are_not_cut():
    holed = Polygon([(0, 0), (10, 0), (10, 8), (0, 8)], [[(4, 3), (6, 3), (6, 5), (4, 5)]])
    assert cut_along_ray(holed, 0, (1.0, 1.0)) == [holed]


def test_clean_ring_keeps_a_corner_next_to_a_near_duplicate():
    # GEOS can return a corner twice, nanometres apart. Both copies must not go.
    ring = [(3.691, 0.618), (0.64, 0.618), (0.64, 4.619), (3.691, 4.619), (3.691, 0.618 + 1e-8)]
    cleaned = clean_ring(ring, eps=1e-12)
    assert math.isclose(Polygon(cleaned).area, Polygon(ring).area, rel_tol=1e-6)


def test_resplan_simplify_does_not_turn_a_rectangle_into_a_triangle():
    rect = Polygon([(3.691, 0.618), (0.64, 0.618), (0.64, 4.619), (3.691, 4.619),
                    (3.691, 0.618 + 1e-8)])
    out = simplify_polygon_vertices(rect, tol=1e-9)
    assert math.isclose(out.area, rect.area, rel_tol=1e-6)


def test_resplan_axis_cuts_give_one_cut_per_inward_direction():
    i = _vertex(U_SHAPE, (8.0, 4.0))
    cuts = _axis_cuts(U_SHAPE, i, 0.0)
    assert len(cuts) == 1  # rightwards runs along the wall, leftwards into the room
    assert all(len(parts) == 2 and _area_kept(parts, U_SHAPE) for parts in cuts)


def test_holeless_pieces_tile_the_polygon_without_zero_area_slivers():
    holed = Polygon([(0, 0), (10, 0), (10, 8), (0, 8)], [[(4, 3), (6, 3), (6, 5), (4, 5)]])
    for max_edges in (None, 0):  # 0 forces the triangulation fallback
        pieces = holeless_polygons(holed, max_edges_per_hole=max_edges)
        assert all(not p.interiors and p.area > 0 for p in pieces)
        assert math.isclose(sum(p.area for p in pieces), holed.area, rel_tol=1e-9)
        assert unary_union(pieces).symmetric_difference(holed).area < 1e-9


def test_decompositions_cover_their_polygon_exactly():
    cfg = ConvexDecompositionConfig(min_area_m2=2.0)
    holed = Polygon([(0, 0), (10, 0), (10, 8), (0, 8)], [[(4, 3), (6, 3), (6, 5), (4, 5)]])
    for poly in (L_SHAPE, U_SHAPE, COMB, holed):
        result = decompose_polygon_with_stats(poly, cfg)
        parts = result["best_variant"]
        assert result["convex_success"]
        assert all(is_convex_polygon(p) for p in parts)
        assert _area_kept(parts, poly)
        assert unary_union(parts).symmetric_difference(poly).area < 1e-6


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print(f"ok   {name}")
            except AssertionError as error:
                failures += 1
                print(f"FAIL {name}: {error}")
    sys.exit(1 if failures else 0)
