from __future__ import annotations

from typing import Any

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import colormaps as cmaps
from matplotlib.lines import Line2D
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from convexdecomp.osm.axis_alignment import alignment_axis_records_for_polygon
from convexdecomp.osm.decompose import decompose_polygon_with_stats, extract_polygons
from convexdecomp.osm.subdivide import subdivide_perimeter_gdf
from convexdecomp.plotting.geometry_grid import GHSL_gridplot

__all__ = [
    "annotate_polygon_failure_reasons",
    "build_parts_geometry",
    "classify_failure_reason",
    "collect_building_polygons",
    "collect_decomposition_polygons",
    "count_polygon_vertices",
    "count_exterior_direction_groups",
    "group_seed_parts",
    "min_part_area",
    "normalize_input_geometry",
    "parts_from_geom",
    "prepare_decomposition_inputs",
    "plot_alignment_family_showcase",
    "run_building_decomposition",
    "select_complexity_ids",
    "summarize_building_decomposition",
    "sum_time_list",
]


def normalize_input_geometry(geom):
    if isinstance(geom, MultiPolygon):
        parts = [part for part in geom.geoms if part is not None and not getattr(part, "is_empty", True)]
        if len(parts) == 1:
            return parts[0]
    return geom


def count_exterior_direction_groups(geom, angle_tol_deg: float = 1.0) -> int:
    if not isinstance(geom, Polygon) or geom.is_empty:
        return 0

    coords = list(geom.exterior.coords)[:-1]
    if len(coords) < 2:
        return 0

    angles = []
    for idx, a in enumerate(coords):
        b = coords[(idx + 1) % len(coords)]
        dx = float(b[0] - a[0])
        dy = float(b[1] - a[1])
        length = float((dx * dx + dy * dy) ** 0.5)
        if length <= 1e-9:
            continue
        angles.append((np.degrees(np.arctan2(dy, dx)) + 360.0) % 180.0)

    if not angles:
        return 0

    ordered = sorted(float(angle) for angle in angles)
    groups = [[ordered[0]]]
    for angle in ordered[1:]:
        if abs(angle - np.mean(groups[-1])) <= angle_tol_deg:
            groups[-1].append(angle)
        else:
            groups.append([angle])

    if len(groups) > 1:
        wrap_members = groups[-1] + [angle + 180.0 for angle in groups[0]]
        wrap_span = max(wrap_members) - min(wrap_members)
        if wrap_span <= angle_tol_deg:
            groups = [groups[-1] + groups[0], *groups[1:-1]]

    return len(groups)


def collect_building_polygons(row: pd.Series) -> list[Polygon]:
    if bool(row.get("perimeter_defined", False)):
        polys: list[Polygon] = []
        polys.extend(extract_polygons(row.get("perimeter_parts")))
        polys.extend(extract_polygons(row.get("interior_geom")))
        return polys
    return extract_polygons(row.get("geometry"))


def count_polygon_vertices(polygons: list[Polygon]) -> int:
    total = 0
    for poly in polygons:
        try:
            total += max(0, len(list(poly.exterior.coords)) - 1)
        except Exception:
            pass
    return total


def group_seed_parts(parts_gdf: gpd.GeoDataFrame, *, id_col: str = "sample_id") -> dict[str, list[Polygon]]:
    grouped: dict[str, list[Polygon]] = {}
    if parts_gdf is None or parts_gdf.empty:
        return grouped
    for sample_id, group in parts_gdf.groupby(id_col):
        grouped[sample_id] = [
            geom for geom in group.geometry
            if geom is not None and not getattr(geom, "is_empty", True)
        ]
    return grouped


def collect_decomposition_polygons(
    row: pd.Series,
    *,
    use_trapezoid_triangle_precheck: bool,
    trapezoid_seed_map: dict[str, list[Polygon]] | None = None,
    corner_seed_map: dict[str, list[Polygon]] | None = None,
    remainder_seed_map: dict[str, list[Polygon]] | None = None,
) -> list[Polygon]:
    if not use_trapezoid_triangle_precheck:
        return collect_building_polygons(row)

    if bool(row.get("perimeter_defined", False)):
        sample_id = row["sample_id"]
        polygons: list[Polygon] = []
        polygons.extend((trapezoid_seed_map or {}).get(sample_id, []))
        polygons.extend((corner_seed_map or {}).get(sample_id, []))
        polygons.extend((remainder_seed_map or {}).get(sample_id, []))
        polygons.extend(extract_polygons(row.get("interior_geom")))
        return polygons

    return extract_polygons(row.get("geometry"))


