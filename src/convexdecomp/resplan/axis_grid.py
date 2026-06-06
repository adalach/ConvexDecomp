from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from convexdecomp.core.convexity import concave_polygon_count
from convexdecomp.resplan.axes import draw_cluster_axes, plan_room_bounds
from convexdecomp.resplan.preprocess import explode_polygon_parts, plan_room_polygons
from convexdecomp.resplan.upstream import get_category_colors, plot_plan

AXIS_GRID_CENTER_MARKER_SIZE = 52
AXIS_GRID_GRIDLINE_COLOR = "#6b6b6b"
AXIS_GRID_GRIDLINE_ALPHA = 0.22
AXIS_GRID_CENTER_ALPHA = 0.96


def room_instances(plan: Dict[str, Any], room_keys: Iterable[str]) -> List[Dict[str, Any]]:
    instances = []
    for room_key in room_keys:
        for index, poly in enumerate(explode_polygon_parts(plan.get(room_key))):
            if isinstance(poly, Polygon) and not poly.is_empty:
                instances.append(
                    {
                        "instance_id": f"{room_key}__{index}",
                        "label": room_key,
                        "geometry": poly,
                    }
                )
    return instances


def safe_union_polygons(polys) -> BaseGeometry:
    if not polys:
        return Polygon()
    geom = unary_union(polys)
    if isinstance(geom, BaseGeometry) and (not geom.is_empty) and (not geom.is_valid):
        geom = geom.buffer(0)
    return geom


def unique_sorted(values: List[float], tol: float = 1e-6) -> List[float]:
    ordered = sorted(values)
    if not ordered:
        return []
    out = [ordered[0]]
    for value in ordered[1:]:
        if abs(value - out[-1]) > tol:
            out.append(value)
    return out


def axis_grid_coordinates(axis_info: Dict[str, Any]) -> Tuple[List[float], List[float]]:
    xs = unique_sorted([cluster["rho"] for cluster in axis_info["clusters_after"] if cluster.get("family") == "vertical"])
    ys = unique_sorted([cluster["rho"] for cluster in axis_info["clusters_after"] if cluster.get("family") == "horizontal"])
    return xs, ys


def assign_cell_to_room_instance(cell, center: Point, room_geoms: Dict[str, Polygon]) -> Tuple[str | None, str]:
    midpoint_hits = [room_id for room_id, geom in room_geoms.items() if geom.covers(center)]
    if len(midpoint_hits) == 1:
        return midpoint_hits[0], "midpoint"

    candidate_ids = midpoint_hits if midpoint_hits else list(room_geoms.keys())
    best_room_id = None
    best_overlap = 0.0
    for room_id in candidate_ids:
        overlap = room_geoms[room_id].intersection(cell).area
        if overlap > best_overlap + 1e-9:
            best_overlap = overlap
            best_room_id = room_id
    if best_room_id is not None and best_overlap > 1e-9:
        return best_room_id, "overlap"
    return None, "unassigned"


def _darken_room_color(room_key: str, factor: float = 0.68):
    base = get_category_colors().get(room_key, "#666666")
    rgb = np.array(mcolors.to_rgb(base), dtype=float)
    if rgb.mean() > 0.92:
        return "#555555"
    return tuple(np.clip(rgb * factor, 0.0, 1.0))


def draw_axis_grid_lines(ax, xs: List[float], ys: List[float], bounds: Tuple[float, float, float, float]) -> None:
    minx, miny, maxx, maxy = bounds
    for x in xs:
        ax.plot([x, x], [miny, maxy], linestyle="--", color=AXIS_GRID_GRIDLINE_COLOR, linewidth=1.0, alpha=AXIS_GRID_GRIDLINE_ALPHA, zorder=6)
    for y in ys:
        ax.plot([minx, maxx], [y, y], linestyle="--", color=AXIS_GRID_GRIDLINE_COLOR, linewidth=1.0, alpha=AXIS_GRID_GRIDLINE_ALPHA, zorder=6)


