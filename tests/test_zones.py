"""Checks for the zone provenance and boundary-interface classifier.

Runs under pytest, or directly with ``python tests/test_zones.py`` so the
repository keeps working without an extra dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.zones.adapters import build_part_records
from convexdecomp.zones.export import (
    interfaces_to_geodataframe,
    parts_to_geodataframe,
    write_zone_artifacts,
)
from convexdecomp.zones.interfaces import (
    InterfaceConfig,
    build_interfaces,
    summarize_interfaces,
)


def _length_by_kind(interfaces):
    totals = {}
    for record in interfaces:
        totals[record.boundary_kind] = totals.get(record.boundary_kind, 0.0) + record.length
    return {kind: round(value, 6) for kind, value in totals.items()}


def _one_zone(parts, geometry, container="c", space="c/s", zone="c/s"):
    return build_part_records(
        [{"container_id": container, "space_id": space, "zone_id": zone, "geometry": geometry, "parts": parts}]
    )


def test_l_shaped_room_split_into_two_rectangles():
    """The example from the paper note: one L-shaped room, one artificial wall."""
    room = Polygon([(0, 0), (8, 0), (8, 3), (4, 3), (4, 6), (0, 6)])
    tall = Polygon([(0, 0), (4, 0), (4, 6), (0, 6)])
    short = Polygon([(4, 0), (8, 0), (8, 3), (4, 3)])

    parts = _one_zone([tall, short], room)
    interfaces = build_interfaces(parts)

    artificial = [r for r in interfaces if r.boundary_kind == "artificial_split"]
    assert len(artificial) == 2, "one record per side of the shared wall"
    assert all(abs(r.length - 3.0) < 1e-9 for r in artificial)
    assert len({r.interface_key for r in artificial}) == 1, "both sides share one key"
    assert all(r.is_virtual and r.same_space_group for r in artificial)

    # The tall part's x=4 edge is 6 long: 3 shared with the short part, 3 on the notch.
    on_cut_line = [r for r in interfaces if r.part_id.endswith("part_00") and abs(r.geometry.centroid.x - 4) < 1e-9]
    assert sorted(round(r.length, 6) for r in on_cut_line) == [3.0, 3.0]
    assert {r.boundary_kind for r in on_cut_line} == {"artificial_split", "exterior"}


def test_partial_overlap_at_a_t_junction():
    """One long edge facing two stacked parts must split into two sub-segments."""
    left = Polygon([(0, 0), (4, 0), (4, 6), (0, 6)])
    lower_right = Polygon([(4, 0), (7, 0), (7, 2), (4, 2)])
    upper_right = Polygon([(4, 2), (7, 2), (7, 6), (4, 6)])
    source = Polygon([(0, 0), (7, 0), (7, 6), (0, 6)])

    interfaces = build_interfaces(_one_zone([left, lower_right, upper_right], source))

    facing = [r for r in interfaces if r.part_id.endswith("part_00") and abs(r.geometry.centroid.x - 4) < 1e-9]
    assert sorted(round(r.length, 6) for r in facing) == [2.0, 4.0]
    assert {r.neighbor_part_id.rsplit("/", 1)[-1] for r in facing} == {"part_01", "part_02"}
    assert not any(r.is_ambiguous for r in interfaces)
    assert "unmatched" not in _length_by_kind(interfaces)


def test_a_gap_is_reported_instead_of_being_called_an_outer_wall():
    """Parts that do not tile their source zone must surface, not hide as exterior."""
    left = Polygon([(0, 0), (4, 0), (4, 6), (0, 6)])
    detached_right = Polygon([(4.5, 0), (7, 0), (7, 6), (4.5, 6)])
    source = Polygon([(0, 0), (7, 0), (7, 6), (0, 6)])

    interfaces = build_interfaces(_one_zone([left, detached_right], source))
    totals = _length_by_kind(interfaces)

    assert totals["unmatched"] == 12.0, "both facing edges, 6 each"
    assert "artificial_split" not in totals


def test_cuts_that_are_not_axis_aligned():
    quad = Polygon([(0, 0), (6, 0), (6, 4), (0, 4)])
    lower = Polygon([(0, 0), (6, 0), (6, 4)])
    upper = Polygon([(0, 0), (6, 4), (0, 4)])

    interfaces = build_interfaces(_one_zone([lower, upper], quad))
    diagonal = (6 ** 2 + 4 ** 2) ** 0.5

    assert abs(_length_by_kind(interfaces)["artificial_split"] - 2 * diagonal) < 1e-6
    assert "unmatched" not in _length_by_kind(interfaces)


def test_perimeter_zoning_is_distinct_from_a_convexity_cut():
    """An OSM-style building: one space, several deliberate zones inside it."""
    core = Polygon([(2, 2), (8, 2), (8, 8), (2, 8)])
    trapezoids = [
        Polygon([(0, 0), (10, 0), (8, 2), (2, 2)]),
        Polygon([(10, 0), (10, 10), (8, 8), (8, 2)]),
        Polygon([(10, 10), (0, 10), (2, 8), (8, 8)]),
        Polygon([(0, 10), (0, 0), (2, 2), (2, 8)]),
    ]
    sources = [
        {
            "container_id": "osm_1",
            "space_id": "osm_1",
            "zone_id": f"osm_1_poly_{index:02d}",
            "space_label": "building",
            "zone_role": "perimeter_trapezoid",
            "geometry": geom,
            "parts": [geom],
        }
        for index, geom in enumerate(trapezoids, start=1)
    ]
    sources.append(
        {
            "container_id": "osm_1",
            "space_id": "osm_1",
            "zone_id": "osm_1_poly_05",
            "space_label": "building",
            "zone_role": "interior",
            "geometry": core,
            "parts": [core],
        }
    )

    interfaces = build_interfaces(build_part_records(sources))
    totals = _length_by_kind(interfaces)

    assert "artificial_split" not in totals, "no convexity cut happened here"
    assert totals["zoning_split"] > 0
    assert totals["exterior"] == 40.0
    zoning = [r for r in interfaces if r.boundary_kind == "zoning_split"]
    assert all(r.is_virtual and r.same_space_group for r in zoning)


def test_two_buildings_sharing_a_party_wall():
    left = Polygon([(0, 0), (5, 0), (5, 5), (0, 5)])
    right = Polygon([(5, 0), (9, 0), (9, 5), (5, 5)])
    parts = build_part_records(
        [
            {"container_id": "block", "space_id": "bldg_a", "zone_id": "bldg_a", "geometry": left, "parts": [left]},
            {"container_id": "block", "space_id": "bldg_b", "zone_id": "bldg_b", "geometry": right, "parts": [right]},
        ]
    )
    interfaces = build_interfaces(parts)

    assert _length_by_kind(interfaces)["space_boundary"] == 10.0
    assert not any(r.is_virtual for r in interfaces), "a party wall is a real wall"


def test_every_part_perimeter_is_fully_accounted_for():
    room = Polygon([(0, 0), (8, 0), (8, 3), (4, 3), (4, 6), (0, 6)])
    tall = Polygon([(0, 0), (4, 0), (4, 6), (0, 6)])
    short = Polygon([(4, 0), (8, 0), (8, 3), (4, 3)])
    parts = _one_zone([tall, short], room)
    interfaces = build_interfaces(parts)

    emitted = sum(r.length for r in interfaces)
    perimeter = sum(p.geometry.length for p in parts)
    assert abs(emitted - perimeter) < 1e-9


def test_a_failed_decomposition_still_keeps_its_source_geometry():
    source = Polygon([(0, 0), (7, 0), (7, 6), (0, 6)])
    parts = _one_zone([], source)

    assert len(parts) == 1
    assert parts[0].attributes["decomposition_failed"] is True


def test_summary_counts_split_spaces():
    room = Polygon([(0, 0), (8, 0), (8, 3), (4, 3), (4, 6), (0, 6)])
    parts = _one_zone(
        [Polygon([(0, 0), (4, 0), (4, 6), (0, 6)]), Polygon([(4, 0), (8, 0), (8, 3), (4, 3)])], room
    )
    summary = summarize_interfaces(parts, build_interfaces(parts))

    assert summary["n_parts"] == 2
    assert summary["n_spaces"] == 1
    assert summary["n_split_spaces"] == 1
    assert summary["n_unmatched"] == 0
    assert summary["n_ambiguous"] == 0


def test_export_round_trip(tmp_path=None):
    import tempfile

    import geopandas as gpd

    directory = Path(tmp_path) if tmp_path is not None else Path(tempfile.mkdtemp())
    room = Polygon([(0, 0), (8, 0), (8, 3), (4, 3), (4, 6), (0, 6)])
    parts = _one_zone(
        [Polygon([(0, 0), (4, 0), (4, 6), (0, 6)]), Polygon([(4, 0), (8, 0), (8, 3), (4, 3)])], room
    )
    interfaces = build_interfaces(parts)

    target = directory / "zones.gpkg"
    write_zone_artifacts(parts_to_geodataframe(parts), interfaces_to_geodataframe(interfaces), target)

    zones = gpd.read_file(target, layer="zones")
    faces = gpd.read_file(target, layer="interfaces")

    assert len(zones) == len(parts) and len(faces) == len(interfaces)
    assert bool(zones["space_is_split"].all())
    assert set(faces["boundary_kind"]) == {"exterior", "artificial_split"}


def test_empty_input_does_not_raise():
    assert build_interfaces([]) == []
    assert parts_to_geodataframe([]).empty
    assert interfaces_to_geodataframe([]).empty


def test_tolerance_is_configurable():
    """A sub-millimetre seam pairs up only once the tolerance allows it."""
    left = Polygon([(0, 0), (4, 0), (4, 6), (0, 6)])
    right = Polygon([(4.0002, 0), (7, 0), (7, 6), (4.0002, 6)])
    source = Polygon([(0, 0), (7, 0), (7, 6), (0, 6)])

    strict = build_interfaces(_one_zone([left, right], source), InterfaceConfig(tol=1e-6))
    loose = build_interfaces(_one_zone([left, right], source), InterfaceConfig(tol=1e-3))

    assert "artificial_split" not in _length_by_kind(strict)
    assert _length_by_kind(loose)["artificial_split"] == 12.0


if __name__ == "__main__":
    failures = 0
    for name, func in sorted(globals().items()):
        if not name.startswith("test_") or not callable(func):
            continue
        try:
            func()
        except AssertionError as error:
            failures += 1
            print(f"FAIL {name}: {error}")
        else:
            print(f"ok   {name}")
    print("\nall passed" if not failures else f"\n{failures} failed")
    raise SystemExit(1 if failures else 0)