def prepare_decomposition_inputs(
    subset_buildings_gdf: gpd.GeoDataFrame,
    *,
    use_trapezoid_triangle_precheck: bool,
    precheck_subdiv_cfg: Any,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    buildings_convex_gdf = subset_buildings_gdf.copy()

    if use_trapezoid_triangle_precheck:
        buildings_convex_gdf, trapezoid_seed_gdf, corner_seed_gdf, remainder_seed_gdf = subdivide_perimeter_gdf(
            buildings_convex_gdf,
            id_col="sample_id",
            cfg=precheck_subdiv_cfg,
        )
        trapezoid_seed_map = group_seed_parts(trapezoid_seed_gdf)
        corner_seed_map = group_seed_parts(corner_seed_gdf)
        remainder_seed_map = group_seed_parts(remainder_seed_gdf)
    else:
        empty_geometry = gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=buildings_convex_gdf.crs)
        trapezoid_seed_gdf = empty_geometry.copy()
        corner_seed_gdf = empty_geometry.copy()
        remainder_seed_gdf = empty_geometry.copy()
        trapezoid_seed_map = {}
        corner_seed_map = {}
        remainder_seed_map = {}

    buildings_convex_gdf["decomp_polygons"] = buildings_convex_gdf.apply(
        collect_decomposition_polygons,
        axis=1,
        use_trapezoid_triangle_precheck=use_trapezoid_triangle_precheck,
        trapezoid_seed_map=trapezoid_seed_map,
        corner_seed_map=corner_seed_map,
        remainder_seed_map=remainder_seed_map,
    )
    buildings_convex_gdf["n_polygons"] = buildings_convex_gdf["decomp_polygons"].apply(len)
    buildings_convex_gdf["n_vertices"] = buildings_convex_gdf["decomp_polygons"].apply(count_polygon_vertices)
    return buildings_convex_gdf, trapezoid_seed_gdf, corner_seed_gdf, remainder_seed_gdf


