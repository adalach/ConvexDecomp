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
from convexdecomp.osm.decompose import PAPER_2026_SEARCH, ConvexDecompositionConfig, decompose_polygon_with_stats

# The search of the EG-ICE 2026 paper: none of the newer rules, area-weighted score.
PAPER = ConvexDecompositionConfig(**PAPER_2026_SEARCH)

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


def test_paper_settings_with_the_score_object_give_the_same_result():
    a = decompose_polygon_with_stats(COURTYARD, PAPER)
    b = decompose_polygon_with_stats(COURTYARD, replace(PAPER, score_fn=PartitionScore()))
    assert sorted(round(p.area, 9) for p in a["best_variant"]) == sorted(round(p.area, 9) for p in b["best_variant"])


# A 4.5 m wide band with a shallow bend: carrying either edge on at the bend runs
# almost along the band, the bisector cuts across it.
BENT_BAND = Polygon([(0, 0), (20, 0), (40, 1.4), (40, 5.9), (19.84, 4.49), (0, 4.5)])


def test_bisector_always_cuts_a_shallow_bend_across():
    plain = decompose_polygon_with_stats(BENT_BAND, PAPER)["best_variant"]
    bis = decompose_polygon_with_stats(BENT_BAND, replace(PAPER, bisector_always=True))["best_variant"]
    assert math.isclose(sum(p.area for p in bis), BENT_BAND.area, rel_tol=1e-9)
    assert len(bis) == 2
    assert min(minimum_width(p) for p in bis) > min(minimum_width(p) for p in plain)


def test_new_corner_angles():
    from convexdecomp.osm.decompose import (
        _new_corner_deg, cut_polygon_at_vertex, cut_polygon_at_vertex_bisector, find_reflex_vertices_scored)
    cfg = ConvexDecompositionConfig()
    (bend, _), = find_reflex_vertices_scored(BENT_BAND, cfg)
    # Carrying either edge on at the 4 degree bend leaves a 4 degree corner; the bisector two near-right ones.
    for use_prev in (True, False):
        assert _new_corner_deg(cut_polygon_at_vertex(BENT_BAND, bend, cfg, use_prev=use_prev)) < 5
    assert _new_corner_deg(cut_polygon_at_vertex_bisector(BENT_BAND, bend, cfg)) > 85


def test_min_corner_rule_cuts_a_shallow_bend_across():
    parts = decompose_polygon_with_stats(BENT_BAND, replace(PAPER, min_corner_deg=15.0))["best_variant"]
    assert len(parts) == 2
    assert min(minimum_width(p) for p in parts) > 4.0
    assert math.isclose(sum(p.area for p in parts), BENT_BAND.area, rel_tol=1e-9)


def test_min_corner_rule_never_makes_a_decomposition_fail():
    # At 91 degrees every cut of an L shape creates a sharper corner; the rule is relaxed.
    result = decompose_polygon_with_stats(L_SHAPE, replace(PAPER, min_corner_deg=91.0))
    assert result["convex_success"]
    assert "sharp_corner" in (result["fallback_used"] or "")
    for poly in (COURTYARD, BENT_BAND):
        assert decompose_polygon_with_stats(poly, replace(PAPER, min_corner_deg=30.0))["convex_success"]


def test_min_zone_width_refuses_thin_zones():
    # Today the bent band is cut along an edge, leaving a 1.4 m sliver.
    plain = decompose_polygon_with_stats(BENT_BAND, PAPER)["best_variant"]
    wide = decompose_polygon_with_stats(BENT_BAND, replace(PAPER, min_zone_width_m=2.0))
    assert min(minimum_width(p) for p in plain) < 2.0
    assert wide["convex_success"] and not wide["fallback_used"]
    assert min(minimum_width(p) for p in wide["best_variant"]) >= 2.0
    assert math.isclose(sum(p.area for p in wide["best_variant"]), BENT_BAND.area, rel_tol=1e-9)


def test_min_zone_width_is_a_hard_rule():
    # A footprint thinner than the limit gets no decomposition: no thin zone enters the result.
    thin_l = Polygon([(0, 0), (20, 0), (20, 1.5), (1.5, 1.5), (1.5, 20), (0, 20)])
    assert decompose_polygon_with_stats(thin_l, PAPER)["convex_success"]
    result = decompose_polygon_with_stats(thin_l, ConvexDecompositionConfig(min_zone_width_m=3.0))
    assert not result["convex_success"] and result["best_variant"] == []
    strip = box(0, 0, 20, 1.5)
    result = decompose_polygon_with_stats(strip, ConvexDecompositionConfig(min_zone_width_m=2.0))
    assert not result["convex_success"]
    assert result["search_terminated_by"] == "thinner_than_min_zone_width"
    # The same strip with a vertex a hair off the straight line, so it is not strictly convex.
    almost = Polygon([(0, 0), (10, 0), (20, 1e-9), (20, 1.5), (0, 1.5)])
    assert not decompose_polygon_with_stats(almost, ConvexDecompositionConfig(min_zone_width_m=2.0))["convex_success"]



def test_the_defaults_are_the_decided_settings():
    cfg = ConvexDecompositionConfig()
    assert (cfg.area_weighting, cfg.beam_area_weighting, cfg.beam_ranking, cfg.bisector_always,
            cfg.min_corner_deg, cfg.min_zone_width_m) == (0.0, 1.0, "score_first", True, 30.0, 1.2)
    # An L shape becomes two rectangles; a courtyard building four.
    from test_openings import ROTATED
    for poly, n in ((L_SHAPE, 2), (ROTATED, 4)):
        parts = decompose_polygon_with_stats(poly, cfg)["best_variant"]
        assert len(parts) == n and all(math.isclose(zone_metrics(p).rectangularity, 1.0) for p in parts)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