def plot_axis_grid_rooms(
    ax,
    plan: Dict[str, Any],
    axis_info: Dict[str, Any],
    metrics: Dict[str, Any],
    room_keys: Iterable[str],
    *,
    legend: bool = False,
    title: str | None = None,
) -> None:
    plot_plan(plan, ax=ax, legend=legend, title=title, tight=False)
    xs = metrics.get("grid_xs") or axis_grid_coordinates(axis_info)[0]
    ys = metrics.get("grid_ys") or axis_grid_coordinates(axis_info)[1]
    draw_axis_grid_lines(ax, xs, ys, plan_room_bounds(plan, room_keys))
    for record in metrics.get("cell_records", []):
        center = record["center"]
        ax.scatter(
            [center.x],
            [center.y],
            s=AXIS_GRID_CENTER_MARKER_SIZE,
            color=[_darken_room_color(record["label"])],
            edgecolors="none",
            alpha=AXIS_GRID_CENTER_ALPHA,
            zorder=12,
        )
    ax.set_axis_off()


def reconstruct_plan_on_axis_grid(
    plan: Dict[str, Any],
    axis_info: Dict[str, Any],
    room_keys: Iterable[str],
):
    instances = room_instances(plan, room_keys)
    instance_meta = {item["instance_id"]: item for item in instances}
    room_geoms = {item["instance_id"]: item["geometry"] for item in instances}
    footprint = safe_union_polygons(list(room_geoms.values()))
    xs, ys = axis_grid_coordinates(axis_info)

    cell_assignments = defaultdict(list)
    cell_records = []
    assignment_counts = {"midpoint": 0, "overlap": 0, "unassigned": 0, "outside": 0}

    for x0, x1 in zip(xs[:-1], xs[1:]):
        for y0, y1 in zip(ys[:-1], ys[1:]):
            if x1 - x0 <= 1e-9 or y1 - y0 <= 1e-9:
                continue
            cell = box(x0, y0, x1, y1)
            center = Point((x0 + x1) / 2.0, (y0 + y1) / 2.0)
            if not footprint.covers(center):
                assignment_counts["outside"] += 1
                continue
            room_id, mode = assign_cell_to_room_instance(cell, center, room_geoms)
            assignment_counts[mode] += 1
            if room_id is not None:
                cell_assignments[room_id].append(cell)
                cell_records.append(
                    {
                        "instance_id": room_id,
                        "label": instance_meta[room_id]["label"],
                        "cell": cell,
                        "center": center,
                    }
                )

    reconstructed = {"id": plan.get("id")}
    if "neighbor" in plan:
        reconstructed["neighbor"] = plan["neighbor"]

    reconstructed_by_label = defaultdict(list)
    for item in instances:
        geom = safe_union_polygons(cell_assignments.get(item["instance_id"], []))
        polys = explode_polygon_parts(geom)
        if polys:
            reconstructed_by_label[item["label"]].extend(polys)
    for room_key, polys in reconstructed_by_label.items():
        reconstructed[room_key] = polys
    original_by_label = defaultdict(list)
    for item in instances:
        original_by_label[item["label"]].append(item["geometry"])

    total_abs_area_error = 0.0
    total_label_symdiff = 0.0
    room_metrics = []
    for room_key in sorted(original_by_label):
        original_geom = safe_union_polygons(original_by_label[room_key])
        reconstructed_geom = safe_union_polygons(reconstructed_by_label.get(room_key, []))
        original_area = float(original_geom.area)
        reconstructed_area = float(reconstructed_geom.area)
        area_diff = reconstructed_area - original_area
        symdiff_area = float(original_geom.symmetric_difference(reconstructed_geom).area)
        total_abs_area_error += abs(area_diff)
        total_label_symdiff += symdiff_area
        room_metrics.append(
            {
                "label": room_key,
                "original_area_m2": original_area,
                "reconstructed_area_m2": reconstructed_area,
                "area_diff_m2": area_diff,
                "symdiff_area_m2": symdiff_area,
            }
        )

    return reconstructed, {
        "assignment_counts": assignment_counts,
        "total_abs_area_error_m2": total_abs_area_error,
        "total_label_symdiff_m2": total_label_symdiff,
        "room_metrics": room_metrics,
        "x_axis_count": len(xs),
        "y_axis_count": len(ys),
        "grid_xs": xs,
        "grid_ys": ys,
        "cell_records": cell_records,
    }