def plot_alignment_family_showcase(
    aligned_plot_gdf: gpd.GeoDataFrame,
    buildings_gdf: gpd.GeoDataFrame,
    edge_alignment_cfg: Any,
) -> None:
    if aligned_plot_gdf.empty:
        print("No buildings changed under the current edge-alignment settings.")
        return

    axis_rows = []
    axis_count_by_id = {}
    family_info_by_id = {}
    for _, row in aligned_plot_gdf.iterrows():
        axis_records, axis_diag = alignment_axis_records_for_polygon(row.geometry, edge_alignment_cfg)
        axis_count_by_id[row["sample_id"]] = axis_diag["n_axes"]
        family_lookup = {}
        for axis_idx, record in enumerate(axis_records):
            label = f"{record['family_angle_deg']:.1f}°"
            family_lookup[label] = float(record["family_angle_deg"])
            axis_rows.append(
                {
                    "sample_id": row["sample_id"],
                    "axis_idx": axis_idx,
                    "family_label": label,
                    "family_angle_deg": float(record["family_angle_deg"]),
                    "geometry": record["geometry"],
                }
            )
        family_info_by_id[row["sample_id"]] = sorted(family_lookup.items(), key=lambda item: item[1])

    before_align_gdf = gpd.GeoDataFrame(aligned_plot_gdf.copy(), geometry="geometry_before_align", crs=buildings_gdf.crs)
    after_align_gdf = gpd.GeoDataFrame(aligned_plot_gdf.copy(), geometry="geometry", crs=buildings_gdf.crs)
    if axis_rows:
        axes_gdf = gpd.GeoDataFrame(axis_rows, geometry="geometry", crs=buildings_gdf.crs)
    else:
        axes_gdf = gpd.GeoDataFrame(
            columns=["sample_id", "axis_idx", "family_label", "family_angle_deg", "geometry"],
            geometry="geometry",
            crs=buildings_gdf.crs,
        )

    plot_ids = aligned_plot_gdf["sample_id"].tolist()
    plot_sources = [
        {
            "name": "before_align",
            "gdf": before_align_gdf,
            "role": "main",
            "id_col": "sample_id",
            "geometry_col": "geometry_before_align",
            "color": "#f4a261",
            "edgecolor": "#8d5524",
            "linewidth": 0.7,
        },
        {
            "name": "after_align",
            "gdf": after_align_gdf,
            "role": "main",
            "id_col": "sample_id",
            "geometry_col": "geometry",
            "color": "none",
            "edgecolor": "#1f77b4",
            "linewidth": 1.0,
            "zorder": 3,
        },
    ]

    def _sample_axes(sample_id):
        if axes_gdf.empty:
            return axes_gdf
        return axes_gdf.loc[axes_gdf["sample_id"] == sample_id].copy()

    fig_black, axes_black = GHSL_gridplot(
        plot_sources,
        ids=plot_ids,
        ncols=min(5, len(plot_ids)),
        figsize_scale=3.0,
        title_fn=lambda sid, i: (
            f"{sid}\n"
            f"{int(aligned_plot_gdf.iloc[i]['n_vertices_before_align'])} -> "
            f"{int(aligned_plot_gdf.iloc[i]['n_vertices_after_align'])} | "
            f"axes {int(axis_count_by_id.get(sid, 0))}"
        ),
    )
    axes_black = np.atleast_1d(axes_black).ravel()
    for i, sid in enumerate(plot_ids):
        sample_axes = _sample_axes(sid)
        if sample_axes.empty:
            continue
        sample_axes.plot(ax=axes_black[i], color="#111111", linewidth=1.0, linestyle="--", alpha=0.6, zorder=10)
    fig_black.suptitle("Step 1: all inferred axes before family grouping", y=1.02)
    fig_black.subplots_adjust(top=0.84, hspace=0.55)

    fig_color, axes_color = GHSL_gridplot(
        plot_sources,
        ids=plot_ids,
        ncols=min(5, len(plot_ids)),
        figsize_scale=3.0,
        title_fn=lambda sid, i: (
            f"{sid}\n"
            f"{int(aligned_plot_gdf.iloc[i]['n_vertices_before_align'])} -> "
            f"{int(aligned_plot_gdf.iloc[i]['n_vertices_after_align'])} | "
            f"fam {int(aligned_plot_gdf.iloc[i]['n_angle_families_align'])} | "
            f"axes {int(axis_count_by_id.get(sid, 0))}"
        ),
    )
    axes_color = np.atleast_1d(axes_color).ravel()
    for i, sid in enumerate(plot_ids):
        sample_axes = _sample_axes(sid)
        family_info = family_info_by_id.get(sid, [])
        if sample_axes.empty or not family_info:
            continue
        family_colors = {label: tuple(cmaps["tab10"](idx % 10)) for idx, (label, _) in enumerate(family_info)}
        legend_handles = []
        for label, _ in family_info:
            family_axes = sample_axes.loc[sample_axes["family_label"] == label]
            if family_axes.empty:
                continue
            family_axes.plot(ax=axes_color[i], color=family_colors[label], linewidth=1.0, linestyle="--", alpha=0.7, zorder=10)
            legend_handles.append(
                Line2D([0], [0], color=family_colors[label], linestyle="--", linewidth=1.2, label=label)
            )
        if legend_handles:
            axes_color[i].legend(
                handles=legend_handles,
                title="Axis families",
                loc="upper center",
                bbox_to_anchor=(0.5, -0.12),
                ncol=min(3, len(legend_handles)),
                frameon=False,
                fontsize=8,
                title_fontsize=8,
                handlelength=2.4,
            )
    fig_color.suptitle("Step 2: inferred axes grouped into angle families", y=1.02)
    fig_color.subplots_adjust(top=0.84, bottom=0.20, hspace=0.60)
    plt.show()


def build_parts_geometry(parts: list[Polygon]) -> BaseGeometry | None:
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return MultiPolygon(parts)


def sum_time_list(values: list[float | None] | None) -> float:
    if not isinstance(values, (list, tuple)):
        return 0.0
    clean_values = []
    for value in values:
        if value is None:
            continue
        try:
            clean_values.append(float(value))
        except (TypeError, ValueError):
            continue
    return float(np.nansum(clean_values)) if clean_values else 0.0


def min_part_area(parts: list[Polygon]) -> float:
    areas = []
    for part in parts or []:
        if part is None or getattr(part, "is_empty", True):
            continue
        try:
            areas.append(float(part.area))
        except Exception:
            continue
    return float(min(areas)) if areas else float("nan")


