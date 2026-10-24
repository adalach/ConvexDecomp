"""The OSM adapter: from a sample of BEM4AI footprints to the working set of the convex decomposition.

| Step | From level | Function | Writes |
| --- | --- | --- | --- |
| 1 Import | all | `load_sample`, `bem4ai_comparison` | the OSM outline (WGS84) and BEM4AI's version of it |
| 2 Local frame | all (L0) | `local_frame_step` | ``geom_local_raw``: metres, bounding-box centre at (0, 0) |
| 3 Vertices | L1 | `vertices_step` | ``geom_local_vertices``: duplicate and collinear vertices dropped |
| 4 Thin parts | L1 | `thin_parts_step`, then the check ``footprint_thin_parts`` | ``geom_local_thin`` |
| 5 Axes | L2 | `axes_step` | the architectural axes of each footprint (``axes``) |
| 6 Normalization | L2 | `normalization_step` | ``geom_local``: the outline on its axes |
| 7 Checks | all | `check_footprints` with every check of ``config/checks.json`` | the checks table |
| 8 Working set | all | `working_set` | ``subset_buildings_gdf`` for ``OSM_2_Decomposition.ipynb`` |

Every step keeps its own geometry column, so each footprint can be compared with the outline it
started from; below the level a step belongs to, it hands its input on unchanged. `run_adapter` runs
them all and records in a funnel where each footprint leaves and why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import shapely

from convexdecomp.core.convexity import convexity_mask
from convexdecomp.osm.axes import AxesSettings, find_axes, residual_steps, snap_to_axes
from convexdecomp.osm.footprints import footprint_fingerprint, footprint_shapes, thin_hole_counts
from convexdecomp.osm.local_frames import sync_geometry, to_local_frames
from convexdecomp.osm.normalize import count_exterior_vertices
from convexdecomp.osm.rules import Rules
from convexdecomp.osm.thin_parts import THIN_PART_COLUMNS, erase_thin_parts
from convexdecomp.osm.vertices import VertexCleanup, remove_redundant_vertices

__all__ = [
    "IDENTITY_COLUMNS",
    "LEVELS",
    "WORKING_SET_COLUMNS",
    "AdapterRun",
    "AdapterSettings",
    "axes_step",
    "bem4ai_comparison",
    "check_footprints",
    "fidelity",
    "load_sample",
    "local_frame_step",
    "normalization_step",
    "reflex_vertex_counts",
    "run_adapter",
    "summarize_checks",
    "thin_parts_step",
    "vertices_step",
    "working_set",
]

LEVELS = ("L0", "L1", "L2")
#: The columns that name a footprint in every table, as in BEM4AI's checks tables.
IDENTITY_COLUMNS = ("sample_id", "source_sample_id", "footprint_fingerprint")
#: The columns of the working table ``OSM_2_Decomposition.ipynb`` reads.
WORKING_SET_COLUMNS = ("sample_id", "geometry", "area_m2", "n_vertices", "n_holes")
_OVERLAY_GRID_M = 1e-6


@dataclass(frozen=True)
class AdapterSettings:
    """The preprocessing level, the settings of the vertex cleanup and the axes, and the working-set rule."""

    level: str = "L2"
    vertices: VertexCleanup = field(default_factory=lambda: VertexCleanup(0.01, 0.05))
    axes: AxesSettings = field(default_factory=AxesSettings)
    #: ``"all"``: every footprint that passes the checks; ``"paper"``: non-convex with 6 to 32 vertices.
    working_set_rule: str = "all"


def _at_least(level: str, minimum: str) -> bool:
    if level not in LEVELS:
        raise ValueError(f"Unknown preprocessing level {level!r}; expected one of {LEVELS}.")
    return LEVELS.index(level) >= LEVELS.index(minimum)


def _step(gdf: gpd.GeoDataFrame, source: str, target: str, values) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out[target] = pd.Series(list(values) if values is not None else list(out[source]), index=out.index, dtype=object)
    return sync_geometry(out, target)


# ---------------------------------------------------------------------------------------------- 1 import
def load_sample(path: str | Path, layer: str = "footprints", bem4ai_layer: str = "footprints_bem4ai") -> gpd.GeoDataFrame:
    """The footprints of a sample as mapped, with BEM4AI's version of each as ``geometry_bem4ai`` when the file has it.

    ``outline_valid`` says whether the outline is a valid polygon as mapped; it is not repaired.
    A set without ``source_sample_id`` or ``footprint_fingerprint`` gets them from its ``sample_id``
    and its outline.
    """
    gdf = gpd.read_file(path, layer=layer)
    gdf["sample_id"] = gdf["sample_id"].astype(str)
    if bem4ai_layer in [name for name, _ in pyogrio.list_layers(path)]:
        bem = gpd.read_file(path, layer=bem4ai_layer).set_index("sample_id").geometry
        gdf["geometry_bem4ai"] = gpd.GeoSeries(bem.reindex(gdf["sample_id"]).to_numpy(), index=gdf.index, crs=gdf.crs)
    if "source_sample_id" not in gdf.columns:
        gdf["source_sample_id"] = gdf["sample_id"]
    if "footprint_fingerprint" not in gdf.columns:
        gdf["footprint_fingerprint"] = [footprint_fingerprint(geom) for geom in gdf.geometry]
    gdf["outline_valid"] = gdf.geometry.is_valid.to_numpy()
    leading = [*IDENTITY_COLUMNS]
    return gdf[leading + [c for c in gdf.columns if c not in leading]]


def bem4ai_comparison(gdf: pd.DataFrame) -> pd.DataFrame:
    """How BEM4AI's version of each footprint differs from the OSM outline, both in the local frame.

    ``vertices_osm`` and ``vertices_bem4ai`` (exterior), ``bem4ai_hausdorff_m`` and ``bem4ai_area_change_pct``.
    """
    raw, bem = gdf["geom_local_raw"].to_numpy(), gdf["geom_local_bem4ai"].to_numpy()
    return pd.DataFrame({
        "vertices_osm": [count_exterior_vertices(g) for g in raw],
        "vertices_bem4ai": [count_exterior_vertices(g) for g in bem],
        "bem4ai_hausdorff_m": shapely.hausdorff_distance(raw, bem),
        "bem4ai_area_change_pct": 100.0 * (shapely.area(bem) - shapely.area(raw)) / shapely.area(raw),
    }, index=gdf.index)


# ----------------------------------------------------------------------------------------- 2 local frame
def local_frame_step(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Every footprint in metres with the centre of its bounding box at (0, 0): ``geom_local_raw`` (and ``geom_local_bem4ai``)."""
    columns = {"geometry": "geom_local_raw"}
    if "geometry_bem4ai" in gdf.columns:
        columns["geometry_bem4ai"] = "geom_local_bem4ai"
    return to_local_frames(gdf, columns)


