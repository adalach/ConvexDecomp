"""Checks for a building's convex zones (``convexdecomp.convex_zones``).

Runs under pytest, or directly with ``python tests/test_convex_zones.py``. The synthetic pieces are those of
``test_decomposition_fallback.py``; the real footprints are the corner-ray perimeter's test footprints.
"""

from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import fields, replace
from pathlib import Path

from shapely import union_all, wkb
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.convex_zones import (LAYERS, NUMBERS, ZoneConfig, decompose_pieces, decompose_zones,  # noqa: E402
                                       engine_call, engine_config, piece_records, preview, settings_table,
                                       zone_records)
from convexdecomp.core.search import AdaptiveSearchConfig  # noqa: E402
from convexdecomp.corner_perimeter import corner_ray_perimeter  # noqa: E402
from convexdecomp.osm.decompose import ConvexDecompositionConfig  # noqa: E402

DATA = Path(__file__).resolve().parent / "data" / "corner_perimeter_footprints.json"

# A 1 m strip along a 10 x 6 block: thinner than the 1.2 m width rule on its own, one rectangle with the block.
STRIP_AND_BLOCK = [(box(0, 0, 10, 1), "thin"), (box(0, 1, 10, 6), "zone")]
# A 1 m wide piece whose union with either neighbour keeps a 1 m arm; only the whole 10 x 10 square decomposes.
ARM_PIECES = [(box(0, 0, 1, 6), "thin"), (box(1, 0, 10, 5), "zone"),
              (Polygon([(1, 5), (10, 5), (10, 10), (0, 10), (0, 6), (1, 6)]), "zone")]


def footprints() -> dict[str, Polygon]:
    return {sid: wkb.loads(bytes.fromhex(h)) for sid, h in json.loads(DATA.read_text()).items()}


def tiles(result, footprint) -> bool:
    parts = [p for p, _ in result.parts]
    return math.isclose(sum(p.area for p in parts), footprint.area, rel_tol=1e-9) and \
        union_all(parts).symmetric_difference(footprint).area < 1e-6


def test_settings():
    table = settings_table()
    names = [f["name"] for section in table for f in section["fields"]]
    assert sorted(names) == sorted(f.name for f in fields(ZoneConfig))
    switches = {f["name"] for section in table for f in section["fields"] if f["kind"] == "switch"}
    for section in table:
        for f in section["fields"]:
            if f["kind"] != "switch":
                assert f["ge"] <= f["lo"] <= f["default"] <= f["hi"] <= f["le"], f["name"]
            assert f["kind"] != "integer" or isinstance(f["default"], int)
            if f["needs"]:
                assert f["needs"] in switches
    for bad in (dict(min_zone_width_m=-1.0), dict(depth=2.5), dict(merge_failed=1), dict(width="wide")):
        try:
            ZoneConfig(**bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} was accepted")
    assert ZoneConfig(depth=30).changed() == {"depth": 30}


def test_the_default_engine_is_osm_2s():
    osm_2 = ConvexDecompositionConfig(
        search_depth=24, search_width=4, max_reflex_per_node=8, max_variants_per_polygon=32, max_failed_states=500,
        track_time=True, min_area_m2=2.0, weight_parts=1.0, weight_compactness=20.0,
        adaptive_search=AdaptiveSearchConfig(enabled=True, initial_depth=4, initial_width=3, max_depth=24,
                                             max_width=4, depth_step=1, width_step=1))
    assert engine_config() == osm_2
    flat = engine_config(ZoneConfig(adaptive=False)).adaptive_search
    assert flat.initial_depth is None and flat.max_depth == 24


def test_a_rectangle_keeps_its_perimeter_zones():
    footprint = box(0, 0, 20, 12)
    got = preview(footprint)
    assert got["fate"] == {"status": "kept", "fallback": None}
    assert set(got["layers"]) == set(LAYERS["before"]) | set(LAYERS["after"])
    assert [k for k, _, _ in NUMBERS] == list(got["numbers"])
    n = got["numbers"]
    assert (n["zones_in"], n["convex_in"], n["zones"], n["cuts"], n["merges"]) == (5, 5, 5, 0, 0)
    assert len(got["layers"]["perimeter_parts"]) == 4 and len(got["layers"]["core_parts"]) == 1
    assert len(got["layers"]["zone_lines"]) == 8 and not got["layers"]["cut_lines"]
    assert tiles(got["result"], footprint)