def reconstruct_floorplans_on_axis_grid(
    plans: Sequence[Dict[str, Any]],
    axes_by_id: Dict[int, Dict[str, Any]],
    room_keys: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], pd.DataFrame]:
    reconstructed_plans = []
    metrics_by_id = {}
    summary_rows = []
    for plan in plans:
        axis_info = axes_by_id[plan["id"]]
        reconstructed_plan, metrics = reconstruct_plan_on_axis_grid(plan, axis_info, room_keys)
        reconstructed_plans.append(reconstructed_plan)
        metrics_by_id[plan["id"]] = metrics
        summary_rows.append(
            {
                "id": plan["id"],
                "x_axis_count": metrics["x_axis_count"],
                "y_axis_count": metrics["y_axis_count"],
                "midpoint_cells": metrics["assignment_counts"]["midpoint"],
                "overlap_cells": metrics["assignment_counts"]["overlap"],
                "total_abs_area_error_m2": metrics["total_abs_area_error_m2"],
                "total_label_symdiff_m2": metrics["total_label_symdiff_m2"],
            }
        )
    return reconstructed_plans, metrics_by_id, pd.DataFrame(summary_rows)


def plot_axis_grid_reconstruction_pairs(
    original_plans: Sequence[Dict[str, Any]],
    reconstructed_plans: Sequence[Dict[str, Any]],
    axes_by_id: Dict[int, Dict[str, Any]],
    metrics_by_id: Dict[int, Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    preview_count: int = 8,
) -> None:
    n = min(preview_count, len(original_plans))
    if n == 0:
        return
    nrows = int(math.ceil(n / 2))
    fig, axes = plt.subplots(nrows, 4, figsize=(18, 4.5 * nrows))
    axes = np.atleast_1d(axes).flatten()

    for idx in range(n):
        original_plan = original_plans[idx]
        reconstructed_plan = reconstructed_plans[idx]
        axis_info = axes_by_id[original_plan["id"]]
        metrics = metrics_by_id[original_plan["id"]]

        ax_left = axes[idx * 2]
        plot_plan(original_plan, ax=ax_left, legend=False, title=f"#{idx} original + axes")
        draw_cluster_axes(ax_left, axis_info["clusters_after"], plan_room_bounds(original_plan, room_keys), orthogonal_only=axis_info["orthogonal_only"])
        ax_left.set_axis_off()

        ax_right = axes[idx * 2 + 1]
        plot_axis_grid_rooms(
            ax_right,
            reconstructed_plan,
            axis_info,
            metrics,
            room_keys,
            legend=(idx == n - 1),
            title=(
                f"#{idx} axis-grid rooms\n"
                f"|ΔA|={metrics['total_abs_area_error_m2']:.2f}  Σsym={metrics['total_label_symdiff_m2']:.2f}"
            ),
        )

    for ax in axes[n * 2:]:
        ax.set_visible(False)
    plt.tight_layout()
    plt.show()


def plot_gap_cleanup_examples(examples, *, max_examples: int) -> None:
    n = min(max_examples, len(examples))
    if n == 0:
        return
    fig, axes = plt.subplots(n, 2, figsize=(10, 4 * n))
    axes = np.atleast_2d(axes)
    for row, example in enumerate(examples[:n]):
        before_ax, after_ax = axes[row]
        move_count = len(example["moves"])
        total_strip_area = sum(move["strip_area_m2"] for move in example["moves"])
        plot_plan(example["before"], ax=before_ax, legend=False, title=f"id={example['id']} before")
        before_ax.set_axis_off()
        plot_plan(
            example["after"],
            ax=after_ax,
            legend=(row == n - 1),
            title=f"id={example['id']} after\nclosed {move_count} gap(s), area={total_strip_area:.2f} m^2",
        )
        after_ax.set_axis_off()
    plt.tight_layout()
    plt.show()


def plan_passes_min_area(plan: Dict[str, Any], room_keys: Iterable[str], min_area_m2: float) -> bool:
    polys = plan_room_polygons(plan, room_keys)
    if not polys:
        return False
    areas = np.fromiter((poly.area for poly in polys), dtype=float)
    return bool((areas >= min_area_m2).all())


def filter_plans_by_min_room_area(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    min_area_m2: float,
) -> tuple[list[dict[str, Any]], list[Any]]:
    kept = []
    dropped_ids = []
    for plan in plans:
        if plan_passes_min_area(plan, room_keys, min_area_m2):
            kept.append(plan)
        else:
            dropped_ids.append(plan["id"])
    return kept, dropped_ids


def concave_room_count_for_plan(plan: Dict[str, Any], room_keys: Iterable[str]) -> int:
    return concave_polygon_count(plan_room_polygons(plan, room_keys))


def select_concave_subset(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    *,
    min_concave_rooms: int,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    counts = np.fromiter((concave_room_count_for_plan(plan, room_keys) for plan in plans), dtype=int)
    selected = [
        plan
        for plan, concave_count in zip(plans, counts)
        if concave_count >= min_concave_rooms
    ]
    return selected, counts