# ------------------------------------------------------------------------------------------- 3 vertices
def vertices_step(gdf: gpd.GeoDataFrame, settings: AdapterSettings) -> gpd.GeoDataFrame:
    """Duplicate and collinear vertices dropped (L1): ``geom_local_vertices`` and ``vertices_removed``."""
    if not _at_least(settings.level, "L1"):
        out = _step(gdf, "geom_local_raw", "geom_local_vertices", None)
        out["vertices_removed"] = 0
        return out
    cleaned = [remove_redundant_vertices(geom, settings.vertices) for geom in gdf["geom_local_raw"]]
    out = _step(gdf, "geom_local_raw", "geom_local_vertices", [g for g, _ in cleaned])
    out["vertices_removed"] = [n for _, n in cleaned]
    return out


# ----------------------------------------------------------------------------------------- 4 thin parts
def thin_parts_step(gdf: gpd.GeoDataFrame, rules: Rules, settings: AdapterSettings) -> gpd.GeoDataFrame:
    """Parts narrower than ``footprint.thin_part_width_m`` erased (L1): ``geom_local_thin`` and `THIN_PART_COLUMNS`."""
    if not _at_least(settings.level, "L1"):
        return _step(gdf, "geom_local_vertices", "geom_local_thin", None)
    width = float(rules.thresholds["footprint"]["thin_part_width_m"])
    erased, report = erase_thin_parts(gdf["geom_local_vertices"].to_numpy(), width, settings.vertices)
    out = _step(gdf, "geom_local_vertices", "geom_local_thin", erased)
    for column in THIN_PART_COLUMNS:
        out[column] = report[column].to_numpy()
    return out


