"""Checks the fallback of `run_building_decomposition` for pieces the engine cannot cut.

Runs under pytest, or directly with ``python tests/test_decomposition_fallback.py``.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.osm.decompose import ConvexDecompositionConfig  # noqa: E402
from convexdecomp.osm.diagnostics import check_area_conservation, run_building_decomposition  # noqa: E402
from convexdecomp.zones.adapters import osm_part_records  # noqa: E402
from convexdecomp.zones.interfaces import InterfaceConfig, build_interfaces, summarize_interfaces  # noqa: E402

CFG = ConvexDecompositionConfig()

# A 1 m strip along a 10 x 6 block: thinner than the 1.2 m width rule on its own, one rectangle with the block.
STRIP_AND_BLOCK = [(box(0, 0, 10, 1), "thin_zone"), (box(0, 1, 10, 6), "perimeter_zone")]
# A 1 m wide piece whose union with either neighbour keeps a 1 m arm; only the whole 10 x 10 square decomposes.
ARM = box(0, 0, 1, 6)
BELOW = box(1, 0, 10, 5)
ABOVE = Polygon([(1, 5), (10, 5), (10, 10), (0, 10), (0, 6), (1, 6)])
ARM_PIECES = [(ARM, "thin_zone"), (BELOW, "perimeter_zone"), (ABOVE, "perimeter_zone")]


def table(buildings: dict[str, list[tuple[Polygon, str]]]) -> gpd.GeoDataFrame:
    rows = []
    for sample_id, entries in buildings.items():
        footprint = unary_union([poly for poly, _ in entries])
        rows.append({"sample_id": sample_id, "geometry": footprint, "perimeter_defined": False,
                     "decomp_polygons": [poly for poly, _ in entries], "decomp_polygon_roles": [role for _, role in entries]})
    return gpd.GeoDataFrame(rows, geometry="geometry")


def test_without_the_fallback_the_strip_fails():
    buildings, _, _ = run_building_decomposition(table({"b": STRIP_AND_BLOCK}), CFG)
    row = buildings.iloc[0]
    assert not row.building_fully_convex and row.fallback_applied is None


def test_a_failing_piece_merges_with_its_neighbour():
    buildings, _, polygons = run_building_decomposition(table({"b": STRIP_AND_BLOCK}), CFG, fallback="merge")
    row = buildings.iloc[0]
    assert row.building_fully_convex and row.fallback_applied == "merge" and row.n_fallback_merges == 1
    assert row.final_polygon_roles == ["merged_zone"]
    merged = polygons.iloc[0]
    assert merged.zone_id == "b_merged_01" and merged.merged_from == ["b_poly_01", "b_poly_02"]
    assert math.isclose(sum(p.area for p in merged.best_variant_parts), 60.0, rel_tol=1e-9)
    assert check_area_conservation(buildings).empty
    assert row.fallback_seconds >= 0.0 and row.time_total_seconds >= row.fallback_seconds


def test_when_no_merge_helps_the_whole_footprint_is_decomposed():
    buildings, _, polygons = run_building_decomposition(table({"b": ARM_PIECES}), CFG, fallback="merge")
    row = buildings.iloc[0]
    assert row.building_fully_convex and row.fallback_applied == "whole"
    assert list(polygons.zone_role) == ["footprint"] and list(polygons.zone_id) == ["b_footprint_01"]
    assert math.isclose(sum(p.area for p in polygons.iloc[0].best_variant_parts), 100.0, rel_tol=1e-9)
    assert check_area_conservation(buildings).empty


def test_a_footprint_that_cannot_be_cut_stays_failed():
    buildings, _, polygons = run_building_decomposition(table({"b": [(box(0, 0, 10, 1), "footprint")]}), CFG,
                                                        fallback="merge")
    row = buildings.iloc[0]
    assert not row.building_fully_convex and row.fallback_applied is None
    assert list(polygons.zone_role) == ["footprint"]


def test_merged_zones_tile_their_building_in_the_provenance_export():
    buildings, _, polygons = run_building_decomposition(
        table({"strip": STRIP_AND_BLOCK, "arm": ARM_PIECES}), CFG, fallback="merge")
    assert buildings.building_fully_convex.all()
    parts = osm_part_records(polygons)
    summary = summarize_interfaces(parts, build_interfaces(parts, InterfaceConfig(tol=1e-6)))
    assert summary["n_unmatched"] == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
