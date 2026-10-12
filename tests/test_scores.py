"""Checks the configurable score and the beam ranking option.

Runs under pytest, or directly with ``python tests/test_scores.py``.
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.core.scores import PartitionScore, minimum_width, zone_metrics
from convexdecomp.core.search import score_polygon_partition
from convexdecomp.osm.decompose import ConvexDecompositionConfig, decompose_polygon_with_stats

L_SHAPE = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
COURTYARD = Polygon([(0, 0), (20, 0), (20, 14), (0, 14)], [[(5, 4), (15, 4), (15, 10), (5, 10)]])
PARTS = [box(0, 0, 4, 4), box(4, 0, 10, 1), box(0, 4, 4, 10)]


def test_default_score_is_the_original_score():
    for w in (1.0, 20.0, 50.0):
        new = PartitionScore(weight_compactness=w)(PARTS)
        old = score_polygon_partition(PARTS, weight_parts=1.0, weight_compactness=w)
        assert math.isclose(new, old, rel_tol=1e-12)


def test_metrics():
    m = zone_metrics(box(0, 0, 6, 1))
    assert math.isclose(m.rectangularity, 1.0)
    assert math.isclose(m.width, 1.0)
    assert math.isclose(m.compactness, 4 * math.pi * 6 / 14 ** 2)
    # A triangle is narrowest perpendicular to its base: its height, 3.
    assert math.isclose(minimum_width(Polygon([(0, 0), (4, 0), (2, 3)])), 3.0, rel_tol=1e-9)


def test_terms():
    # Every zone counts equally: the 1 m strip weighs as much as the squares.
    equal = PartitionScore(area_weighting=0.0).terms(PARTS)["compactness"]
    weighted = PartitionScore().terms(PARTS)["compactness"]
    assert equal > weighted
    narrow = PartitionScore(weight_narrow=1.0, narrow_width_m=2.0).terms(PARTS)["narrow"]
    assert math.isclose(narrow, 0.5)  # only the strip: (2 - 1) / 2
    small = PartitionScore(weight_small=1.0, small_area_m2=10.0).terms(PARTS)["small"]
    assert math.isclose(small, 0.4)  # only the strip: (10 - 6) / 10


def test_custom_score_and_ranking_keep_the_area():
    base = ConvexDecompositionConfig()
    for cfg in (replace(base, score_fn=PartitionScore(area_weighting=0.0, weight_narrow=5.0)),
                replace(base, beam_ranking="score_first"),
                replace(base, beam_ranking="score_first", score_fn=PartitionScore(weight_rectangularity=10.0))):
        for poly in (L_SHAPE, COURTYARD):
            result = decompose_polygon_with_stats(poly, cfg)
            assert result["convex_success"]
            assert math.isclose(sum(p.area for p in result["best_variant"]), poly.area, rel_tol=1e-9)


def test_default_config_is_unchanged():
    a = decompose_polygon_with_stats(COURTYARD, ConvexDecompositionConfig())
    b = decompose_polygon_with_stats(COURTYARD, ConvexDecompositionConfig(score_fn=PartitionScore()))
    assert sorted(round(p.area, 9) for p in a["best_variant"]) == sorted(round(p.area, 9) for p in b["best_variant"])


# A 4.5 m wide band with a shallow bend: carrying either edge on at the bend runs
# almost along the band, the bisector cuts across it.
BENT_BAND = Polygon([(0, 0), (20, 0), (40, 1.4), (40, 5.9), (19.84, 4.49), (0, 4.5)])


def test_bisector_always_cuts_a_shallow_bend_across():
    plain = decompose_polygon_with_stats(BENT_BAND, ConvexDecompositionConfig())["best_variant"]
    bis = decompose_polygon_with_stats(BENT_BAND, ConvexDecompositionConfig(bisector_always=True))["best_variant"]
    assert math.isclose(sum(p.area for p in bis), BENT_BAND.area, rel_tol=1e-9)
    assert len(bis) == 2
    assert min(minimum_width(p) for p in bis) > min(minimum_width(p) for p in plain)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
