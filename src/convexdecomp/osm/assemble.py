"""
Reusable post-perimeter helpers for zone-piece convexity and decomposition.

The module keeps notebook cells thin by moving:
- piece-level convexity bookkeeping,
- per-shape convexity summaries,
- convex decomposition of only the non-convex pieces,
- area-conservation validation of the final rebuilt shapes,
out of the notebook and into plain Python functions.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from shapely.ops import unary_union

from convexdecomp.geometry.holes import holeless_polygons
from convexdecomp.osm.decompose import (
    ConvexDecompositionConfig,
    decompose_polygon_with_stats,
)
from convexdecomp.osm.perimeter import normalize_to_polygons
from convexdecomp.core.convexity import is_convex_polygon as shared_is_convex_polygon


def build_direct_shape_convexity_tables(
    shape_gdf: gpd.GeoDataFrame,
    *,
    id_col: str = "sample_id",
    geometry_col: str = "geometry",
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, gpd.GeoDataFrame]:
    zone_piece_rows: list[dict] = []

    for _, row in shape_gdf.iterrows():
        source_geom = row.get(geometry_col)
        for piece_idx, polygon in enumerate(normalize_to_polygons(source_geom), start=1):
            zone_piece_rows.append(
                {
                    id_col: row[id_col],
                    "zone_id": f"{row[id_col]}_original_{piece_idx:02d}",
                    "piece_type": "original",
                    "convexity_source": "checked_original",
                    "is_convex": bool(shared_is_convex_polygon(polygon)),
                    "geometry": polygon,
                }
            )

    zone_convexity_gdf = gpd.GeoDataFrame(zone_piece_rows, geometry="geometry", crs=None)
    shape_convexity_summary_df = (
        zone_convexity_gdf.groupby(id_col)
        .agg(
            n_zone_parts=("zone_id", "size"),
            n_nonconvex_parts=("is_convex", lambda s: int((~s).sum())),
            all_parts_convex=("is_convex", "all"),
            n_checked_originals=("piece_type", lambda s: int((s == "original").sum())),
        )
        .reset_index()
    )

    updated_shape_gdf = shape_gdf.drop(
        columns=["all_parts_convex", "n_nonconvex_parts", "n_zone_parts"],
        errors="ignore",
    ).merge(
        shape_convexity_summary_df[[id_col, "n_zone_parts", "n_nonconvex_parts", "all_parts_convex"]],
        on=id_col,
        how="left",
    )
    return zone_convexity_gdf, shape_convexity_summary_df, updated_shape_gdf


def build_zone_convexity_tables(
    trapezoid_split_gdf: gpd.GeoDataFrame,
    trapezoid_parts_gdf: gpd.GeoDataFrame,
    corner_parts_gdf: gpd.GeoDataFrame,
    trapezoid_remainder_parts_gdf: gpd.GeoDataFrame,
    *,
    id_col: str = "sample_id",
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, gpd.GeoDataFrame]:
    zone_piece_rows: list[dict] = []

    for _, row in trapezoid_parts_gdf.iterrows():
        zone_piece_rows.append(
            {
                id_col: row[id_col],
                "zone_id": row["zone_id"],
                "piece_type": "trapezoid",
                "convexity_source": "assumed_trapezoid",
                "is_convex": True,
                "geometry": row.geometry,
            }
        )

    for _, row in corner_parts_gdf.iterrows():
        zone_piece_rows.append(
            {
                id_col: row[id_col],
                "zone_id": row["zone_id"],
                "piece_type": "triangle",
                "convexity_source": "assumed_triangle",
                "is_convex": True,
                "geometry": row.geometry,
            }
        )

    for _, row in trapezoid_split_gdf.loc[trapezoid_split_gdf["interior_geom"].notna()].iterrows():
        for interior_idx, polygon in enumerate(normalize_to_polygons(row["interior_geom"]), start=1):
            zone_piece_rows.append(
                {
                    id_col: row[id_col],
                    "zone_id": f"{row[id_col]}_interior_{interior_idx:02d}",
                    "piece_type": "interior",
                    "convexity_source": "checked_interior",
                    "is_convex": bool(shared_is_convex_polygon(polygon)),
                    "geometry": polygon,
                }
            )

    for _, row in trapezoid_remainder_parts_gdf.iterrows():
        zone_piece_rows.append(
            {
                id_col: row[id_col],
                "zone_id": row["zone_id"],
                "piece_type": "remainder",
                "convexity_source": "checked_remainder",
                "is_convex": bool(shared_is_convex_polygon(row.geometry)),
                "geometry": row.geometry,
            }
        )

    zone_convexity_gdf = gpd.GeoDataFrame(zone_piece_rows, geometry="geometry", crs=None)
    shape_convexity_summary_df = (
        zone_convexity_gdf.groupby(id_col)
        .agg(
            n_zone_parts=("zone_id", "size"),
            n_nonconvex_parts=("is_convex", lambda s: int((~s).sum())),
            all_parts_convex=("is_convex", "all"),
            n_checked_interiors=("piece_type", lambda s: int((s == "interior").sum())),
            n_checked_remainders=("piece_type", lambda s: int((s == "remainder").sum())),
        )
        .reset_index()
    )

    updated_split_gdf = trapezoid_split_gdf.drop(
        columns=["all_parts_convex", "n_nonconvex_parts", "n_zone_parts"],
        errors="ignore",
    ).merge(
        shape_convexity_summary_df[[id_col, "n_zone_parts", "n_nonconvex_parts", "all_parts_convex"]],
        on=id_col,
        how="left",
    )
    return zone_convexity_gdf, shape_convexity_summary_df, updated_split_gdf


def _decompose_source_polygon(
    polygon: Polygon,
    *,
    primary_cfg: ConvexDecompositionConfig,
) -> tuple[list[Polygon], str]:
    primary_result = decompose_polygon_with_stats(polygon, primary_cfg)
    candidate_parts = (
        primary_result["best_variant"]
        if primary_result["convex_success"] and primary_result["best_variant"]
        else []
    )
    if candidate_parts:
        return candidate_parts, "osm_convex_decomposer"
    return [], "failed"


def decompose_zone_pieces_to_convex_parts(
    zone_convexity_gdf: gpd.GeoDataFrame,
    shape_gdf: gpd.GeoDataFrame,
    *,
    id_col: str = "sample_id",
    primary_cfg: ConvexDecompositionConfig | None = None,
    area_tol_m2: float = 1e-6,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, pd.DataFrame, gpd.GeoDataFrame]:
    primary_cfg = primary_cfg or ConvexDecompositionConfig(
        search_depth=7,
        search_width=2,
        max_variants_per_polygon=32,
        max_failed_states=500,
        track_time=False,
        min_area_m2=2.0,
        weight_parts=1.0,
        weight_compactness=20.0,
    )

    final_convex_rows: list[dict] = []
    convex_decomposition_piece_stats: list[dict] = []

    for _, row in zone_convexity_gdf.sort_values([id_col, "zone_id"]).iterrows():
        source_geom = row.geometry
        source_area_m2 = float(source_geom.area)

        if row["is_convex"]:
            final_parts = [source_geom]
            method_labels = ["passthrough"]
            hole_preprocessed = False
            n_holeless_source_parts = 1
        else:
            hole_preprocessed = len(source_geom.interiors) > 0
            source_subparts = (
                holeless_polygons(
                    source_geom,
                    area_tol=1e-6,
                    max_edges_per_hole=None,
                    use_triangulation_fallback=True,
                )
                if hole_preprocessed
                else [source_geom]
            )
            n_holeless_source_parts = len(source_subparts)
            final_parts: list[Polygon] = []
            method_labels: list[str] = []

            for source_subpart in source_subparts:
                candidate_parts, chosen_method = _decompose_source_polygon(
                    source_subpart,
                    primary_cfg=primary_cfg,
                )
                if not candidate_parts:
                    final_parts = []
                    method_labels = ["failed"]
                    break
                if not all(shared_is_convex_polygon(part) for part in candidate_parts):
                    final_parts = []
                    method_labels = ["failed_nonconvex_output"]
                    break
                final_parts.extend(candidate_parts)
                method_labels.append(chosen_method)

        output_union = unary_union(final_parts) if final_parts else None
        output_area_sum_m2 = float(sum(part.area for part in final_parts))
        output_union_area_m2 = float(output_union.area) if output_union is not None else 0.0
        convex_decomposition_piece_stats.append(
            {
                id_col: row[id_col],
                "source_zone_id": row["zone_id"],
                "source_piece_type": row["piece_type"],
                "source_is_convex": bool(row["is_convex"]),
                "hole_preprocessed": hole_preprocessed,
                "n_holeless_source_parts": n_holeless_source_parts,
                "decomposition_method": ",".join(sorted(set(method_labels))),
                "decomposition_success": bool(final_parts),
                "n_output_parts": len(final_parts),
                "source_area_m2": source_area_m2,
                "output_area_sum_m2": output_area_sum_m2,
                "output_union_area_m2": output_union_area_m2,
                "area_sum_error_m2": output_area_sum_m2 - source_area_m2,
                "area_union_error_m2": output_union_area_m2 - source_area_m2,
            }
        )

        for final_piece_idx, polygon in enumerate(final_parts, start=1):
            final_convex_rows.append(
                {
                    id_col: row[id_col],
                    "source_zone_id": row["zone_id"],
                    "source_piece_type": row["piece_type"],
                    "final_piece_idx": final_piece_idx,
                    "final_zone_id": f"{row['zone_id']}_convex_{final_piece_idx:02d}",
                    "decomposition_method": ",".join(sorted(set(method_labels))),
                    "geometry": polygon,
                }
            )

    final_convex_parts_gdf = gpd.GeoDataFrame(final_convex_rows, geometry="geometry", crs=None)
    convex_decomposition_piece_stats_df = pd.DataFrame(convex_decomposition_piece_stats)

    shape_summary_rows: list[dict] = []
    for _, row in shape_gdf.iterrows():
        sample_id = row[id_col]
        final_parts = list(final_convex_parts_gdf.loc[final_convex_parts_gdf[id_col] == sample_id, "geometry"])
        final_union = unary_union(final_parts) if final_parts else None
        original_area_m2 = float(row.geometry.area)
        final_sum_area_m2 = float(sum(part.area for part in final_parts))
        final_union_area_m2 = float(final_union.area) if final_union is not None else 0.0
        shape_summary_rows.append(
            {
                id_col: sample_id,
                "n_final_convex_parts": len(final_parts),
                "original_area_m2": original_area_m2,
                "final_sum_area_m2": final_sum_area_m2,
                "final_union_area_m2": final_union_area_m2,
                "area_sum_error_m2": final_sum_area_m2 - original_area_m2,
                "area_union_error_m2": final_union_area_m2 - original_area_m2,
                "all_final_parts_convex": bool(final_parts)
                and all(shared_is_convex_polygon(part) for part in final_parts),
            }
        )

    shape_convex_decomposition_summary_df = pd.DataFrame(shape_summary_rows)

    failed_piece_df = convex_decomposition_piece_stats_df.loc[
        ~convex_decomposition_piece_stats_df["decomposition_success"]
    ].copy()
    invalid_shape_df = shape_convex_decomposition_summary_df.loc[
        (~shape_convex_decomposition_summary_df["all_final_parts_convex"])
        | (shape_convex_decomposition_summary_df["area_sum_error_m2"].abs() > area_tol_m2)
        | (shape_convex_decomposition_summary_df["area_union_error_m2"].abs() > area_tol_m2)
    ].copy()

    if not failed_piece_df.empty:
        raise ValueError(
            "Convex decomposition failed for: "
            + ", ".join(failed_piece_df["source_zone_id"].tolist())
        )
    if not invalid_shape_df.empty:
        raise ValueError(
            "Final convex rebuild failed area/convexity checks for: "
            + ", ".join(invalid_shape_df[id_col].tolist())
        )

    updated_shape_gdf = shape_gdf.drop(
        columns=["n_final_convex_parts", "final_all_parts_convex", "final_area_union_error_m2"],
        errors="ignore",
    ).merge(
        shape_convex_decomposition_summary_df[
            [id_col, "n_final_convex_parts", "all_final_parts_convex", "area_union_error_m2"]
        ].rename(
            columns={
                "all_final_parts_convex": "final_all_parts_convex",
                "area_union_error_m2": "final_area_union_error_m2",
            }
        ),
        on=id_col,
        how="left",
    )

    return (
        final_convex_parts_gdf,
        convex_decomposition_piece_stats_df,
        shape_convex_decomposition_summary_df,
        updated_shape_gdf,
    )
