from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from convexdecomp.osm.decompose import extract_polygons
from convexdecomp.core.convexity import convexity_mask

__all__ = [
    "build_osm_input_polygon_df",
    "build_resplan_input_polygon_df",
    "plot_input_dataset_diagnostics",
    "plot_osm_input_dataset_diagnostics",
    "plot_resplan_input_dataset_diagnostics",
    "print_input_dataset_summary",
    "summarize_input_dataset",
]


def _polygon_vertex_count(poly: Polygon) -> int:
    if not isinstance(poly, Polygon) or poly.is_empty:
        return 0
    return max(len(poly.exterior.coords) - 1, 0)


def _safe_hist(ax: Any, values: Sequence[float], *, bins: int, color: str, title: str, xlabel: str) -> None:
    clean = np.asarray([float(v) for v in values if np.isfinite(v)], dtype=float)
    if clean.size == 0:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
        ax.set_axis_off()
        return

    ax.hist(clean, bins=min(bins, max(5, int(np.sqrt(clean.size)) + 4)), color=color, edgecolor="black", alpha=0.85)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Count")


def summarize_input_dataset(
    polygon_df: pd.DataFrame,
    *,
    n_samples_total: int,
    min_area_m2: float,
) -> dict[str, float | int]:
    n_polygons_total = int(len(polygon_df))
    n_concave_polygons = int((~polygon_df["is_convex"]).sum()) if n_polygons_total else 0
    pct_concave = 100.0 * n_concave_polygons / max(n_polygons_total, 1)

    if n_polygons_total:
        min_area_by_sample = polygon_df.groupby("sample_id")["polygon_area_m2"].min()
        n_small_samples = int((min_area_by_sample < min_area_m2).sum())
    else:
        n_small_samples = 0
    pct_small_samples = 100.0 * n_small_samples / max(n_samples_total, 1)

    summary = {
        "n_samples_total": n_samples_total,
        "n_polygons_total": n_polygons_total,
        "n_concave_polygons": n_concave_polygons,
        "pct_concave_polygons": pct_concave,
        "n_small_samples": n_small_samples,
        "pct_small_samples": pct_small_samples,
    }
    return summary


def print_input_dataset_summary(
    summary: dict[str, float | int],
    *,
    min_area_m2: float,
    dataset_label: str,
) -> None:
    n_samples_total = int(summary["n_samples_total"])
    n_polygons_total = int(summary["n_polygons_total"])
    n_concave_polygons = int(summary["n_concave_polygons"])
    pct_concave = float(summary["pct_concave_polygons"])
    n_small_samples = int(summary["n_small_samples"])
    pct_small_samples = float(summary["pct_small_samples"])
    print(f"=== {dataset_label} input diagnostics ===")
    print(f"Samples in working set: {n_samples_total}")
    print(f"Polygons across all samples: {n_polygons_total}")
    print(
        f"Concave polygons before convex decomposition: "
        f"{n_concave_polygons} / {n_polygons_total} ({pct_concave:.1f}%)"
    )
    print(
        f"Samples with at least one polygon below {min_area_m2:.1f} m^2: "
        f"{n_small_samples} / {n_samples_total} ({pct_small_samples:.1f}%)"
    )


def plot_input_dataset_diagnostics(
    polygon_df: pd.DataFrame,
    *,
    min_area_m2: float,
    dataset_label: str,
    units_per_sample: np.ndarray | None = None,
    units_per_sample_title: str = "Polygons per sample",
    units_per_sample_xlabel: str = "Polygon count",
) -> tuple[plt.Figure, np.ndarray]:
    fig, axes = plt.subplots(1, 4, figsize=(18, 4.5))

    polygon_areas = polygon_df["polygon_area_m2"].to_numpy(dtype=float) if not polygon_df.empty else np.array([])
    polygon_vertices = polygon_df["n_vertices"].to_numpy(dtype=float) if not polygon_df.empty else np.array([])
    if units_per_sample is None:
        units_per_sample = polygon_df.groupby("sample_id").size().to_numpy(dtype=float) if not polygon_df.empty else np.array([])
    min_area_per_sample = (
        polygon_df.groupby("sample_id")["polygon_area_m2"].min().to_numpy(dtype=float)
        if not polygon_df.empty
        else np.array([])
    )

    _safe_hist(
        axes[0],
        polygon_areas,
        bins=40,
        color="#4c78a8",
        title="Polygon area distribution",
        xlabel="Area [m^2]",
    )
    axes[0].axvline(min_area_m2, color="#d62728", linestyle="--", linewidth=1.5)

    _safe_hist(
        axes[1],
        polygon_vertices,
        bins=24,
        color="#f58518",
        title="Polygon vertex counts",
        xlabel="Exterior vertices",
    )

    _safe_hist(
        axes[2],
        units_per_sample,
        bins=24,
        color="#54a24b",
        title=units_per_sample_title,
        xlabel=units_per_sample_xlabel,
    )

    _safe_hist(
        axes[3],
        min_area_per_sample,
        bins=32,
        color="#b279a2",
        title="Minimum polygon area per sample",
        xlabel="Min polygon area [m^2]",
    )
    axes[3].axvline(min_area_m2, color="#d62728", linestyle="--", linewidth=1.5)

    fig.suptitle(f"{dataset_label}: pre-decomposition geometry diagnostics", y=1.02, fontsize=12)
    fig.tight_layout()
    return fig, axes


