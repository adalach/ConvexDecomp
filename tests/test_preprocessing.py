"""Checks for the OSM preprocessing: the BEM4AI-style checks, cleanup, local-frame steps, review log and alignment.

Runs under pytest, or directly with ``python tests/test_preprocessing.py``.
"""

from __future__ import annotations

import math
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import affinity
from shapely.geometry import Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from convexdecomp.osm.axis_alignment import (EdgeAlignmentConfig, _cluster_angle_families, _raw_edges_from_polygon,
                                             _weighted_mean_alpha, align_polygon_edges_via_line_clustering)
from convexdecomp.osm.footprints import (FootprintCleanup, clean_footprints, footprint_fingerprint, footprint_shapes,
                                         thin_part_shares)
from convexdecomp.osm.local_frames import (FacadeOffsetConfig, clean_polygonal_in_one_piece, close_facade_offsets,
                                           local_frame)
from convexdecomp.osm.normalize import FootprintNormalizationConfig, normalize_footprint_geometry
from convexdecomp.osm.preprocess import GeometrySettings, check_footprints, fidelity, regularize_geometry
from convexdecomp.osm.review_log import add_marks, footprint_identity, keep_marks, mark_states, read_log
from convexdecomp.osm.rules import FOOTPRINT_STAGE, PREPROCESSED_STAGE, load_rules, parse_checks

ALIGN = EdgeAlignmentConfig(family_angle_tol_deg=3.0, line_angle_tol_deg=2.0, line_offset_tol_m=0.2,
                            collinear_tol_deg=1.0, min_seg_len_m=0.05, max_vertex_snap_shift_m=0.4)


# ------------------------------------------------------------------------------------------------- rules
def test_repository_checks_load_and_flag_by_kind():
    rules = load_rules()
    frame = pd.DataFrame({
        "n_footprint_parts": [1, 2], "footprint_width_m": [10.0, 1.0], "footprint_length_m": [20.0, 30.0],
        "footprint_area_m2": [200.0, 15.0], "n_footprint_holes": [0, 5], "n_footprint_vertices": [4, 8],
        "min_corner_angle_deg": [90.0, 20.0], "thin_part_share": [0.01, 0.06],
    })
    at_footprint = rules.evaluate(frame, FOOTPRINT_STAGE)
    assert at_footprint.loc[0].tolist() == ["", "", ""]
    assert set(at_footprint.at[1, "hard_failures"].split(";")) == {
        "footprint_not_one_polygon", "footprint_sliver", "footprint_below_min_area", "sharp_angle",
        "footprint_thin_parts"}
    assert at_footprint.at[1, "review_flags"] == "footprint_too_many_holes"
    # The footprint checks judge the preprocessed footprint as well.
    assert rules.evaluate(frame, PREPROCESSED_STAGE).equals(at_footprint)


def test_a_check_must_read_its_columns():
    entry = {"name": "x", "kind": "remove", "first_stage": 3, "columns": ["a"], "expression": "b > 1", "meaning": ""}
    try:
        parse_checks([entry], {})
    except ValueError as error:
        assert "reads" in str(error)
    else:
        raise AssertionError("a check reading another column than its columns was accepted")


# ---------------------------------------------------------------------------------------------- footprints
CLEANUP = FootprintCleanup(thin_part_max_width_m=1.0, collinear_tolerance_m=0.05, min_piece_area_m2=20.0)


def test_cleanup_cuts_thin_legs_fills_thin_holes_and_drops_straight_vertices():
    leg = Polygon([(0, 0), (10, 0), (10, 10), (5.3, 10), (5.3, 14), (4.7, 14), (4.7, 10), (0, 10), (0, 5.02)])
    holed = Polygon(box(0, 0, 10, 10).exterior, [box(4, 2, 4.5, 8).exterior])
    out, report = clean_footprints([leg, holed], CLEANUP)
    assert out[0].equals_exact(box(0, 0, 10, 10), 1e-6) or abs(out[0].area - 100.0) < 1e-6
    assert len(out[0].exterior.coords) - 1 == 4                 # the leg is gone and (0, 5.02) dropped
    assert math.isclose(report.at[0, "cleanup_cut_m2"], 0.6 * 4, rel_tol=1e-6)
    assert report.at[0, "n_osm_vertices"] == 9
    assert len(out[1].interiors) == 0 and math.isclose(report.at[1, "cleanup_filled_m2"], 3.0)


def test_footprint_shapes_of_a_rectangle():
    row = footprint_shapes([box(0, 0, 20, 10)]).iloc[0]
    assert (row["n_footprint_parts"], row["n_footprint_holes"], row["n_footprint_vertices"]) == (1, 0, 4)
    assert math.isclose(row["footprint_width_m"], 10) and math.isclose(row["footprint_length_m"], 20)
    assert math.isclose(row["min_corner_angle_deg"], 90.0) and math.isclose(row["footprint_area_m2"], 200.0)
    assert math.isclose(row["footprint_circularity"], 4 * math.pi * 200 / 60 ** 2)


def test_thin_part_share_counts_parts_narrower_than_the_zone_width():
    with_strip = box(0, 0, 10, 10).union(box(10, 0, 20, 1.5))   # a 1.5 m wide strip of 15 m2
    shares = thin_part_shares([box(0, 0, 10, 10), with_strip, box(0, 0, 1, 10)], 2.0)
    assert shares[0] == 0.0
    assert math.isclose(shares[1], 15.0 / 115.0, rel_tol=1e-6)
    assert shares[2] == 1.0


