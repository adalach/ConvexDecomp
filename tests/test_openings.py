"""Checks that a hole is opened exactly and that the search opens it like any other cut.

Runs under pytest, or directly with ``python tests/test_openings.py``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.core.convexity import is_convex_polygon
from convexdecomp.geometry.openings import hole_chords, hole_openings
from convexdecomp.osm.decompose import (
    ConvexDecompositionConfig,
    _poly_key_for_state,
    decompose_polygon_with_stats,
    normalize_polygon,
)

SQUARE_HOLE = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(4, 4), (6, 4), (6, 6), (4, 6)]])
# The hole's right corner lies on the outer wall.
TOUCHING_HOLE = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(10, 5), (6, 4), (6, 6)]])
TWO_HOLES = Polygon(
    [(0, 0), (20, 0), (20, 10), (0, 10)],
    [[(3, 3), (6, 3), (6, 6), (3, 6)], [(13, 3), (16, 3), (16, 6), (13, 6)]],
)
# A courtyard building, rotated so that no edge is axis-aligned.
_c, _s = math.cos(0.3), math.sin(0.3)
ROTATED = Polygon(
    [(_c * x - _s * y, _s * x + _c * y) for x, y in [(0, 0), (30, 0), (30, 12), (0, 12)]],
    [[(_c * x - _s * y, _s * x + _c * y) for x, y in [(10, 4), (20, 4), (20, 8), (10, 8)]]],
)


def _area_kept(parts, poly):
    return math.isclose(sum(p.area for p in parts), poly.area, rel_tol=1e-9)


def test_every_opening_cuts_two_holeless_pieces_of_the_full_area():
    openings = hole_openings(SQUARE_HOLE)
    # 4 corners x (2 carried-on edges + 1 bisector), any two from different corners.
    assert len(hole_chords(orient(SQUARE_HOLE, 1.0))) == 12
    assert len(openings) == 54
    for opening in openings:
        assert len(opening.pieces) == 2
        assert all(p.is_valid and not p.interiors for p in opening.pieces)
        assert _area_kept(opening.pieces, SQUARE_HOLE)


def test_a_hole_touching_the_wall_opens_with_one_chord():
    assert TOUCHING_HOLE.is_valid
    openings = hole_openings(TOUCHING_HOLE)
    assert openings
    assert all("touch" in o.label for o in openings)
    for opening in openings:
        assert all(p.is_valid and not p.interiors for p in opening.pieces)
        assert _area_kept(opening.pieces, TOUCHING_HOLE)


def test_a_second_hole_goes_to_the_piece_that_contains_it():
    openings = hole_openings(TWO_HOLES)
    assert openings
    for opening in openings:
        assert sorted(len(p.interiors) for p in opening.pieces) == [0, 1]
        assert _area_kept(opening.pieces, TWO_HOLES)


def test_min_part_area_refuses_openings_with_small_pieces():
    small = hole_openings(SQUARE_HOLE, min_part_area=10.0)
    assert small and len(small) < len(hole_openings(SQUARE_HOLE))
    assert all(min(p.area for p in o.pieces) >= 10.0 for o in small)


def test_holes_survive_normalisation_and_count_in_the_state_key():
    assert normalize_polygon(SQUARE_HOLE).interiors
    outline = Polygon(SQUARE_HOLE.exterior)
    assert _poly_key_for_state(SQUARE_HOLE) != _poly_key_for_state(outline)


def test_the_search_decomposes_holed_polygons_without_triangles():
    cfg = ConvexDecompositionConfig()
    for poly in (SQUARE_HOLE, TOUCHING_HOLE, TWO_HOLES, ROTATED):
        result = decompose_polygon_with_stats(poly, cfg)
        parts = result["best_variant"]
        assert result["convex_success"]
        assert all(is_convex_polygon(p) and not p.interiors for p in parts)
        assert _area_kept(parts, poly)
    # A courtyard building is four quadrilaterals around the courtyard, not a fan of triangles.
    parts = decompose_polygon_with_stats(ROTATED, cfg)["best_variant"]
    assert len(parts) == 4
    assert all(len(p.exterior.coords) == 5 for p in parts)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
