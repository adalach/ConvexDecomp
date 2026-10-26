"""Checks for the OSM adapter: checks, local frames, vertices, thin parts, architectural axes, review log.

Runs under pytest, or directly with ``python tests/test_preprocessing.py``.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import affinity
from shapely.geometry import Point, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.osm.axes import AxesSettings, find_axes, residual_steps, snap_to_axes
from convexdecomp.osm.axis_alignment import (EdgeAlignmentConfig, _cluster_angle_families, _raw_edges_from_polygon,
                                             _weighted_mean_alpha, align_polygon_edges_via_line_clustering)
from convexdecomp.osm import footprints
from convexdecomp.osm.footprints import footprint_fingerprint, footprint_shapes, thin_hole_counts
from convexdecomp.osm.local_frames import to_local_frames
from convexdecomp.osm.normalize import FootprintNormalizationConfig, normalize_footprint_geometry
from convexdecomp.osm.preprocess import fidelity
from convexdecomp.osm.review_log import add_marks, footprint_identity, keep_marks, mark_states, read_log
from convexdecomp.osm.rules import load_rules, parse_checks
from convexdecomp.osm.thin_parts import erase_thin_parts
from convexdecomp.osm.vertices import VertexCleanup, remove_redundant_vertices


def vertex_count(poly) -> int:
    return len(poly.exterior.coords) - 1


# ------------------------------------------------------------------------------------------------- rules
def test_repository_checks_load_and_flag_by_kind():
    rules = load_rules()
    frame = pd.DataFrame({
        "n_footprint_parts": [1, 2], "footprint_width_m": [10.0, 1.0], "footprint_length_m": [20.0, 30.0],
        "footprint_area_m2": [200.0, 15.0], "n_footprint_holes": [0, 5], "n_footprint_vertices": [4, 8],
        "min_corner_angle_deg": [90.0, 20.0], "n_thin_holes": [0, 1],
        "thin_part_share": [0.01, 0.06], "thin_part_topology_changed": [False, False],
    })
    flags = rules.evaluate(frame)
    assert flags.loc[0].tolist() == ["", "", ""]
    assert set(flags.at[1, "hard_failures"].split(";")) == {
        "footprint_not_one_polygon", "footprint_sliver", "footprint_below_min_area", "sharp_angle",
        "footprint_thin_hole", "footprint_thin_parts"}
    assert flags.at[1, "review_flags"] == "footprint_too_many_holes"
    only = rules.evaluate(frame, names=["footprint_thin_parts"])
    assert only["hard_failures"].tolist() == ["", "footprint_thin_parts"]


def test_a_check_must_read_its_columns():
    entry = {"name": "x", "kind": "remove", "first_stage": 3, "columns": ["a"], "expression": "b > 1", "meaning": ""}
    try:
        parse_checks([entry], {})
    except ValueError as error:
        assert "reads" in str(error)
    else:
        raise AssertionError("a check reading another column than its columns was accepted")


# ---------------------------------------------------------------------------------------------- measures
def test_footprint_shapes_of_a_rectangle():
    row = footprint_shapes([box(0, 0, 20, 10)]).iloc[0]
    assert (row["n_footprint_parts"], row["n_footprint_holes"], row["n_footprint_vertices"]) == (1, 0, 4)
    assert math.isclose(row["footprint_width_m"], 10) and math.isclose(row["footprint_length_m"], 20)
    assert math.isclose(row["min_corner_angle_deg"], 90.0) and math.isclose(row["footprint_area_m2"], 200.0)


def test_a_degenerate_rectangle_from_geos_is_measured_on_the_hull(monkeypatch):
    # GEOS 3.13 returned a zero-width rectangle for a snapped outline; a rectangle smaller than the hull is not used.
    poly = affinity.rotate(Polygon([(0, 0), (20, 0), (20, 4), (12, 4), (12, 10), (0, 10)]), 30, origin=(0, 0))
    monkeypatch.setattr(footprints.shapely, "oriented_envelope", lambda geom: box(0, 0, 7, 1e-15))
    row = footprint_shapes([poly]).iloc[0]
    assert math.isclose(row["footprint_width_m"], 10) and math.isclose(row["footprint_length_m"], 20)


def test_thin_holes_are_counted():
    courtyard = Polygon(box(0, 0, 20, 20).exterior, [box(5, 5, 15, 15).exterior, box(2, 2, 2.6, 4).exterior])
    assert thin_hole_counts([courtyard, box(0, 0, 5, 5)], 1.0).tolist() == [1, 0]


def test_fingerprint_ignores_noise_below_the_grid_and_the_start_vertex():
    square = box(0, 0, 10, 10)
    assert footprint_fingerprint(square) == footprint_fingerprint(affinity.translate(square, 0.001, 0))
    assert footprint_fingerprint(square) == footprint_fingerprint(Polygon([(10, 10), (0, 10), (0, 0), (10, 0)]))
    assert footprint_fingerprint(square) != footprint_fingerprint(affinity.translate(square, 0.05, 0))


def test_iou_of_outlines_that_differ_by_rounding_noise_is_one():
    a = Polygon([(-2.63, 2.57), (3.56, 0.77), (2.63, -2.57), (-3.56, -0.77)])
    b = Polygon([(-2.63, 2.57), (3.5599999999999996, 0.7699999999999999), (2.63, -2.57),
                 (-3.5599999999999996, -0.7699999999999999)])
    assert math.isclose(fidelity([a], [b])["fidelity_iou"].iloc[0], 1.0)


# -------------------------------------------------------------------------------------------- local frame
def test_local_frame_projects_and_centres_the_bounding_box():
    square = box(11.575, 48.137, 11.5753, 48.1372)  # a small square in WGS84
    gdf = gpd.GeoDataFrame({"metric_crs": ["EPSG:32632"], "geometry_bem4ai": gpd.GeoSeries([square], crs="EPSG:4326")},
                           geometry=[square], crs="EPSG:4326")
    out = to_local_frames(gdf, {"geometry": "geom_local_raw", "geometry_bem4ai": "geom_local_bem4ai"})
    minx, miny, maxx, maxy = out.at[0, "geom_local_raw"].bounds
    assert abs(minx + maxx) < 1e-6 and abs(miny + maxy) < 1e-6 and 15 < maxx - minx < 30
    assert out.at[0, "geom_local_bem4ai"].equals_exact(out.at[0, "geom_local_raw"], 1e-9)
    assert out.at[0, "bbox_cx_m"] > 600_000 and out.crs is None


# ----------------------------------------------------------------------------------------------- vertices
def test_duplicate_and_collinear_vertices_are_dropped_and_corners_kept():
    noisy = Polygon([(0, 0), (5, 0.03), (10, 0), (10, 0.004), (10, 10), (5, 10.04), (0, 10)])
    cleaned, removed = remove_redundant_vertices(noisy)
    assert vertex_count(cleaned) == 4 and removed == 3
    stepped = Polygon([(0, 0), (12, 0), (12, 0.3), (20, 0.3), (20, 10), (0, 10)])
    assert remove_redundant_vertices(stepped)[1] == 0


def test_vertex_cleanup_moves_the_outline_at_most_its_tolerance():
    arc = [(10 * math.cos(t), 10 * math.sin(t)) for t in np.linspace(0, math.pi, 40)]
    poly = Polygon(arc)
    cleaned, removed = remove_redundant_vertices(poly, VertexCleanup(0.01, 0.05))
    assert removed > 0 and cleaned.hausdorff_distance(poly) <= 0.05 + 1e-9


def test_a_spike_is_not_a_collinear_vertex():
    spiked = Polygon([(0, 0), (10, 0), (10, 10), (5, 10), (5.02, 13), (5.04, 10), (0, 10)])
    assert remove_redundant_vertices(spiked)[0].equals(spiked)


# ---------------------------------------------------------------------------------------------- thin parts
def test_a_stub_is_cut_and_a_curve_keeps_its_vertices():
    arc = [(20 + 6 * math.cos(t), 5 + 6 * math.sin(t)) for t in np.linspace(-math.pi / 2, math.pi / 2, 15)]
    curved = Polygon([(0, -1), *arc, (0, 11)])
    stubbed = curved.union(box(-1.5, 4, 0.0, 5.2))                    # a 1.2 m wide stub on the west wall
    out, report = erase_thin_parts([curved, stubbed], 2.0)
    assert report["thin_part_share"].iloc[0] == 0.0 and out[0].equals(curved)  # a curve is not a thin part
    assert abs(out[1].area - curved.area) < 1e-4
    assert not report["thin_part_topology_changed"].any()


def test_a_split_or_an_opened_courtyard_is_a_topology_change():
    dumbbell = box(0, 0, 10, 10).union(box(10, 4.2, 14, 5.8)).union(box(14, 0, 24, 10))
    thin_wall = Polygon(box(0, 0, 20, 20).exterior, [box(1.5, 5, 15, 15).exterior])  # 1.5 m between hole and outside
    _, report = erase_thin_parts([dumbbell, thin_wall], 2.0)
    assert report["thin_part_topology_changed"].tolist() == [True, True]


def test_a_wing_takes_a_large_share_off():
    winged = box(0, 0, 10, 10).union(box(10, 4, 30, 5.8))           # a 20 m wing 1.8 m wide: 36 of 136 m2
    _, report = erase_thin_parts([winged], 2.0)
    assert math.isclose(report["thin_part_share"].iloc[0], 36 / 136, rel_tol=1e-3)


# ------------------------------------------------------------------------------------------------- axes
def test_facade_steps_up_to_the_offset_tolerance_close():
    small = Polygon([(0, 0), (12, 0), (12, 0.4), (20, 0.4), (20, 10), (0, 10)])
    large = Polygon([(0, 0), (12, 0), (12, 0.8), (20, 0.8), (20, 10), (0, 10)])
    wide = AxesSettings(offset_tolerance_m=0.5)
    out, status, _ = snap_to_axes(small, wide)
    assert status == "snapped" and vertex_count(out) == 4 and residual_steps(out) == 0
    assert out.hausdorff_distance(small) <= 0.4 + 1e-9                  # the longer run stays, the shorter moves
    assert snap_to_axes(large, wide)[1] == "unchanged"
    assert snap_to_axes(small)[1] == "unchanged"                        # the default 0.2 m keeps a 0.4 m step


def test_a_slightly_bent_wall_becomes_one_wall_without_a_step():
    bent = Polygon([(0, 0), (15, 0), (30, 0.52), (30, 10), (0, 10)])
    out, status, _ = snap_to_axes(bent)
    assert status == "snapped" and vertex_count(out) == 4 and residual_steps(out) == 0


def test_north_south_walls_either_side_of_the_wrap_are_one_direction():
    kinked = Polygon([(0, 0), (10, 0.087), (20, 0), (20, 10), (10, 10.087), (0, 10)])
    for rotation in (0.0, 90.0, 45.0, 0.3, 89.7):
        poly = affinity.rotate(kinked, rotation, origin=(0, 0))
        axes = find_axes(poly)
        assert len(axes.directions) == 2, rotation
        assert vertex_count(snap_to_axes(poly, axes=axes)[0]) == 4, rotation


def test_walls_facing_each_other_are_never_one_line():
    slot = Polygon([(0, 0), (20, 0), (20, 10), (10.2, 10), (10.2, 3), (9.8, 3), (9.8, 10), (0, 10)])  # 0.4 m slot
    axes = find_axes(slot, AxesSettings(offset_tolerance_m=0.5))
    sides = [line for line in axes.lines if abs(math.cos(axes.directions[line["direction"]])) < 1e-6]
    assert len(sides) == 4                                              # x = 0, 9.8, 10.2, 20 stay apart


def test_an_edge_shorter_than_the_snap_is_dropped_rather_than_flipped():
    # The 45-degree edge joins the line of the long one 0.3 m out, which carries its end 0.42 m along
    # the 0.2 m edge after it: that edge would point backwards and leave a flap, so it goes.
    poly = Polygon([(0, 0), (4, 0), (4.7, 0.7), (4.9, 0.7), (4.9, 1.5), (5.92, 1.5), (8.92, 4.5), (8.92, 8), (0, 8)])
    out, status, _ = snap_to_axes(poly, AxesSettings(offset_tolerance_m=0.5))
    assert status == "snapped" and vertex_count(out) == 8
    assert Point(4.9, 0.48).distance(out.exterior) < 1e-6


def test_a_normalization_that_moves_too_far_is_refused():
    stepped = Polygon([(0, 0), (12, 0), (12, 0.4), (20, 0.4), (20, 10), (0, 10)])
    out, status, _ = snap_to_axes(stepped, AxesSettings(offset_tolerance_m=0.5, max_move_m=0.1))
    assert status == "kept_move" and out.equals(stepped)


# ------------------------------------------------------------------------------------------- review log
def test_review_decisions_apply_while_the_fingerprint_holds():
    table = pd.DataFrame({"source_sample_id": ["a", "b"], "sample_id": ["s1", "s2"],
                          "footprint_fingerprint": ["f1", "f2"]})
    with tempfile.TemporaryDirectory() as folder:
        log = Path(folder) / "review_log.csv"
        assert add_marks("footprints", footprint_identity(table), ["a"], path=log, note="garage") == ["a"]
        assert keep_marks("footprints", footprint_identity(table), ["b"], path=log) == ["b"]
        assert read_log(log)[["source_sample_id", "decision"]].values.tolist() == [["a", "remove"], ["b", "keep"]]
        table.loc[1, "footprint_fingerprint"] = "f2-new"
        states = mark_states("footprints", footprint_identity(table), log).set_index("source_sample_id")["state"]
        assert states.to_dict() == {"a": "applies", "b": "changed"}


# ----------------------------------------------------------------------------- earlier alignment and normalization
ALIGN = EdgeAlignmentConfig(family_angle_tol_deg=3.0, line_angle_tol_deg=2.0, line_offset_tol_m=0.2,
                            collinear_tol_deg=1.0, min_seg_len_m=0.05, max_vertex_snap_shift_m=0.4)


def test_alignment_averages_directions_either_side_of_north_south():
    edges = [{"alpha": math.radians(a), "length": 10.0, "n": np.array([math.cos(math.radians(a)), math.sin(math.radians(a))])}
             for a in (0.5, 179.5)]
    mean = math.degrees(_weighted_mean_alpha(edges, ALIGN)) % 180.0
    assert min(mean, 180.0 - mean) < 1e-9


def test_alignment_straightens_a_kinked_wall():
    kinked = Polygon([(0, 0), (10, 0.087), (20, 0), (20, 10), (10, 10.087), (0, 10)])
    poly = affinity.rotate(kinked, 90.0, origin=(0, 0))
    assert len(_cluster_angle_families(_raw_edges_from_polygon(poly, ALIGN), ALIGN)) == 2
    assert vertex_count(align_polygon_edges_via_line_clustering(poly, ALIGN)) == 4


def test_normalization_distance_limit_keeps_the_original():
    bumped = Polygon([(0, 0), (9, 0), (10, -0.15), (11, 0), (20, 0), (20, 10), (0, 10)])
    assert vertex_count(normalize_footprint_geometry(bumped, FootprintNormalizationConfig())) == 4
    assert normalize_footprint_geometry(bumped, FootprintNormalizationConfig(max_hausdorff_m=0.1)).equals(bumped)


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
            print("ok", name)
