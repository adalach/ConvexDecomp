"""Checks for the tidy step of the corner-ray perimeter (``convexdecomp.corner_perimeter.tidy``).

Runs under pytest next to ``test_corner_perimeter.py`` (it reuses its footprints and tiling check).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_corner_perimeter import OFF, check_tiling, footprints  # noqa: E402

from convexdecomp.corner_perimeter import CornerRayConfig, corner_ray_perimeter  # noqa: E402
from convexdecomp.corner_perimeter.geometry import is_convex  # noqa: E402
from convexdecomp.corner_perimeter.tidy import drop_collinear, snap_to_outline, straighten_corners  # noqa: E402

TIDY = dict(snap_to_outline=True, merge_close_again=True, tidy_close_m=1.5, straighten_corners=True,
            straight_deg=3.0, drop_collinear=True, split_pinched=True)


def test_straighten_then_drop_collinear():
    poly = box(0, 0, 10, 4)
    left = Polygon([(0, 0), (5, 0), (5.05, 2), (5, 4), (0, 4)])
    right = Polygon([(5, 0), (10, 0), (10, 4), (5, 4), (5.05, 2)])  # 182.9° at (5.05, 2): not convex
    cfg = CornerRayConfig()
    zones = [(left, "zone"), (right, "zone")]
    assert not is_convex(right)
    assert straighten_corners(poly, zones, cfg, 2.0)[1] == []  # 2.9° off straight: beyond 2°
    zones, moved = straighten_corners(poly, zones, cfg, 3.0)
    assert len(moved) == 1 and np.allclose(moved[0], (5, 2))
    assert all(is_convex(z) for z, _ in zones)
    zones, dropped = drop_collinear(poly, zones, cfg, 0.1)
    assert len(dropped) == 1 and all(len(z.exterior.coords) == 5 for z, _ in zones)
    assert abs(sum(z.area for z, _ in zones) - poly.area) < 1e-9


def test_snap_to_the_outline():
    poly = box(0, 0, 10, 6)
    a = Polygon([(0, 0), (5, 0), (5.3, 0.4), (5, 6), (0, 6)])
    b = Polygon([(5, 0), (10, 0), (10, 6), (5, 6), (5.3, 0.4)])  # reflex at the inner point 0.5 m off the corner
    cfg = CornerRayConfig()
    assert snap_to_outline(poly, [(a, "zone"), (b, "zone")], cfg, 0.4)[1] == []  # out of reach
    zones, snapped = snap_to_outline(poly, [(a, "zone"), (b, "zone")], cfg, 0.6)
    assert len(snapped) == 1 and np.allclose(snapped[0], (5, 0))  # onto the outline point, which stays
    assert all(is_convex(z) for z, _ in zones) and all(z.area == 30 for z, _ in zones)


def test_tidy_on_the_test_footprints():
    keys = ("thin", "not_convex", "sharp_20", "specks")
    for sid, poly in footprints().items():
        base = corner_ray_perimeter(poly, CornerRayConfig(**OFF)).counts()
        result = corner_ray_perimeter(poly, CornerRayConfig(**{**OFF, **TIDY}))
        check_tiling(poly, result)
        got = result.counts()
        assert all(got[k] <= base[k] for k in keys), (sid, base, got)
    liverpool = corner_ray_perimeter(footprints()["Liverpool_0042"], CornerRayConfig(**{**OFF, **TIDY})).counts()
    assert (liverpool["zones"], liverpool["not_convex"], liverpool["snapped"]) == (14, 0, 1)  # the L-shape splits


def test_tidy_off_changes_nothing():
    poly = footprints()["Valdemoro_0001"]
    result = corner_ray_perimeter(poly, CornerRayConfig(**OFF))
    assert all(not v for v in result.tidied.values())