def run_building_decomposition(
    buildings_convex_gdf: gpd.GeoDataFrame,
    cfg: Any,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, gpd.GeoDataFrame]:
    results = []
    polygon_rows = []

    for idx, row in buildings_convex_gdf.iterrows():
        polygons = row.get("decomp_polygons") or []
        if not polygons:
            results.append(
                {
                    "index": idx,
                    "convex_decomp_success": False,
                    "convex_variants_total": 0,
                    "convex_parts_counts": [],
                    "polygon_fully_convex_flags": [],
                    "building_fully_convex": False,
                    "building_decomposition_failed": False,
                    "n_successful_polygons": 0,
                    "building_has_small_zone": False,
                    "convex_parts_geom": None,
                    "polygon_times_seconds": [],
                    "polygon_fallbacks": [],
                    "polygon_search_terminated_by": [],
                    "polygon_search_depths_used": [],
                    "polygon_search_widths_used": [],
                    "polygon_search_attempt_counts": [],
                    "n_failed_polygons": 0,
                    "n_small_zone_polygons": 0,
                    "n_retried_polygons": 0,
                    "total_search_attempts": 0,
                    "max_search_depth_used": 0,
                    "max_search_width_used": 0,
                    "time_total_seconds": 0.0,
                }
            )
            continue

        convex_parts = []
        convex_parts_counts = []
        polygon_flags = []
        polygon_times = []
        polygon_fallbacks = []
        polygon_stop_reasons = []
        polygon_search_depths = []
        polygon_search_widths = []
        polygon_search_attempt_counts = []
        convex_variants_total = 0
        building_has_small_zone = False
        n_failed_polygons = 0
        n_small_zone_polygons = 0

        for polygon_idx, polygon in enumerate(polygons, start=1):
            stats = decompose_polygon_with_stats(polygon, cfg)
            convex_variants_total += int(stats["n_variants"])
            convex_parts_counts.append(int(stats["n_parts"]))
            polygon_flags.append(bool(stats["fully_convex_best"]))
            polygon_times.append(stats.get("time_seconds"))
            polygon_fallbacks.append(stats.get("fallback_used"))
            polygon_stop_reasons.append(stats.get("search_terminated_by"))
            polygon_search_depths.append(stats.get("search_depth_used"))
            polygon_search_widths.append(stats.get("search_width_used"))
            polygon_search_attempt_counts.append(int(stats.get("search_attempt_count", 0) or 0))
            building_has_small_zone = building_has_small_zone or bool(stats["has_small_zone"])
            n_failed_polygons += int(not bool(stats["fully_convex_best"]))
            n_small_zone_polygons += int(bool(stats["has_small_zone"]))
            convex_parts.extend(stats["best_variant"])

            polygon_rows.append(
                {
                    "index": idx,
                    "sample_id": row["sample_id"],
                    "polygon_idx": polygon_idx,
                    "zone_id": f"{row['sample_id']}_poly_{polygon_idx:02d}",
                    "fully_convex_best": bool(stats["fully_convex_best"]),
                    "has_small_zone": bool(stats["has_small_zone"]),
                    "n_variants": int(stats["n_variants"]),
                    "n_parts": int(stats["n_parts"]),
                    "search_terminated_by": stats.get("search_terminated_by"),
                    "n_failed_terminal_states": int(stats.get("n_failed_terminal_states", 0) or 0),
                    "n_depth_limit_dead_ends": int(stats.get("n_depth_limit_dead_ends", 0) or 0),
                    "n_no_reflex_dead_ends": int(stats.get("n_no_reflex_dead_ends", 0) or 0),
                    "n_min_area_dead_ends": int(stats.get("n_min_area_dead_ends", 0) or 0),
                    "n_states_seen": int(stats.get("n_states_seen", 0) or 0),
                    "search_depth_used": stats.get("search_depth_used"),
                    "search_width_used": stats.get("search_width_used"),
                    "search_attempt_count": int(stats.get("search_attempt_count", 0) or 0),
                    "search_attempt_history": list(stats.get("search_attempt_history") or []),
                    "fallback_used": stats.get("fallback_used"),
                    "polygon_time_seconds": stats.get("time_seconds"),
                    "best_min_part_area_m2": min_part_area(stats["best_variant"]),
                    "geometry": polygon,
                    "building_geometry": row["geometry"],
                    "best_variant_geom": build_parts_geometry(stats["best_variant"]),
                }
            )

        building_fully_convex = bool(polygon_flags) and all(polygon_flags)
        n_successful_polygons = int(sum(polygon_flags))
        used_depths = [int(value) for value in polygon_search_depths if value is not None]
        used_widths = [int(value) for value in polygon_search_widths if value is not None]
        n_retried_polygons = int(sum(1 for attempts in polygon_search_attempt_counts if attempts > 1))
        total_search_attempts = int(sum(polygon_search_attempt_counts))

        results.append(
            {
                "index": idx,
                "convex_decomp_success": building_fully_convex,
                "convex_variants_total": convex_variants_total,
                "convex_parts_counts": convex_parts_counts,
                "polygon_fully_convex_flags": polygon_flags,
                "building_fully_convex": building_fully_convex,
                "building_decomposition_failed": n_failed_polygons > 0,
                "n_successful_polygons": n_successful_polygons,
                "building_has_small_zone": building_has_small_zone,
                "convex_parts_geom": build_parts_geometry(convex_parts),
                "polygon_times_seconds": polygon_times,
                "polygon_fallbacks": polygon_fallbacks,
                "polygon_search_terminated_by": polygon_stop_reasons,
                "polygon_search_depths_used": polygon_search_depths,
                "polygon_search_widths_used": polygon_search_widths,
                "polygon_search_attempt_counts": polygon_search_attempt_counts,
                "n_failed_polygons": n_failed_polygons,
                "n_small_zone_polygons": n_small_zone_polygons,
                "n_retried_polygons": n_retried_polygons,
                "total_search_attempts": total_search_attempts,
                "max_search_depth_used": max(used_depths, default=0),
                "max_search_width_used": max(used_widths, default=0),
                "time_total_seconds": sum_time_list(polygon_times),
            }
        )

    results_df = pd.DataFrame(results).set_index("index")
    buildings_convex_gdf = buildings_convex_gdf.join(results_df)

    polygon_results_gdf = (
        gpd.GeoDataFrame(pd.DataFrame(polygon_rows), geometry="geometry", crs=buildings_convex_gdf.crs)
        if polygon_rows
        else gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=buildings_convex_gdf.crs)
    )
    return buildings_convex_gdf, results_df, polygon_results_gdf