# ------------------------------------------------------------------------------------------------ 5 axes
def axes_step(gdf: gpd.GeoDataFrame, settings: AdapterSettings) -> gpd.GeoDataFrame:
    """The architectural axes of each footprint (L2): ``axes``, ``n_directions`` and ``n_wall_lines``."""
    out = gdf.copy()
    if not _at_least(settings.level, "L2"):
        out["axes"] = None
        return out
    axes = [find_axes(geom, settings.axes) if geom.geom_type == "Polygon" else None for geom in out["geom_local_thin"]]
    out["axes"] = pd.Series(axes, index=out.index, dtype=object)
    out["n_directions"] = [len(a.directions) if a is not None else 0 for a in axes]
    out["n_wall_lines"] = [len(a.lines) if a is not None else 0 for a in axes]
    return out


# --------------------------------------------------------------------------------------- 6 normalization
def normalization_step(gdf: gpd.GeoDataFrame, settings: AdapterSettings) -> gpd.GeoDataFrame:
    """The outline moved onto its axes (L2): ``geom_local`` and ``axes_status`` (`snap_to_axes`)."""
    if not _at_least(settings.level, "L2"):
        out = _step(gdf, "geom_local_thin", "geom_local", None)
        out["axes_status"] = "off"
        return out
    snapped = [snap_to_axes(geom, settings.axes, axes) for geom, axes in zip(gdf["geom_local_thin"], gdf["axes"])]
    out = _step(gdf, "geom_local_thin", "geom_local", [g for g, _, _ in snapped])
    out["axes_status"] = [status for _, status, _ in snapped]
    return out