def _build_polygon_df_from_records(
    records: Iterable[dict[str, Any]],
) -> pd.DataFrame:
    polygon_df = pd.DataFrame.from_records(records)
    if polygon_df.empty:
        return pd.DataFrame(columns=["sample_id", "polygon_idx", "polygon_area_m2", "n_vertices", "is_convex"])
    polygon_df["is_convex"] = polygon_df["is_convex"].astype(bool)
    return polygon_df.sort_values(["sample_id", "polygon_idx"]).reset_index(drop=True)


def build_resplan_input_polygon_df(
    floorplans: Sequence[dict[str, Any]],
    *,
    room_keys: Iterable[str],
) -> pd.DataFrame:
    room_keys = tuple(room_keys)
    polygon_records: list[dict[str, Any]] = []

    for plan_idx, plan in enumerate(floorplans):
        sample_id = plan.get("id", f"plan_{plan_idx:05d}")
        polygons = [
            poly
            for room_key in room_keys
            for poly in plan.get(room_key, [])
            if isinstance(poly, Polygon) and not poly.is_empty
        ]
        convex_flags = convexity_mask(polygons)

        for polygon_idx, (poly, is_convex) in enumerate(zip(polygons, convex_flags), start=1):
            polygon_records.append(
                {
                    "sample_id": sample_id,
                    "polygon_idx": polygon_idx,
                    "polygon_area_m2": float(poly.area),
                    "n_vertices": _polygon_vertex_count(poly),
                    "is_convex": bool(is_convex),
                }
            )

    return _build_polygon_df_from_records(polygon_records)


def plot_resplan_input_dataset_diagnostics(
    floorplans: Sequence[dict[str, Any]],
    *,
    room_keys: Iterable[str],
    min_area_m2: float = 2.0,
    dataset_label: str = "ResPlan",
) -> tuple[dict[str, float | int], pd.DataFrame]:
    polygon_df = build_resplan_input_polygon_df(floorplans, room_keys=room_keys)
    summary = summarize_input_dataset(
        polygon_df,
        n_samples_total=len(floorplans),
        min_area_m2=min_area_m2,
    )
    print_input_dataset_summary(summary, min_area_m2=min_area_m2, dataset_label=dataset_label)
    plot_input_dataset_diagnostics(polygon_df, min_area_m2=min_area_m2, dataset_label=dataset_label)
    return summary, polygon_df


def build_osm_input_polygon_df(
    buildings_gdf: pd.DataFrame,
    *,
    polygons_col: str = "decomp_polygons",
    sample_id_col: str = "sample_id",
) -> tuple[pd.DataFrame, np.ndarray]:
    polygon_records: list[dict[str, Any]] = []
    polygons_plus_holes_per_sample: list[int] = []

    for row_idx, row in buildings_gdf.iterrows():
        sample_id = row.get(sample_id_col, f"sample_{row_idx:05d}")
        polygons = [
            poly
            for poly in extract_polygons(row.get(polygons_col))
            if isinstance(poly, Polygon) and not poly.is_empty
        ]
        polygons_plus_holes_per_sample.append(
            int(sum(1 + len(poly.interiors) for poly in polygons))
        )
        convex_flags = convexity_mask(polygons)

        for polygon_idx, (poly, is_convex) in enumerate(zip(polygons, convex_flags), start=1):
            polygon_records.append(
                {
                    "sample_id": sample_id,
                    "polygon_idx": polygon_idx,
                    "polygon_area_m2": float(poly.area),
                    "n_vertices": _polygon_vertex_count(poly),
                    "is_convex": bool(is_convex),
                }
            )

    polygon_df = _build_polygon_df_from_records(polygon_records)
    return polygon_df, np.asarray(polygons_plus_holes_per_sample, dtype=float)


def plot_osm_input_dataset_diagnostics(
    buildings_gdf: pd.DataFrame,
    *,
    polygons_col: str = "decomp_polygons",
    sample_id_col: str = "sample_id",
    min_area_m2: float = 2.0,
    dataset_label: str = "OSM",
) -> tuple[dict[str, float | int], pd.DataFrame]:
    polygon_df, polygons_plus_holes_per_sample = build_osm_input_polygon_df(
        buildings_gdf,
        polygons_col=polygons_col,
        sample_id_col=sample_id_col,
    )
    summary = summarize_input_dataset(
        polygon_df,
        n_samples_total=len(buildings_gdf),
        min_area_m2=min_area_m2,
    )
    print_input_dataset_summary(summary, min_area_m2=min_area_m2, dataset_label=dataset_label)
    plot_input_dataset_diagnostics(
        polygon_df,
        min_area_m2=min_area_m2,
        dataset_label=dataset_label,
        units_per_sample=polygons_plus_holes_per_sample,
        units_per_sample_title="Polygons + holes per sample",
        units_per_sample_xlabel="Polygon + hole count",
    )
    return summary, polygon_df