def test_a_strip_merges_with_its_neighbour():
    result = decompose_zones(STRIP_AND_BLOCK)
    assert result.ok and result.fallback == "merge" and result.merges == 1
    assert [(p.kind, p.sources) for p in result.pieces] == [("merged", (0, 1))]
    assert tiles(result, box(0, 0, 10, 6))
    assert not decompose_zones(STRIP_AND_BLOCK, ZoneConfig(merge_failed=False, whole_footprint=False)).ok


def test_when_no_merge_helps_the_whole_footprint_is_decomposed():
    result = decompose_zones(ARM_PIECES)
    assert result.ok and result.fallback == "whole"
    assert [(p.kind, p.sources) for p in result.pieces] == [("footprint", (0, 1, 2))]
    assert tiles(result, box(0, 0, 10, 10))
    got = preview(box(0, 0, 10, 10), ARM_PIECES)
    assert got["numbers"]["whole"] == 1 and got["numbers"]["failed_first"] == 1
    assert len(got["layers"]["removed_lines"]) >= 2 and not got["layers"]["zone_lines"]


def test_a_footprint_that_cannot_be_cut_is_dropped():
    got = preview(box(0, 0, 10, 1), [(box(0, 0, 10, 1), "footprint")])
    assert got["fate"]["status"] == "dropped" and got["fate"]["reason"] == "decomposition_failed"
    assert got["result"].fallback is None and len(got["layers"]["failed_pieces"]) == 1


def test_the_core_is_searched_last_and_sources_keep_the_given_order():
    seen = []

    def decompose(polygon, max_seconds):
        seen.append(round(polygon.area))
        return {"fully_convex_best": True, "best_variant": [polygon], "time_seconds": 0.0}

    zones = [(box(0, 0, 4, 4), "core"), (box(4, 0, 6, 4), "zone"), (box(6, 0, 9, 4), "thin")]
    result = decompose_pieces(zones, decompose)
    assert seen == [8, 12, 16]
    assert [p.sources for p in result.pieces] == [(1,), (2,), (0,)]


def test_the_fallback_stops_when_its_time_is_up():
    calls = []

    def slow(polygon, max_seconds):
        calls.append(max_seconds)
        if max_seconds is None:                    # the first pass: the strip cannot be cut
            return engine_call(engine_config())(polygon)
        time.sleep(0.05)
        return {"fully_convex_best": False, "best_variant": [], "time_seconds": 0.05}

    result = decompose_pieces(ARM_PIECES, slow, footprint=box(0, 0, 10, 10), max_seconds=0.12)
    assert not result.ok and result.out_of_time and result.fallback is None
    assert calls[:3] == [None, None, None] and all(0.0 < t <= 0.12 for t in calls[3:])
    assert len(calls) <= 6                         # the budget runs out after about two fallback searches
    got = preview(box(0, 0, 10, 10), ARM_PIECES, ZoneConfig(fallback_max_seconds=0.12), decompose=slow)
    assert got["fate"]["detail"]["out_of_time"] and got["numbers"]["out_of_time"] == 1
    assert decompose_zones(ARM_PIECES, ZoneConfig(fallback_max_seconds=0.0)).fallback == "whole"


def test_the_engine_stops_at_its_time_limit():
    from convexdecomp.osm.decompose import decompose_polygon_with_stats
    comb = Polygon([(0, 0), (40, 0), (40, 10)] + [(x, 10 + 6 * (k % 2)) for k, x in enumerate(range(38, 0, -2))]
                   + [(0, 10)])
    cfg = replace(engine_config(), max_seconds=0.0)
    got = decompose_polygon_with_stats(comb, cfg)
    assert got["search_terminated_by"] == "time_limit" and not got["fully_convex_best"]


def test_an_engine_error_is_a_fate():
    def broken(polygon, max_seconds):
        raise RuntimeError("stuck")

    got = preview(box(0, 0, 20, 12), decompose=broken)
    assert got["fate"]["reason"] == "decomposition_error" and got["result"] is None


def test_real_footprints():
    for sid, footprint in footprints().items():
        if sid in ("osm_0719",):
            continue
        result = decompose_zones(corner_ray_perimeter(footprint).zones, footprint=footprint)
        assert result.ok, sid
        assert tiles(result, footprint), sid
        rows = zone_records(result)
        assert all(r["min_width_m"] >= 1.2 - 1e-9 for r in rows), sid
        assert len(piece_records(result)) == len(result.pieces)


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
            print("ok", name)
