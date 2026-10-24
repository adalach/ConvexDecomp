"""Checks for the corner-ray perimeter (``convexdecomp.corner_perimeter``).

Runs under pytest, or directly with ``python tests/test_corner_perimeter.py``. The four real footprints in
``tests/data/corner_perimeter_footprints.json`` (WKB, local metres) are the test footprints of the method; their
expected counts are the results the method was settled on (2026-10-20); five more footprints show the fixes of
2026-10-23.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import fields
from pathlib import Path

import numpy as np
from shapely import union_all, wkb
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.corner_perimeter import (LAYERS, NUMBERS, STAGES, CornerRayConfig, corner_ray_perimeter,
                                           preview, settings_table, sharp_corners, zone_records)
from convexdecomp.corner_perimeter.geometry import insert_point, zone_across
from convexdecomp.corner_perimeter.zones import cut_sharp_corners, join_specks

DATA = Path(__file__).resolve().parent / "data" / "corner_perimeter_footprints.json"

RECT = box(0, 0, 20, 12)
ROUND = Polygon([(12 * math.cos(2 * math.pi * k / 24), 12 * math.sin(2 * math.pi * k / 24)) for k in range(24)])
COURTYARD = Polygon(box(0, 0, 30, 30).exterior.coords, [box(10, 10, 20, 20).exterior.coords])

#: zones, core parts, thin, not convex, corners under 20 degrees, wing-end corners
EXPECTED = {
    "Valdemoro_0001": (40, 3, 1, 8, 0, 0),
    "Krakow_0013": (28, 2, 1, 6, 0, 8),
    "Bognor_Regis_0005": (38, 2, 0, 2, 0, 4),
    "Angers_0018": (4, 0, 0, 0, 0, 2),
    # the footprints of the fixes of 2026-10-23
    "Liverpool_0042": (13, 1, 0, 1, 0, 0),    # rule 9 hands a 38.8 m² triangle over whole
    "Bucharest_0003": (27, 1, 0, 1, 0, 0),    # rule 7: the hull would leave its neighbour 1.52 m wide
    "Derby_0029": (3, 0, 0, 0, 0, 0),         # the wing-end check gives the narrow part's end its rays back
    "Caen_0016": (23, 1, 1, 5, 0, 0),         # rule 10: three specks join a neighbour
    "Athens_0005": (11, 0, 1, 2, 0, 0),       # rule 9: two zones share the shorter edge; the longer share takes it
}


def footprints() -> dict[str, Polygon]:
    return {sid: wkb.loads(hexed, hex=True) for sid, hexed in json.loads(DATA.read_text()).items()}


def counts(result) -> tuple:
    c = result.counts()
    return (c["zones"], c["core_parts"], c["thin"], c["not_convex"], c["sharp_20"], c["wing_end_corners"])


def check_tiling(poly: Polygon, result) -> None:
    """The zones are valid, do not overlap, cover the footprint and keep every corner of its outline."""
    zones = [z for z, _ in result.zones]
    assert all(z.is_valid and z.area > 0 for z in zones)
    total = sum(z.area for z in zones)
    assert abs(total - poly.area) < 1e-6 * poly.area
    assert abs(union_all(zones).area - total) < 1e-6 * poly.area  # no overlaps
    corners = {(round(x, 6), round(y, 6)) for ring in [poly.exterior, *poly.interiors] for x, y in ring.coords}
    kept = {(round(x, 6), round(y, 6)) for z in zones for ring in [z.exterior, *z.interiors] for x, y in ring.coords}
    assert corners <= kept  # the outline is never moved


def test_real_footprints():
    for sid, poly in footprints().items():
        result = corner_ray_perimeter(poly)
        assert counts(result) == EXPECTED[sid], (sid, counts(result))
        check_tiling(poly, result)


def test_wing_ends_on_a_narrow_leg():
    poly = footprints()["Angers_0018"]
    with_rays = corner_ray_perimeter(poly, CornerRayConfig(wing_end_max_m=0.0))
    assert counts(with_rays) == (6, 0, 4, 0, 0, 0)  # the leg's end rays split it into strips under 2 m
    assert counts(corner_ray_perimeter(poly)) == (4, 0, 0, 0, 0, 2)


def test_each_fix_on_its_footprint():
    f = footprints()
    assert corner_ray_perimeter(f["Liverpool_0042"]).counts()["sharp_whole"] == 1
    no_width = corner_ray_perimeter(f["Bucharest_0003"], CornerRayConfig(hull_keeps_width=False))
    assert counts(no_width) == (27, 1, 1, 0, 0, 0)  # the hull leaves the neighbour 1.52 m wide
    derby = corner_ray_perimeter(f["Derby_0029"])
    assert derby.counts()["wing_ends_restored"] == 2 and not derby.wing_ends
    assert counts(corner_ray_perimeter(f["Derby_0029"], CornerRayConfig(wing_end_check=False))) == (4, 0, 1, 0, 0, 2)
    assert corner_ray_perimeter(f["Caen_0016"], CornerRayConfig(join_specks=False)).counts()["specks"] == 2
    assert corner_ray_perimeter(f["Caen_0016"]).counts()["specks_joined"] == 3


def test_rule9_hands_a_cascading_triangle_over_whole():
    # a 10° triangle whose third corner is 80°: cut, it would leave a similar triangle, again and again
    tip_y = 10 * math.tan(math.radians(10.0))
    below, above = box(0, -3, 10, 0), Polygon([(0, 0), (10, tip_y), (10, 4), (0, 4)])
    triangle = Polygon([(0, 0), (10, 0), (10, tip_y)])
    zones, cuts, whole = cut_sharp_corners([(below, "zone"), (triangle, "zone"), (above, "zone")], CornerRayConfig())
    assert len(zones) == 2 and len(whole) == 1 and not cuts
    assert sharp_corners(zones) == 0 and abs(sum(z.area for z, _ in zones) - 70.0) < 1e-9


def test_zone_across_within_rounding():
    # the zone on the right carries a point 1e-12 m off the shared edge: exactly, the two edges share nothing
    left, right = box(0, 0, 1, 1), Polygon([(1, 0), (2, 0), (2, 1), (1, 1), (1 + 1e-12, 0.5)])
    edge = LineString([(1, 0), (1, 1)])
    assert zone_across([(left, "zone"), (right, "zone")], 0, edge)[0] < 1e-6
    share, j = zone_across([(left, "zone"), (right, "zone")], 0, edge, tol=1e-6)
    assert j == 1 and abs(share - 1.0) < 1e-5


def test_insert_point():
    square = box(0, 0, 2, 2)
    assert len(insert_point(square, np.array([1.0, 0.0])).exterior.coords) == 6
    assert insert_point(square, np.array([1.0, 1.0])).equals_exact(square, 0.0)  # not on an edge


def test_specks_join_the_longest_shared_edge():
    a, b = box(0, 0, 5, 4), Polygon([(5, 0), (10, 0), (10, 4), (5.3, 4), (5, 3.5)])
    speck = Polygon([(5, 3.5), (5.3, 4), (5, 4)])  # shares 0.5 m with a and 0.58 m with b
    zones, joined = join_specks([(a, "zone"), (b, "zone"), (speck, "thin")], CornerRayConfig())
    assert len(zones) == 2 and len(joined) == 1 and abs(joined[0].area - 20.0) < 1e-9


def test_curved_facade_keeps_its_rays():
    # a round building drawn as 24 walls of 3.1 m: no wall is a wing's end, since its neighbours are not parallel
    assert counts(corner_ray_perimeter(ROUND)) == (24, 1, 0, 0, 0, 0)
    every_wall = corner_ray_perimeter(ROUND, CornerRayConfig(wing_end_parallel_deg=180.0))
    assert every_wall.counts()["wing_end_corners"] == 24


def test_simple_shapes():
    assert counts(corner_ray_perimeter(RECT)) == (4, 1, 0, 0, 0, 0)
    assert counts(corner_ray_perimeter(COURTYARD)) == (8, 1, 0, 0, 0, 0)
    for poly in (RECT, ROUND, COURTYARD):
        check_tiling(poly, corner_ray_perimeter(poly))


def test_stages():
    poly = footprints()["Krakow_0013"]
    for stage in STAGES:
        check_tiling(poly, corner_ray_perimeter(poly, upto=stage))
    for bad in (dict(upto="nowhere"), dict(skip=("nowhere",))):
        try:
            corner_ray_perimeter(poly, **bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")


def test_deterministic():
    poly = footprints()["Valdemoro_0001"]
    a, b = corner_ray_perimeter(poly), corner_ray_perimeter(poly)
    assert [(z.wkb, k) for z, k in a.zones] == [(z.wkb, k) for z, k in b.zones]


def test_settings():
    table = settings_table()
    names = [f["name"] for section in table for f in section["fields"]]
    assert sorted(names) == sorted(f.name for f in fields(CornerRayConfig))
    for section in table:
        for f in section["fields"]:
            if f["kind"] == "number":
                assert f["ge"] <= f["lo"] <= f["default"] <= f["hi"] <= f["le"], f["name"]
            if f["needs"]:
                assert f["needs"] in names
    for bad in (dict(band_m=-1.0), dict(fill_notches=1.0), dict(relink_m="far")):
        try:
            CornerRayConfig(**bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")
    assert CornerRayConfig(band_m=5.0).changed() == {"band_m": 5.0}


def test_preview():
    got = preview(footprints()["Bognor_Regis_0005"])
    assert got["fate"] == {"status": "kept"}
    assert set(got["layers"]) == set(LAYERS["before"]) | set(LAYERS["after"])
    assert [k for k, _, _ in NUMBERS] == list(got["numbers"])
    assert got["numbers"]["zones"] == 38 and got["numbers"]["wing_end_corners"] == 4
    rows = zone_records(got["result"])
    assert len(rows) == 40 and {r["kind"] for r in rows} == {"zone", "core"}
    failed = preview(LineString([(0, 0), (1, 1)]))
    assert failed["fate"]["status"] == "dropped" and failed["fate"]["reason"] == "perimeter_failed"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