# ---------------------------------------------------------------------------------------------- checks
def check_footprints(gdf: gpd.GeoDataFrame, geometry_col: str, rules: Rules, names=None) -> pd.DataFrame:
    """The checks table of `geometry_col`, as BEM4AI writes ``checks_stage<N>.csv``.

    One row per footprint: the identity columns (the fingerprint as ``fingerprint``), the
    measurements (`footprint_shapes`, ``n_thin_holes``, and the thin-part report when the frame has
    it), the failed checks of each kind (``hard_failures``, ``review_flags``, ``rerun_flags``) and
    ``written_at``. With `names`, only those checks are evaluated.
    """
    geometries = gdf[geometry_col].to_numpy()
    measured = footprint_shapes(geometries)
    measured["n_thin_holes"] = thin_hole_counts(geometries, float(rules.thresholds["footprint"]["thin_hole_width_m"]))
    measured.index = gdf.index
    for column in THIN_PART_COLUMNS:
        if column in gdf.columns:
            measured[column] = gdf[column]
    table = pd.concat([
        gdf[["sample_id", "source_sample_id"]].astype(str),
        gdf["footprint_fingerprint"].astype(str).rename("fingerprint"),
        measured,
        rules.evaluate(measured, names=names),
    ], axis=1)
    table["written_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return table


def summarize_checks(checks: pd.DataFrame, rules: Rules, names=None) -> pd.DataFrame:
    """Per check evaluated on `checks`: its kind, rule and how many footprints fail it."""
    flag_column = {"remove": "hard_failures", "review": "review_flags", "rerun": "rerun_flags"}
    rows = []
    for check in rules.available(checks.columns, names=names):
        failing = checks[flag_column[check.kind]].str.split(";").map(lambda found, name=check.name: name in found)
        rows.append({"check": check.name, "kind": check.kind, "rule": check.rule(rules.thresholds),
                     "failing": int(failing.sum())})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------- measures
def reflex_vertex_counts(geometries, tol_deg: float = 1.0) -> np.ndarray:
    """The reflex corners of each outline: turns against the ring's orientation by more than `tol_deg` (exterior only)."""
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


# --------------------------------------------------------------------------------------------- working set
def working_set(gdf: gpd.GeoDataFrame, rule: str = "all") -> tuple[gpd.GeoDataFrame, pd.Series]:
    """The decomposition working set in the format ``OSM_2_Decomposition.ipynb`` reads, and why the others are out.

    `rule` is ``"all"`` (every footprint that reached this point) or ``"paper"`` (the EG-ICE 2026
    working set: non-convex footprints with 6 to 32 exterior vertices). Returns the table with
    `WORKING_SET_COLUMNS` (``geometry`` is ``geom_local``) and, on the index of `gdf`, the reason
    each footprint left ("" for those in the set).
    """
    geometries = gdf["geom_local"].to_numpy()
    n_vertices = np.array([count_exterior_vertices(geom) for geom in geometries])
    reason = pd.Series("", index=gdf.index, dtype=object)
    if rule == "paper":
        convex = convexity_mask(geometries)
        reason[convex] = "convex"
        reason[~convex & ((n_vertices < 6) | (n_vertices > 32))] = "vertices outside 6-32"
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


# ----------------------------------------------------------------------------------------------- the run
@dataclass
class AdapterRun:
    """Everything one run of the adapter produces."""

    footprints: gpd.GeoDataFrame        # every footprint that reached the checks, with all geometry columns
    final: gpd.GeoDataFrame             # the footprints that passed them, with fidelity and complexity
    working_set: gpd.GeoDataFrame       # the table for OSM_2_Decomposition.ipynb
    checks_thin_parts: pd.DataFrame     # step 4
    checks: pd.DataFrame                # step 7
    funnel: pd.DataFrame                # one row per footprint of the sample


def _leave(funnel: pd.DataFrame, sample_ids, step: str, reasons) -> None:
    ids = list(sample_ids)
    if ids:
        funnel.loc[ids, "left_at"] = step
        funnel.loc[ids, "reason"] = list(reasons)


def run_adapter(path: str | Path, rules: Rules, settings: AdapterSettings = AdapterSettings()) -> AdapterRun:
    """All steps of the adapter on a footprint sample (see the module docstring)."""
    gdf = local_frame_step(load_sample(path))
    funnel = gdf[list(IDENTITY_COLUMNS)].set_index("sample_id", drop=False).copy()
    funnel["left_at"], funnel["reason"] = "", ""
    funnel["n_vertices_raw"] = gdf["geom_local_raw"].map(count_exterior_vertices).to_numpy()
    if "geom_local_bem4ai" in gdf.columns:
        gdf = gdf.join(bem4ai_comparison(gdf))
    invalid = ~gdf["outline_valid"].astype(bool)
    _leave(funnel, gdf.loc[invalid, "sample_id"], "import", ["outline not valid as mapped"] * int(invalid.sum()))
    gdf = gdf.loc[~invalid]

    gdf = vertices_step(gdf, settings)
    gdf = thin_parts_step(gdf, rules, settings)
    reached_thin = gdf.copy()
    checks_thin = check_footprints(gdf, "geom_local_thin", rules, names=["footprint_thin_parts"])
    failed = checks_thin["hard_failures"].ne("")
    _leave(funnel, gdf.loc[failed, "sample_id"], "thin parts", checks_thin.loc[failed, "hard_failures"])
    gdf = gdf.loc[~failed]

    gdf = axes_step(gdf, settings)
    gdf = normalization_step(gdf, settings)

    checks = check_footprints(gdf, "geom_local", rules)
    failed = checks["hard_failures"].ne("")
    _leave(funnel, gdf.loc[failed, "sample_id"], "checks", checks.loc[failed, "hard_failures"])
    final = gdf.loc[~failed].copy()
    final = final.join(fidelity(final["geom_local_raw"], final["geom_local"]).set_axis(final.index))
    final["n_reflex_raw"] = reflex_vertex_counts(final["geom_local_raw"].to_numpy())
    final["n_reflex"] = reflex_vertex_counts(final["geom_local"].to_numpy())
    final["residual_steps"] = [residual_steps(g) for g in final["geom_local"]]

    table, reason = working_set(final, settings.working_set_rule)
    out_of_set = reason.ne("")
    _leave(funnel, final.loc[out_of_set, "sample_id"], "working set", reason[out_of_set])

    # What each step measured, for every footprint that reached it.
    for frame, columns in ((reached_thin, ("bem4ai_hausdorff_m", "vertices_removed", *THIN_PART_COLUMNS)),
                           (gdf, ("n_directions", "n_wall_lines", "axes_status")),
                           (final, ("fidelity_hausdorff_m", "fidelity_iou", "fidelity_area_change_pct",
                                    "n_reflex_raw", "n_reflex", "residual_steps"))):
        present = [c for c in columns if c in frame.columns]
        funnel = funnel.join(frame.set_index("sample_id")[present])
    record = final.set_index("sample_id")
    funnel["n_vertices"] = record["geom_local"].map(count_exterior_vertices).reindex(funnel.index)
    funnel["preprocessing_level"] = settings.level
    funnel["working_set_rule"] = settings.working_set_rule
    return AdapterRun(gdf, final, table, checks_thin, checks, funnel.reset_index(drop=True))
