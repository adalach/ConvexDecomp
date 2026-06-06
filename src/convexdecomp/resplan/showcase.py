from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Sequence

from IPython.display import display
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry.base import BaseGeometry

from convexdecomp.resplan.axes import draw_cluster_axes, plan_room_bounds
from convexdecomp.resplan.upstream import plot_plan
from convexdecomp.resplan.axis_grid import concave_room_count_for_plan, plot_axis_grid_rooms
from convexdecomp.resplan.preprocess import plan_room_polygons, reduce_to_room_layers


def plan_lookup(plans: Sequence[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    return {plan.get("id"): plan for plan in plans}


def _plan_has_plottable_geometry(value: Any) -> bool:
    if isinstance(value, BaseGeometry):
        return not value.is_empty
    if isinstance(value, (list, tuple)):
        return any(_plan_has_plottable_geometry(item) for item in value)
    geoms = getattr(value, "geoms", None)
    if geoms is not None:
        return any(_plan_has_plottable_geometry(item) for item in geoms)
    return False


def _is_plottable_plan(plan: Dict[str, Any]) -> bool:
    for key, value in plan.items():
        if key in {"id", "graph", "neighbor"}:
            continue
        if _plan_has_plottable_geometry(value):
            return True
    return False


def plot_floorplans_grid(
    floorplans: Sequence[Dict[str, Any]],
    *,
    ncols: int = 5,
    nrows: int = 2,
    title_prefix: str = "Plan",
) -> None:
    max_plots = ncols * nrows
    plottable_floorplans = [plan for plan in floorplans if _is_plottable_plan(plan)]
    if not plottable_floorplans:
        print(f"No plottable floorplans available for '{title_prefix}'.")
        return
    n = min(len(plottable_floorplans), max_plots)

    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 4))
    axes = np.atleast_1d(axes).flatten()

    shown = 0
    for plan in plottable_floorplans:
        if shown >= n:
            break
        ax = axes[shown]
        try:
            plot_plan(plan, ax=ax, legend=True, title=f"{title_prefix} #{shown}")
        except ValueError:
            ax.set_visible(False)
            continue
        ax.set_axis_off()
        shown += 1

    if shown == 0:
        plt.close(fig)
        print(f"No plottable floorplans available for '{title_prefix}'.")
        return

    for ax in axes[shown:]:
        ax.set_visible(False)

    plt.tight_layout()
    plt.show()


def select_dropped_preselection_examples(
    records: Sequence[Dict[str, Any]],
    *,
    max_examples: int,
) -> list[Dict[str, Any]]:
    buckets = {0: [], 1: [], 2: []}
    for record in records:
        bucket = min(int(record["concave_count"]), 2)
        buckets[bucket].append(record)

    selected: list[Dict[str, Any]] = []
    while len(selected) < max_examples:
        added = False
        for bucket in (2, 1, 0):
            if buckets[bucket]:
                selected.append(buckets[bucket].pop(0))
                added = True
                if len(selected) >= max_examples:
                    break
        if not added:
            break
    return selected


def plot_dropped_preselection_examples(
    records: Sequence[Dict[str, Any]],
    *,
    max_examples: int,
) -> None:
    examples = select_dropped_preselection_examples(records, max_examples=max_examples)
    if not examples:
        return

    n = len(examples)
    ncols = min(3, n)
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4 * ncols, 4 * nrows))
    axes = np.atleast_1d(axes).flatten()

    shown = 0
    for record in examples:
        if shown >= len(axes):
            break
        ax = axes[shown]
        try:
            plot_plan(
                record["plan"],
                ax=ax,
                legend=(shown == len(examples) - 1),
                title=f"id={record['id']} concave={record['concave_count']}",
            )
        except ValueError:
            ax.set_visible(False)
            continue
        ax.set_axis_off()
        shown += 1

    if shown == 0:
        plt.close(fig)
        print("No plottable dropped-preselection examples are available.")
        return

    for ax in axes[shown:]:
        ax.set_visible(False)

    fig.suptitle("Dropped before rescaling: plans with too few concave rooms to be interesting for this task")
    plt.tight_layout()
    plt.show()


def room_vertex_count_for_plan(plan: Dict[str, Any], room_keys: Iterable[str]) -> int:
    return sum(len(list(poly.exterior.coords)) for poly in plan_room_polygons(plan, room_keys))


def room_area_for_plan(plan: Dict[str, Any], room_keys: Iterable[str]) -> float:
    return float(sum(poly.area for poly in plan_room_polygons(plan, room_keys)))


def _plot_plan_or_axes(
    ax,
    stage_name: str,
    plan: Dict[str, Any],
    room_keys: Iterable[str],
    axis_info=None,
    axis_grid_metrics=None,
    *,
    show_legend: bool = False,
) -> None:
    if axis_info is not None and axis_grid_metrics is not None:
        plot_axis_grid_rooms(ax, plan, axis_info, axis_grid_metrics, room_keys, legend=show_legend, title=stage_name)
    else:
        plot_plan(plan, ax=ax, legend=show_legend, title=stage_name)
        if axis_info is not None:
            draw_cluster_axes(ax, axis_info["clusters_after"], plan_room_bounds(plan, room_keys), orthogonal_only=axis_info["orthogonal_only"])
        ax.set_axis_off()


