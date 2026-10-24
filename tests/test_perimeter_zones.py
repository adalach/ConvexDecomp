"""Checks for the corner-ray perimeter on a table of OSM footprints (``convexdecomp.osm.perimeter_zones``).

Runs under pytest, or directly with ``python tests/test_perimeter_zones.py`` so the
repository keeps working without an extra dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.osm.diagnostics import area_ledger, prepare_decomposition_inputs
from convexdecomp.osm.perimeter_zones import RESULT_COLUMNS, build_corner_perimeter_for_gdf, summarize_corner_perimeter

RECT = box(0, 0, 20, 12)
NARROW = box(0, 0, 12, 6)  # no core: the band covers it
COURTYARD = Polygon(box(0, 0, 30, 30).exterior.coords, [box(10, 10, 20, 20).exterior.coords])
#: an invalid footprint (a bow tie): the perimeter fails, and the footprint is decomposed whole
BOWTIE = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])


def frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"sample_id": ["rect", "narrow", "courtyard", "bowtie"],
                             "geometry": [RECT, NARROW, COURTYARD, BOWTIE]}, geometry="geometry")


def test_columns_and_counts():
    g = build_corner_perimeter_for_gdf(frame()).set_index("sample_id")
    assert set(RESULT_COLUMNS) <= set(g.columns)
    assert (g.loc["rect", "n_perimeter_zones"], g.loc["rect", "n_core_parts"]) == (4, 1)
    assert (g.loc["courtyard", "n_perimeter_zones"], g.loc["courtyard", "n_core_parts"]) == (8, 1)
    assert g.loc["rect", "perimeter"] and not g.loc["rect", "perimeter_only"]
    assert g.loc["narrow", "perimeter_only"] and g.loc["narrow", "interior_geom"] is None
    assert not g.loc["bowtie", "perimeter_defined"] and g.loc["bowtie", "perimeter_reason"] == "perimeter_failed"
    s = summarize_corner_perimeter(g)
    assert (s["n_total"], s["n_with_core"], s["n_perimeter_only"], s["n_failed"]) == (4, 2, 1, 1)


def test_zones_tile_the_footprint():
    g = build_corner_perimeter_for_gdf(frame())
    for _, row in g.loc[g["perimeter_defined"]].iterrows():
        ledger = area_ledger(row, [], None)
        assert abs(ledger["perimeter_area_error_m2"]) < 1e-6 * row.geometry.area, row["sample_id"]
        assert len(row["perimeter_parts"]) == len(row["perimeter_part_roles"])
        assert set(row["perimeter_part_roles"]) <= {"perimeter_zone", "thin_zone"}


def test_decomposition_inputs_carry_the_roles():
    g = build_corner_perimeter_for_gdf(frame())
    b, *_ = prepare_decomposition_inputs(g, use_trapezoid_triangle_precheck=False, precheck_subdiv_cfg=None)
    b = b.set_index("sample_id")
    assert b.loc["rect", "decomp_polygon_roles"] == ["perimeter_zone"] * 4 + ["interior"]
    assert b.loc["bowtie", "decomp_polygon_roles"] == ["footprint"]
    assert abs(sum(p.area for p in b.loc["courtyard", "decomp_polygons"]) - COURTYARD.area) < 1e-6


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