def test_fingerprint_ignores_noise_below_the_grid_and_the_start_vertex():
    square = box(0, 0, 10, 10)
    assert footprint_fingerprint(square) == footprint_fingerprint(affinity.translate(square, 0.001, 0))
    assert footprint_fingerprint(square) == footprint_fingerprint(Polygon([(10, 10), (0, 10), (0, 0), (10, 0)]))
    assert footprint_fingerprint(square) != footprint_fingerprint(affinity.translate(square, 0.05, 0))


# --------------------------------------------------------------------------------------------- local frames
def test_local_frame_centres_the_bounding_box():
    moved, cx, cy = local_frame([box(100, 200, 120, 210)])
    assert moved[0].bounds == (-10.0, -5.0, 10.0, 5.0) and (cx[0], cy[0]) == (110.0, 205.0)


def test_thin_part_cleanup_keeps_a_footprint_in_one_piece():
    dumbbell = box(0, 0, 10, 10).union(box(10, 4.2, 14, 5.8)).union(box(14, 0, 24, 10))
    cleaned, mode = clean_polygonal_in_one_piece(dumbbell, 1.0, 1.0, 2.0)
    assert mode in {"cleaned_reduced", "uncleaned"} and cleaned.geom_type == "Polygon"
    legged = box(0, 0, 10, 10).union(box(10, 0, 13, 1.2))
    cleaned, mode = clean_polygonal_in_one_piece(legged, 1.0, 1.0, 2.0)
    assert mode == "cleaned" and math.isclose(cleaned.area, 100.0, rel_tol=1e-6)


def test_facade_offsets_of_a_few_decimetres_are_closed():
    jogged = Polygon([(0, 0), (12, 0), (12, 0.3), (20, 0.3), (20, 10), (0, 10)])
    closed, info = close_facade_offsets(jogged, FacadeOffsetConfig())
    assert info["status"] == "closed" and len(closed.exterior.coords) - 1 == 4
    far, info = close_facade_offsets(Polygon([(0, 0), (12, 0), (12, 2), (20, 2), (20, 10), (0, 10)]))
    assert info["status"] == "unchanged" and far.equals(Polygon([(0, 0), (12, 0), (12, 2), (20, 2), (20, 10), (0, 10)]))


# ----------------------------------------------------------------------------------------------- review log
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


# ----------------------------------------------------------------------------------------------- alignment
def _edge(alpha_deg):
    a = math.radians(alpha_deg % 180.0)
    return {"alpha": a, "length": 10.0, "n": np.array([math.cos(a), math.sin(a)])}


def test_directions_either_side_of_north_south_average_to_north_south():
    mean = math.degrees(_weighted_mean_alpha([_edge(0.5), _edge(179.5)], ALIGN)) % 180.0
    assert min(mean, 180.0 - mean) < 1e-9


def test_a_kinked_wall_is_straightened_in_every_orientation():
    kinked = Polygon([(0, 0), (10, 0.087), (20, 0), (20, 10), (10, 10.087), (0, 10)])
    for rotation in (0.0, 90.0, 45.0, 0.3, 89.7):
        poly = affinity.rotate(kinked, rotation, origin=(0, 0))
        assert len(_cluster_angle_families(_raw_edges_from_polygon(poly, ALIGN), ALIGN)) == 2, rotation
        aligned = align_polygon_edges_via_line_clustering(poly, ALIGN)
        assert len(aligned.exterior.coords) - 1 == 4, rotation


# --------------------------------------------------------------------------------------------- normalization
def test_normalization_distance_limit_keeps_the_original():
    # A 0.15 m bump along a 20 m wall: normalization flattens it, moving the outline by 0.15 m.
    bumped = Polygon([(0, 0), (9, 0), (10, -0.15), (11, 0), (20, 0), (20, 10), (0, 10)])
    flattened = normalize_footprint_geometry(bumped, FootprintNormalizationConfig())
    assert len(flattened.exterior.coords) - 1 == 4
    kept = normalize_footprint_geometry(bumped, FootprintNormalizationConfig(max_hausdorff_m=0.1))
    assert kept.equals(bumped)


# -------------------------------------------------------------------------------------------- preprocessing
def test_iou_of_outlines_that_differ_by_rounding_noise_is_one():
    a = Polygon([(-2.63, 2.57), (3.56, 0.77), (2.63, -2.57), (-3.56, -0.77)])
    b = Polygon([(-2.63, 2.57), (3.5599999999999996, 0.7699999999999999), (2.63, -2.57),
                 (-3.5599999999999996, -0.7699999999999999)])
    assert math.isclose(fidelity([a], [b])["fidelity_iou"].iloc[0], 1.0)


def test_a_wing_fails_the_thin_part_check_and_a_stub_is_erased():
    import geopandas as gpd
    winged = box(0, 0, 10, 10).union(box(10, 4, 30, 5.8))       # a 20 m wing 1.8 m wide: 36 of 136 m2
    stub = box(0, 0, 10, 10).union(box(10, 4, 11.0, 5.8))       # a 1 m stub: 1.8 of 101.8 m2
    gdf = gpd.GeoDataFrame({"sample_id": ["w", "s"], "source_sample_id": ["w", "s"],
                            "footprint_fingerprint": ["fw", "fs"], "geom_local_clean": [winged, stub]},
                           geometry=[winged, stub], crs=None)
    checks = check_footprints(gdf, "geom_local_clean", load_rules(), FOOTPRINT_STAGE).set_index("sample_id")
    assert checks.at["w", "hard_failures"] == "footprint_thin_parts" and checks.at["s", "hard_failures"] == ""
    settings = GeometrySettings(FootprintNormalizationConfig(), ALIGN, 1.0, FacadeOffsetConfig())
    out = regularize_geometry(gdf.loc[[1]], "L2", settings).set_index("sample_id")
    assert out.at["s", "cleanup_mode"] == "cleaned" and math.isclose(out.at["s", "geom_local"].area, 100.0)


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
            print("ok", name)
