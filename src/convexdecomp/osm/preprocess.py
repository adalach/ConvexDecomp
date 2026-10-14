"""The steps of ``OSM_1_Preprocessing.ipynb``: from an OSM footprint set to the decomposition working set.

The steps and their tables follow BEM4AI: the mapping cleanup and footprint checks of its Stage 3,
the review log, and the main-footprint geometry of its Stage 4 (``helpers/local_frames.py``). Each
footprint carries one geometry column per step (`convexdecomp.osm.local_frames`), so every step can
be compared with the outline it started from.

How far the geometry is changed is set by the preprocessing level (`LEVELS`):

| Level | Steps |
| --- | --- |
| ``L0`` | metric scale and local coordinates only: the outline as it arrived, repaired |
| ``L1`` | + the mapping cleanup (parts and holes thinner than ``footprint_cleanup.thin_part_max_width_m`` and vertices within ``footprint_cleanup.collinear_tolerance_m``) and the normalization (duplicate vertices, short edges, near-collinear runs) |
| ``L2`` | + edge alignment, erasing parts thinner than twice the shrink distance, closing facade offsets |

The checks of ``config/checks.json`` run at every level.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon

from convexdecomp.core.convexity import convexity_mask
from convexdecomp.osm.axis_alignment import EdgeAlignmentConfig, align_buildings_gdf
from convexdecomp.osm.footprints import (CLEANUP_COLUMNS, FootprintCleanup, clean_footprints, footprint_fingerprint,
                                         footprint_shapes, make_geometry_valid, thin_part_shares)
from convexdecomp.osm.local_frames import (FacadeOffsetConfig, clean_polygonal_in_one_piece, close_facade_offsets,
                                           sync_geometry)
from convexdecomp.osm.normalize import FootprintNormalizationConfig, count_exterior_vertices, normalize_buildings_gdf
from convexdecomp.osm.review_log import footprint_identity, mark_states
from convexdecomp.osm.rules import Rules

__all__ = [
    "IDENTITY_COLUMNS",
    "LEVELS",
    "WORKING_SET_COLUMNS",
    "GeometrySettings",
    "check_footprints",
    "clean_mapping_artefacts",
    "fidelity",
    "load_footprints",
    "reflex_vertex_counts",
    "regularize_geometry",
    "review_removals",
    "summarize_checks",
    "working_set",
]

LEVELS = ("L0", "L1", "L2")
#: The columns that name a footprint in every table, as in BEM4AI's checks tables.
IDENTITY_COLUMNS = ("sample_id", "source_sample_id", "footprint_fingerprint")
_OVERLAY_GRID_M = 1e-6
#: The columns of the working table ``OSM_2_Decomposition.ipynb`` reads.
WORKING_SET_COLUMNS = ("sample_id", "geometry", "area_m2", "n_vertices", "n_holes")


def _level_at_least(level: str, minimum: str) -> bool:
    if level not in LEVELS:
        raise ValueError(f"Unknown preprocessing level {level!r}; expected one of {LEVELS}.")
    return LEVELS.index(level) >= LEVELS.index(minimum)


def _single_part(geom):
    """A MultiPolygon of one part as that Polygon; anything else as it is."""
    if isinstance(geom, MultiPolygon):
        parts = [part for part in geom.geoms if not part.is_empty]
        if len(parts) == 1:
            return parts[0]
    return geom


# --------------------------------------------------------------------------------------------------- loading
def load_footprints(path: str | Path, layer: str = "footprints") -> gpd.GeoDataFrame:
    """A footprint set in local metres, with the identity and frame columns of BEM4AI's tables.

    The file needs ``sample_id`` and the footprints in local metres; any other column is kept. Where
    they are missing, ``source_sample_id`` is the ``sample_id`` and ``footprint_fingerprint`` the
    fingerprint of the repaired outline (`footprint_fingerprint`, in metres). Each outline is
    repaired (`make_geometry_valid`), a MultiPolygon of one part becomes a Polygon, and the result is
    ``geom_local_raw``. Local frames have no CRS, so any CRS label of the file is dropped.
    """
    gdf = gpd.read_file(path, layer=layer)
    raw = [_single_part(make_geometry_valid(_single_part(geom))) for geom in gdf.geometry]
    frame = pd.DataFrame(gdf.drop(columns=gdf.geometry.name))
    frame["sample_id"] = frame["sample_id"].astype(str)
    if "source_sample_id" not in frame.columns:
        frame["source_sample_id"] = frame["sample_id"]
    if "footprint_fingerprint" not in frame.columns:
        frame["footprint_fingerprint"] = [footprint_fingerprint(geom) for geom in raw]
    frame["geom_local_raw"] = pd.Series(raw, index=frame.index, dtype=object)
    leading = [*IDENTITY_COLUMNS]
    frame = frame[leading + [column for column in frame.columns if column not in leading]]
    return sync_geometry(gpd.GeoDataFrame(frame, geometry=frame["geom_local_raw"].to_numpy(), crs=None),
                         "geom_local_raw")


# ------------------------------------------------------------------------------------------------- footprints
def clean_mapping_artefacts(gdf: gpd.GeoDataFrame, rules: Rules, level: str) -> gpd.GeoDataFrame:
    """The mapping cleanup of BEM4AI Stage 3 (`clean_footprints`) from L1 on: ``geom_local_clean`` and `CLEANUP_COLUMNS`.

    At L0 the outline stays as it arrived and the report records no change.
    """
    out = gdf.copy()
    raw = out["geom_local_raw"].to_numpy()
    if _level_at_least(level, "L1"):
        cleaned, report = clean_footprints(raw, FootprintCleanup.from_thresholds(rules.thresholds))
    else:
        cleaned = raw.copy()
        report = pd.DataFrame({"n_osm_vertices": [count_exterior_vertices(geom) for geom in raw],
                               "cleanup_filled_m2": 0.0, "cleanup_cut_m2": 0.0})
    out["geom_local_clean"] = pd.Series(list(cleaned), index=out.index, dtype=object)
    for column in CLEANUP_COLUMNS:
        out[column] = report[column].to_numpy()
    return sync_geometry(out, "geom_local_clean")


def check_footprints(gdf: gpd.GeoDataFrame, geometry_col: str, rules: Rules, stage: int) -> pd.DataFrame:
    """The checks table of one stage, as BEM4AI writes ``checks_stage<N>.csv``.

    One row per footprint: the identity columns (the fingerprint as ``fingerprint``), the
    measurements (`footprint_shapes` and ``thin_part_share`` below ``zones.min_width_m``), the
    failed checks of each kind (``hard_failures``, ``review_flags``, ``rerun_flags``) and
    ``written_at``.
    """
    geometries = gdf[geometry_col].to_numpy()
    measured = footprint_shapes(geometries)
    measured["thin_part_share"] = thin_part_shares(geometries, float(rules.thresholds["zones"]["min_width_m"]))
    measured.index = gdf.index
    table = pd.concat([
        gdf[["sample_id", "source_sample_id"]].astype(str),
        gdf["footprint_fingerprint"].astype(str).rename("fingerprint"),
        measured,
        rules.evaluate(measured, stage),
    ], axis=1)
    table["written_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return table


def summarize_checks(checks: pd.DataFrame, rules: Rules, stage: int) -> pd.DataFrame:
    """Per check that runs at `stage` on the measurements of `checks`: its kind, rule and how many footprints fail it."""
    flag_column = {"remove": "hard_failures", "review": "review_flags", "rerun": "rerun_flags"}
    rows = []
    for check in rules.available(checks.columns, stage):
        failing = checks[flag_column[check.kind]].str.split(";").map(lambda names, name=check.name: name in names)
        rows.append({"check": check.name, "kind": check.kind, "rule": check.rule(rules.thresholds),
                     "failing": int(failing.sum())})
    return pd.DataFrame(rows)


def review_removals(checks: pd.DataFrame, gdf: gpd.GeoDataFrame, log_path: str | Path,
                    remove_check_selections: bool) -> pd.DataFrame:
    """What leaves at a checks stage, and why, on the index of `checks`.

    As in BEM4AI: a footprint failing a ``remove`` check leaves (``check_hard``), one removed in the
    review log leaves (``manual``), and one a ``review`` check selected leaves only with
    `remove_check_selections` (``check_review``). A ``keep`` in the review log overrides the checks.
    Decisions apply while the footprint has the fingerprint they were made with.

    Returns ``removed_by`` (``check_hard``, ``manual``, ``check_review`` or "") and ``removal_reason``
    (the checks' names, or the log's note).
    """
    states = mark_states("footprints", footprint_identity(gdf), log_path)
    applying = states.loc[states["state"].eq("applies")].set_index("source_sample_id")
    decision = gdf["source_sample_id"].astype(str).map(applying["decision"]).fillna("")
    note = gdf["source_sample_id"].astype(str).map(applying["note"]).fillna("")
    kept = decision.eq("keep")

    removed_by = pd.Series("", index=checks.index, dtype=object)
    reason = pd.Series("", index=checks.index, dtype=object)
    hard = checks["hard_failures"].ne("") & ~kept
    removed_by[hard], reason[hard] = "check_hard", checks.loc[hard, "hard_failures"]
    manual = decision.eq("remove") & removed_by.eq("")
    removed_by[manual], reason[manual] = "manual", note[manual].where(note[manual].ne(""), "manual")
    if remove_check_selections:
        selected = checks["review_flags"].ne("") & ~kept & removed_by.eq("")
        removed_by[selected], reason[selected] = "check_review", checks.loc[selected, "review_flags"]
    return pd.DataFrame({"removed_by": removed_by, "removal_reason": reason})


# --------------------------------------------------------------------------------------------------- geometry
@dataclass(frozen=True)
class GeometrySettings:
    """The settings of the geometry steps (BEM4AI Stage 4, main footprints)."""

    normalization: FootprintNormalizationConfig
    alignment: EdgeAlignmentConfig
    #: Parts narrower than twice this are erased (shrink and expand by it, mitre joins).
    thin_part_shrink_m: float
    facade_offsets: FacadeOffsetConfig
    #: Pieces below this are dropped by the thin-part cleanup.
    min_piece_area_m2: float = 2.0
    #: A thin-part cleanup that takes more than this share of the area off is refused
    #: (``cleanup_mode`` ``kept_area_change``); None: no limit.
    thin_part_max_area_change_ratio: float | None = 0.05


def regularize_geometry(gdf: gpd.GeoDataFrame, level: str, settings: GeometrySettings) -> gpd.GeoDataFrame:
    """The geometry steps of the level, from ``geom_local_clean`` to ``geom_local_preray`` and ``geom_local``.

    L1 normalizes (`normalize_buildings_gdf`); L2 also aligns the edges (`align_buildings_gdf`),
    erases the parts thinner than twice ``settings.thin_part_shrink_m`` while keeping the footprint
    one polygon (`clean_polygonal_in_one_piece`, ``cleanup_mode``; ``kept_area_change`` where it would take
    more than ``settings.thin_part_max_area_change_ratio`` of the area off) and closes the facade offsets
    (`close_facade_offsets`, ``facade_status`` and ``facade_area_change_m2``). The normalization and
    alignment diagnostics (``vertex_delta_norm``, ``alignment_changed``, ...) are kept.
    """
    work = sync_geometry(gdf.copy(), "geom_local_clean")
    if _level_at_least(level, "L1"):
        work = normalize_buildings_gdf(work, geometry_col="geometry", cfg=settings.normalization)
    if _level_at_least(level, "L2"):
        work["geometry_before_align"] = work["geometry"]
        work = align_buildings_gdf(work, geometry_col="geometry", cfg=settings.alignment)
    work["geom_local_preray"] = pd.Series(list(work["geometry"]), index=work.index, dtype=object)

    if _level_at_least(level, "L2"):
        thin = [_thin_parts_erased(geom, settings) for geom in work["geom_local_preray"]]
        facade = [close_facade_offsets(geom, settings.facade_offsets) for geom, _ in thin]
        work["cleanup_mode"] = [mode for _, mode in thin]
        work["facade_status"] = [info["status"] for _, info in facade]
        work["facade_area_change_m2"] = [info["area_change_m2"] for _, info in facade]
        final = [geom for geom, _ in facade]
    else:
        work["cleanup_mode"] = "off"
        work["facade_status"] = "off"
        work["facade_area_change_m2"] = 0.0
        final = list(work["geom_local_preray"])
    work["geom_local"] = pd.Series(final, index=work.index, dtype=object)
    return sync_geometry(work, "geom_local")


def _thin_parts_erased(geom, settings: GeometrySettings):
    """`clean_polygonal_in_one_piece` within the area guard of `settings`: the geometry and its ``cleanup_mode``."""
    cleaned, mode = clean_polygonal_in_one_piece(geom, settings.thin_part_shrink_m, settings.thin_part_shrink_m,
                                                 settings.min_piece_area_m2)
    limit = settings.thin_part_max_area_change_ratio
    if limit is not None and geom.area > 0 and abs(cleaned.area - geom.area) > limit * geom.area:
        return geom, "kept_area_change"
    return cleaned, mode


def reflex_vertex_counts(geometries, tol_deg: float = 1.0) -> np.ndarray:
    """The reflex corners of each outline: turns against the ring's orientation by more than `tol_deg`.

    The exterior ring only; a footprint that is no single polygon counts 0.
    """
    counts = np.zeros(len(geometries), dtype=int)
    for k, geom in enumerate(geometries):
        if geom is None or geom.geom_type != "Polygon" or geom.is_empty:
            continue
        pts = np.asarray(shapely.geometry.polygon.orient(geom, 1.0).exterior.coords[:-1])
        v1 = pts - np.roll(pts, 1, axis=0)
        v2 = np.roll(pts, -1, axis=0) - pts
        cross = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
        turn = np.degrees(np.abs(np.arctan2(cross, (v1 * v2).sum(axis=1))))
        counts[k] = int(((cross < 0) & (turn > tol_deg)).sum())
    return counts


def fidelity(raw, final) -> pd.DataFrame:
    """How far each final footprint is from its OSM outline.

    ``fidelity_hausdorff_m`` (the largest distance between the two outlines), ``fidelity_iou``
    (intersection over union) and ``fidelity_area_change_pct`` (signed, relative to the outline).
    """
    raw = np.asarray(list(raw), dtype=object)
    final = np.asarray(list(final), dtype=object)
    raw_valid, final_valid = shapely.make_valid(raw), shapely.make_valid(final)
    # Snap rounding to a micrometre: GEOS can return an empty intersection for two outlines that differ by 1e-16 m.
    union = shapely.area(shapely.union(raw_valid, final_valid, grid_size=_OVERLAY_GRID_M))
    with np.errstate(divide="ignore", invalid="ignore"):
        return pd.DataFrame({
            "fidelity_hausdorff_m": shapely.hausdorff_distance(raw, final),
            "fidelity_iou": shapely.area(shapely.intersection(raw_valid, final_valid, grid_size=_OVERLAY_GRID_M)) / union,
            "fidelity_area_change_pct": 100.0 * (shapely.area(final_valid) - shapely.area(raw_valid))
                                        / shapely.area(raw_valid),
        })


# ------------------------------------------------------------------------------------------------ working set
def working_set(gdf: gpd.GeoDataFrame, rule: str = "all") -> tuple[gpd.GeoDataFrame, pd.Series]:
    """The decomposition working set in the format ``OSM_2_Decomposition.ipynb`` reads, and why the others are out.

    `rule` is ``"all"`` (every footprint that reached this point) or ``"paper"`` (the EG-ICE 2026
    working set: footprints still non-convex, with 6 to 32 exterior vertices). Returns the working
    table with `WORKING_SET_COLUMNS` (``geometry`` is ``geom_local``) and, on the index of `gdf`,
    the reason each footprint left ("" for those in the set).
    """
    geometries = gdf["geom_local"].to_numpy()
    n_vertices = np.array([count_exterior_vertices(geom) for geom in geometries])
    reason = pd.Series("", index=gdf.index, dtype=object)
    if rule == "paper":
        convex = convexity_mask(geometries)
        reason[convex] = "convex"
        outside = ~convex & ((n_vertices < 6) | (n_vertices > 32))
        reason[outside] = "vertices outside 6-32"
    elif rule != "all":
        raise ValueError(f"Unknown working-set rule {rule!r}; expected 'all' or 'paper'.")
    keep = reason.eq("").to_numpy()
    table = gpd.GeoDataFrame({
        "sample_id": gdf["sample_id"].astype(str).to_numpy()[keep],
        "geometry": geometries[keep],
        "area_m2": shapely.area(geometries[keep]).astype(float),
        "n_vertices": n_vertices[keep].astype(int),
        "n_holes": shapely.get_num_interior_rings(geometries[keep]).astype(int),
    }, geometry="geometry", crs=None)
    return table[list(WORKING_SET_COLUMNS)].reset_index(drop=True), reason