def _safe_flags(values) -> list[bool]:
    if isinstance(values, (list, tuple)):
        return [bool(v) for v in values]
    return []


def _safe_attempts(values) -> list[int]:
    if isinstance(values, (list, tuple)):
        return [int(v or 0) for v in values]
    return []


def summarize_building_decomposition(
    buildings_convex_gdf: pd.DataFrame,
    *,
    min_area_m2: float,
) -> dict[str, float | int]:
    flags_series = buildings_convex_gdf["polygon_fully_convex_flags"].apply(_safe_flags)
    attempt_series = buildings_convex_gdf["polygon_search_attempt_counts"].apply(_safe_attempts)
    n_buildings_total = len(buildings_convex_gdf)
    n_polygons_total = int(flags_series.apply(len).sum())
    n_polygons_success = int(flags_series.apply(sum).sum())
    total_search_attempts = int(attempt_series.apply(sum).sum())

    return {
        "n_buildings_total": n_buildings_total,
        "n_polygons_total": n_polygons_total,
        "n_polygons_success": n_polygons_success,
        "n_buildings_success": int(buildings_convex_gdf["building_fully_convex"].fillna(False).sum()),
        "n_buildings_failed": int(buildings_convex_gdf["building_decomposition_failed"].fillna(False).sum()),
        "n_small_zone": int(buildings_convex_gdf["building_has_small_zone"].fillna(False).sum()),
        "n_polygons_retried": int(attempt_series.apply(lambda xs: sum(v > 1 for v in xs)).sum()),
        "parts_per_building_mean": float(
            buildings_convex_gdf["convex_parts_counts"].apply(lambda xs: int(sum(xs)) if isinstance(xs, list) else 0).mean()
        ),
        "parts_per_building_median": float(
            buildings_convex_gdf["convex_parts_counts"].apply(lambda xs: int(sum(xs)) if isinstance(xs, list) else 0).median()
        ),
        "mean_attempts_per_polygon": float(total_search_attempts / max(n_polygons_total, 1)),
        "max_search_depth_used": int(buildings_convex_gdf["max_search_depth_used"].fillna(0).max()),
        "max_search_width_used": int(buildings_convex_gdf["max_search_width_used"].fillna(0).max()),
        "mean_time_seconds": float(buildings_convex_gdf["time_total_seconds"].mean()),
        "min_area_m2": float(min_area_m2),
    }


