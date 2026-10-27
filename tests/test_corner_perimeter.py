"""Checks for the corner-ray perimeter (``convexdecomp.corner_perimeter``).

Runs under pytest, or directly with ``python tests/test_corner_perimeter.py``. The four real footprints in
``tests/data/corner_perimeter_footprints.json`` (WKB, local metres) are the test footprints of the method; five more
footprints show rules 7, 9 and 10 and the wing-end check at work. ``EXPECTED`` holds their counts with the defaults,
``EXPECTED_OFF`` those with the short walls, bays and tidy step switched off (``OFF``). Tests of one step switch the
others off, so that each shows its own step.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import fields, replace
from pathlib import Path

import numpy as np
from shapely import union_all, wkb
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.corner_perimeter import (LAYERS, NUMBERS, STAGES, CornerRayConfig, corner_ray_perimeter,
                                           narrow_arms, preview, settings_table, sharp_corners, zone_records)
from convexdecomp.corner_perimeter.geometry import insert_point, is_convex, zone_across
from convexdecomp.corner_perimeter.zones import cut_sharp_corners, join_specks

DATA = Path(__file__).resolve().parent / "data" / "corner_perimeter_footprints.json"

RECT = box(0, 0, 20, 12)
ROUND = Polygon([(12 * math.cos(2 * math.pi * k / 24), 12 * math.sin(2 * math.pi * k / 24)) for k in range(24)])
COURTYARD = Polygon(box(0, 0, 30, 30).exterior.coords, [box(10, 10, 20, 20).exterior.coords])
#: a 30 m x 12 m block with a five-sided bay 3 m wide and 1.3 m deep on its south facade, and one with a 6 m x 3 m
#: rectangular bump there instead
BAY_CAP = [(13, 0), (13.2, -0.8), (14, -1.3), (15, -1.3), (15.8, -0.8), (16, 0)]
BAY = Polygon([(0, 0), *BAY_CAP, (30, 0), (30, 12), (0, 12)])
BUMP = Polygon([(0, 0), (12, 0), (12, -3), (18, -3), (18, 0), (30, 0), (30, 12), (0, 12)])

#: the short walls, bays and tidy step switched off: the rays come from every corner but the wing ends, and the
#: zones end with rule 10
OFF = dict(short_wall_one_ray=False, bays=False, snap_to_outline=False, merge_close_again=False,
           straighten_corners=False, drop_collinear=False, split_pinched=False)

#: zones, core parts, thin, not convex, corners under 20 degrees, wing-end corners, with the defaults
EXPECTED = {
    "osm_0499": (40, 3, 0, 7, 0, 0),    # two bay corners, tidied corners
    "osm_0699": (27, 2, 0, 4, 0, 8),       # two short walls
    "osm_0353": (35, 2, 0, 2, 0, 4),  # six bay corners, four short walls
    "osm_0351": (4, 0, 0, 0, 0, 2),
    "osm_0738": (14, 1, 0, 0, 0, 0),    # tidy snaps a corner to the outline and splits a pinched zone
    "osm_0377": (20, 1, 0, 1, 0, 0),    # its two round bays are one zone each
    "osm_0915": (3, 0, 0, 0, 0, 0),
    "osm_0719": (21, 1, 0, 3, 0, 0),
    "osm_0656": (10, 0, 0, 1, 0, 0),
}

#: the same with OFF
EXPECTED_OFF = {
    "osm_0499": (40, 3, 1, 8, 0, 0),
    "osm_0699": (28, 2, 1, 6, 0, 8),
    "osm_0353": (38, 2, 0, 2, 0, 4),
    "osm_0351": (4, 0, 0, 0, 0, 2),
    # footprints that show one rule or check at work
    "osm_0738": (13, 1, 0, 1, 0, 0),    # rule 9 hands a 38.8 m² triangle over whole
    "osm_0377": (27, 1, 0, 1, 0, 0),    # rule 7: the hull would leave its neighbour 1.52 m wide
    "osm_0915": (3, 0, 0, 0, 0, 0),         # the wing-end check gives the narrow part's end its rays back
    "osm_0719": (23, 1, 1, 5, 0, 0),         # rule 10: three specks join a neighbour
    "osm_0656": (11, 0, 1, 2, 0, 0),       # rule 9: two zones share the shorter edge; the longer share takes it
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
        off = corner_ray_perimeter(poly, CornerRayConfig(**OFF))
        assert counts(off) == EXPECTED_OFF[sid], (sid, counts(off))
        check_tiling(poly, off)


def test_wing_ends_on_a_narrow_leg():
    poly = footprints()["osm_0351"]
    with_rays = corner_ray_perimeter(poly, CornerRayConfig(**OFF, wing_end_max_m=0.0))
    assert counts(with_rays) == (6, 0, 4, 0, 0, 0)  # the leg's end rays split it into strips under 2 m
    assert counts(corner_ray_perimeter(poly)) == (4, 0, 0, 0, 0, 2)


def test_rules_and_checks_on_their_footprints():
    f = footprints()

    def run(sid, **changes):
        return corner_ray_perimeter(f[sid], CornerRayConfig(**{**OFF, **changes}))

    assert run("osm_0738").counts()["sharp_whole"] == 1
    assert counts(run("osm_0377", hull_keeps_width=False)) == (27, 1, 1, 0, 0, 0)  # neighbour left 1.52 m wide
    derby = run("osm_0915")
    assert derby.counts()["wing_ends_restored"] == 2 and not derby.wing_ends
    assert counts(run("osm_0915", wing_end_check=False)) == (4, 0, 1, 0, 0, 2)
    assert run("osm_0719", join_specks=False).counts()["specks"] == 2
    assert run("osm_0719").counts()["specks_joined"] == 3


def test_rule9_hands_a_cascading_triangle_over_whole():
    # a 10° triangle whose third corner is 80°: cut, it would leave a similar triangle, again and again
    tip_y = 10 * math.tan(math.radians(10.0))
    below, above = box(0, -3, 10, 0), Polygon([(0, 0), (10, tip_y), (10, 4), (0, 4)])
    triangle = Polygon([(0, 0), (10, 0), (10, tip_y)])
    zones, cuts, whole = cut_sharp_corners([(below, "zone"), (triangle, "zone"), (above, "zone")], CornerRayConfig())
    assert len(zones) == 2 and len(whole) == 1 and not cuts
    assert sharp_corners(zones) == 0 and abs(sum(z.area for z, _ in zones) - 70.0) < 1e-9


def test_rule9_keeps_points_the_outline_runs_almost_straight_through():
    # The long edge of the sharp corner at (0, 0) bends by 0.03 degrees at v, a point it shares with the zone below.
    v = (10.0, -0.005)
    zones = [(Polygon([(0, 0), (4, 1), (20, 6), (20, 10), (0, 10)]), "zone"),
             (Polygon([(0, 0), v, (20, 0), (20, 6), (4, 1)]), "zone"),
             (Polygon([(0, 0), (0, -5), (20, -5), (20, 0), v]), "zone")]
    out, cuts, _ = cut_sharp_corners(zones, replace(CornerRayConfig(), sharp_keep_convex=False))
    union = union_all([z for z, _ in out])
    assert len(cuts) == 1
    assert union.symmetric_difference(box(0, -5, 20, 10)).area < 1e-9
    assert math.isclose(sum(z.area for z, _ in out), union.area, abs_tol=1e-9)
    assert all(v in z.exterior.coords for z, _ in out[1:])


def test_rule1_ends_a_merged_ray_that_runs_along_the_band_line():
    # A bar with two steps: the end walls' rays merge 0.1 m from the band line and would run along it, leaving a
    # sliver arm on the zone of a step (a shape derived from a real footprint, on a 10 cm grid).
    bar = Polygon([(22.9, 0), (22.9, 8.8), (18.2, 8.8), (18.1, 11), (6.4, 11), (6.4, 8.8), (0, 8.8), (0.3, 0)])
    off = corner_ray_perimeter(bar, CornerRayConfig(merged_ray_band_m=0.0)).counts()
    assert off["narrow_arms"] == 1 and off["not_convex"] == 1 and off["merged_rays_to_band"] == 0
    result = corner_ray_perimeter(bar)
    on = result.counts()
    assert on["merged_rays_to_band"] == 1 and on["narrow_arms"] == 0 and on["not_convex"] == 0
    check_tiling(bar, result)


def test_narrow_arms():
    arm = Polygon([(0, 0), (7, 0), (7, 0.2), (4, 0.2), (4, 4), (0, 4)])  # a 3 m x 0.2 m arm
    found = narrow_arms([(arm, "zone"), (box(0, 4, 4, 8), "zone")])
    assert len(found) == 1 and math.isclose(found[0].area, 0.6, rel_tol=1e-6)
    assert not narrow_arms([(Polygon([(0, 0), (7, 0), (7, 0.4), (4, 0.4), (4, 4), (0, 4)]), "zone")])


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


def holder(result, cap: Polygon) -> list[Polygon]:
    """The zones that hold the whole of cap."""
    return [z for z, _ in result.zones if z.buffer(1e-6).covers(cap)]


def test_bays():
    bays = CornerRayConfig(**{**OFF, "bays": True})
    # the bay's four corners cast no ray: the bay and the triangle behind its opening are one convex zone
    result = corner_ray_perimeter(BAY, bays)
    assert result.counts()["bay_corners"] == 4
    zone = holder(result, Polygon(BAY_CAP))
    assert len(zone) == 1 and is_convex(zone[0]) and zone[0].area > Polygon(BAY_CAP).area + 1.0
    # a rectangular bump counts with two corners, not with three
    assert counts(corner_ray_perimeter(BUMP, CornerRayConfig(**OFF))) == (8, 1, 0, 0, 0, 0)
    bump = corner_ray_perimeter(BUMP, bays)
    assert counts(bump) == (6, 1, 0, 0, 0, 0) and bump.counts()["bay_corners"] == 2
    assert len(holder(bump, box(12, -3, 18, 0))) == 1
    assert corner_ray_perimeter(BUMP, CornerRayConfig(**{**OFF, "bays": True, "bay_min_corners": 3})
                                ).counts()["bay_corners"] == 0
    # a round building has no reflex corner, so no bay: it keeps every ray
    assert corner_ray_perimeter(ROUND, bays).counts()["bay_corners"] == 0
    for poly in (BAY, BUMP):
        check_tiling(poly, corner_ray_perimeter(poly, bays))


def test_bays_on_real_footprints():
    bays = CornerRayConfig(**{**OFF, "bays": True})
    # osm_0377's two round bays (9.1 m wide, 5 m deep, 11 and 12 corners) each become one zone, and a 2-corner
    # bump loses its rays too: 27 zones become 20
    poly = footprints()["osm_0377"]
    result = corner_ray_perimeter(poly, bays)
    assert counts(result)[:5] == (20, 1, 0, 1, 0) and result.counts()["bay_corners"] == 25
    check_tiling(poly, result)
    # osm_0699's and osm_0351's wing ends stay wing ends, and nothing else of them changes
    for sid in ("osm_0699", "osm_0351"):
        assert counts(corner_ray_perimeter(footprints()[sid], bays)) == EXPECTED_OFF[sid]


def test_simple_shapes():
    assert counts(corner_ray_perimeter(RECT)) == (4, 1, 0, 0, 0, 0)
    assert counts(corner_ray_perimeter(COURTYARD)) == (8, 1, 0, 0, 0, 0)
    for poly in (RECT, ROUND, COURTYARD):
        check_tiling(poly, corner_ray_perimeter(poly))


def test_stages():
    poly = footprints()["osm_0699"]
    for stage in STAGES:
        check_tiling(poly, corner_ray_perimeter(poly, upto=stage))
    for bad in (dict(upto="nowhere"), dict(skip=("nowhere",))):
        try:
            corner_ray_perimeter(poly, **bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")


def test_deterministic():
    poly = footprints()["osm_0499"]
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
    got = preview(footprints()["osm_0353"])
    assert got["fate"] == {"status": "kept"}
    assert set(got["layers"]) == set(LAYERS["before"]) | set(LAYERS["after"])
    assert [k for k, _, _ in NUMBERS] == list(got["numbers"])
    assert got["numbers"]["zones"] == 35 and got["numbers"]["wing_end_corners"] == 4
    assert got["numbers"]["bay_corners"] == 6 and got["numbers"]["short_walls"] == 4
    rows = zone_records(got["result"])
    assert len(rows) == 37 and {r["kind"] for r in rows} == {"zone", "core"}
    failed = preview(LineString([(0, 0), (1, 1)]))
    assert failed["fate"]["status"] == "dropped" and failed["fate"]["reason"] == "perimeter_failed"


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