def select_showcase_ids(
    final_selected_plans: Sequence[Dict[str, Any]],
    preferred_ids: Sequence[Any],
    *,
    count: int,
) -> list[Any]:
    final_lookup = plan_lookup(final_selected_plans)
    showcase_ids = [pid for pid in preferred_ids if pid in final_lookup]
    if len(showcase_ids) < count:
        fallback_ids = [pid for pid in final_lookup if pid not in showcase_ids]
        showcase_ids.extend(fallback_ids[: max(0, count - len(showcase_ids))])
    return showcase_ids[:count]


def build_showcase_summary(
    step_entries,
    room_keys: Iterable[str],
) -> pd.DataFrame:
    rows = []
    for stage_name, plan, stage_axis_info, _stage_grid_metrics in step_entries:
        if plan is None:
            continue
        row = {
            "step": stage_name,
            "room_polygons": int(len(plan_room_polygons(plan, room_keys))),
            "vertices": int(room_vertex_count_for_plan(plan, room_keys)),
            "total_room_area_m2": float(room_area_for_plan(plan, room_keys)),
            "concave_rooms": int(concave_room_count_for_plan(plan, room_keys)),
            "vertical_axes": None,
            "horizontal_axes": None,
        }
        if stage_name == "Architectural axes" and stage_axis_info is not None:
            row["vertical_axes"] = int(sum(1 for c in stage_axis_info["clusters_after"] if c.get("family") == "vertical"))
            row["horizontal_axes"] = int(sum(1 for c in stage_axis_info["clusters_after"] if c.get("family") == "horizontal"))
        rows.append(row)
    return pd.DataFrame(rows)


def render_showcase_panel(
    *,
    showcase_ids: Sequence[Any],
    room_keys: Iterable[str],
    floorplans_all_raw: Sequence[Dict[str, Any]],
    floorplans_all: Sequence[Dict[str, Any]],
    rescaled_floorplans: Sequence[Dict[str, Any]],
    room_offset_floorplans: Sequence[Dict[str, Any]],
    floorplans_rooms: Sequence[Dict[str, Any]],
    floorplans_rooms_split: Sequence[Dict[str, Any]],
    axis_grid_showcase_before_gap_cleanup: Dict[Any, Dict[str, Any]],
    floorplans_rooms_axis_grid_all: Sequence[Dict[str, Any]],
    floorplans_rooms_axis_grid: Sequence[Dict[str, Any]],
    floorplan_axes_by_id: Dict[Any, Dict[str, Any]],
    axis_grid_metrics_by_id: Dict[Any, Dict[str, Any]],
) -> None:
    if not showcase_ids:
        print("[Error] No final selected showcase plans are available.")
        return

    raw_lookup = plan_lookup(floorplans_all_raw)
    raw_clean_lookup = plan_lookup(floorplans_all)
    rescaled_lookup = plan_lookup(rescaled_floorplans)
    wall_offset_lookup = plan_lookup(room_offset_floorplans)
    rooms_lookup = plan_lookup(floorplans_rooms)
    split_lookup = plan_lookup(floorplans_rooms_split)
    pre_gap_lookup = dict(axis_grid_showcase_before_gap_cleanup)
    pre_min_area_lookup = plan_lookup(floorplans_rooms_axis_grid_all)
    final_selected_lookup = plan_lookup(floorplans_rooms_axis_grid)

    for target_id in showcase_ids:
        rescaled_plan = rescaled_lookup.get(target_id)
        room_offset_plan = wall_offset_lookup.get(target_id)
        rooms_before = reduce_to_room_layers([rescaled_plan], room_keys, keep_keys=("id",))[0] if rescaled_plan is not None else None

        step_entries = [
            ("Raw", raw_lookup.get(target_id), None, None),
            ("Raw vertex-cleaned", raw_clean_lookup.get(target_id), None, None),
            ("Rescaled", rescaled_plan, None, None),
            ("Rooms-only before wall-offset", rooms_before, None, None),
            ("Wall-offset", room_offset_plan, None, None),
            ("Rooms-only", rooms_lookup.get(target_id), None, None),
        ]

        axis_info = floorplan_axes_by_id.get(target_id)
        step_entries.extend(
            [
                ("Architectural axes", split_lookup.get(target_id), axis_info, None),
                ("Axis-grid rooms", pre_gap_lookup.get(target_id), axis_info, axis_grid_metrics_by_id.get(target_id)),
                ("Gap-closed axis-grid rooms", pre_min_area_lookup.get(target_id), None, None),
                ("Final selected rooms", final_selected_lookup.get(target_id), None, None),
            ]
        )

        display(build_showcase_summary(step_entries, room_keys))

        ncols = 3
        nrows = int(math.ceil(len(step_entries) / ncols))
        fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 4 * nrows))
        axes = np.atleast_1d(axes).flatten()
        for idx, (stage_name, plan, stage_axis_info, stage_grid_metrics) in enumerate(step_entries):
            ax = axes[idx]
            if plan is None:
                ax.set_visible(False)
                continue
            _plot_plan_or_axes(
                ax,
                stage_name,
                plan,
                room_keys,
                axis_info=stage_axis_info,
                axis_grid_metrics=stage_grid_metrics,
                show_legend=(idx == len(step_entries) - 1),
            )
        for ax in axes[len(step_entries):]:
            ax.set_visible(False)
        plt.tight_layout()
        plt.show()