def classify_failure_reason(row: pd.Series) -> str:
    if bool(row.get("fully_convex_best", False)):
        return "fully_convex"
    if row.get("search_terminated_by") == "max_failed_states":
        return "search_capped_by_max_failed_states"
    n_depth = int(row.get("n_depth_limit_dead_ends", 0) or 0)
    n_no_reflex = int(row.get("n_no_reflex_dead_ends", 0) or 0)
    n_min_area = int(row.get("n_min_area_dead_ends", 0) or 0)
    active_modes = [
        name for name, count in [
            ("min_area_dead_ends", n_min_area),
            ("depth_limit_dead_ends", n_depth),
            ("no_reflex_dead_ends", n_no_reflex),
        ]
        if count > 0
    ]
    if len(active_modes) > 1:
        return "mixed_" + "_and_".join(active_modes)
    if n_min_area > 0:
        return "min_area_dead_ends"
    if n_depth > 0:
        return "depth_limit_dead_ends"
    if n_no_reflex > 0:
        return "no_reflex_dead_ends"
    return str(row.get("search_terminated_by") or "unknown")


def annotate_polygon_failure_reasons(
    polygon_results_gdf: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    polygon_results_gdf = polygon_results_gdf.copy()
    polygon_results_gdf["failure_reason"] = polygon_results_gdf.apply(classify_failure_reason, axis=1)
    failed_polygon_gdf = polygon_results_gdf.loc[~polygon_results_gdf["fully_convex_best"]].copy()
    small_zone_polygon_gdf = polygon_results_gdf.loc[polygon_results_gdf["has_small_zone"]].copy()
    return polygon_results_gdf, failed_polygon_gdf, small_zone_polygon_gdf


def parts_from_geom(geom: BaseGeometry | None) -> list[BaseGeometry]:
    if geom is None or getattr(geom, "is_empty", True):
        return []
    if hasattr(geom, "geoms"):
        return [part for part in geom.geoms if not getattr(part, "is_empty", True)]
    return [geom]


def select_complexity_ids(frame: pd.DataFrame, *, perimeter_flag: bool, n_examples: int) -> list[str]:
    subset = frame.loc[frame["perimeter"].fillna(False).astype(bool) == perimeter_flag].copy()
    if subset.empty:
        return []

    preferred = subset.loc[subset["building_fully_convex"].fillna(False).astype(bool)].copy()
    if preferred.empty:
        preferred = subset.copy()

    preferred = preferred.sort_values(["n_vertices", "sample_id"]).reset_index(drop=True)
    unique_vertices = np.sort(preferred["n_vertices"].dropna().astype(int).unique())
    if unique_vertices.size == 0:
        return []

    target_vertices = np.linspace(
        int(unique_vertices.min()),
        int(unique_vertices.max()),
        num=min(n_examples, unique_vertices.size),
    )

    chosen_ids: list[str] = []
    used_ids: set[str] = set()

    for target in target_vertices:
        nearest_idx = int(np.argmin(np.abs(unique_vertices - target)))
        vertex_count = int(unique_vertices[nearest_idx])
        pool = preferred.loc[(preferred["n_vertices"] == vertex_count) & (~preferred["sample_id"].isin(used_ids))]
        if pool.empty:
            remaining = preferred.loc[~preferred["sample_id"].isin(used_ids)].copy()
            if remaining.empty:
                continue
            remaining["vertex_distance"] = (remaining["n_vertices"].astype(int) - float(target)).abs()
            pool = remaining.sort_values(["vertex_distance", "n_vertices", "sample_id"])
        pick = pool.iloc[0]
        chosen_ids.append(str(pick["sample_id"]))
        used_ids.add(str(pick["sample_id"]))

    target_n = min(n_examples, len(preferred))
    if len(chosen_ids) < target_n:
        remaining = preferred.loc[~preferred["sample_id"].isin(used_ids)].copy()
        if not remaining.empty:
            remaining = remaining.sort_values(["n_vertices", "sample_id"])
            extra_positions = np.linspace(0, len(remaining) - 1, num=min(target_n - len(chosen_ids), len(remaining)), dtype=int)
            for pos in extra_positions:
                sid = str(remaining.iloc[int(pos)]["sample_id"])
                if sid in used_ids:
                    continue
                chosen_ids.append(sid)
                used_ids.add(sid)
                if len(chosen_ids) >= target_n:
                    break

    chosen = preferred.loc[preferred["sample_id"].isin(chosen_ids)].copy()
    chosen["sample_id"] = chosen["sample_id"].astype(str)
    order_lookup = {sid: idx for idx, sid in enumerate(chosen_ids)}
    chosen["order_idx"] = chosen["sample_id"].map(order_lookup)
    chosen = chosen.sort_values(["order_idx", "n_vertices", "sample_id"])
    return chosen["sample_id"].tolist()
