from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from convexdecomp.resplan.preprocess import iter_plan_room_polygons
from convexdecomp.resplan.upstream import plot_plan

MIN_EDGE_LEN_M = 1e-12


def _points_array(coords) -> np.ndarray:
    arr = np.asarray(coords, dtype=float)
    if len(arr) > 1 and np.allclose(arr[0], arr[-1]):
        arr = arr[:-1]
    return arr


def _ring_edges(points: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
    n = len(points)
    return [(points[i], points[(i + 1) % n]) for i in range(n)] if n > 1 else []


def _edge_params(p: np.ndarray, q: np.ndarray):
    v = q - p
    length = float(np.linalg.norm(v))
    if length <= MIN_EDGE_LEN_M:
        d = np.array([1.0, 0.0], dtype=float)
    else:
        d = v / length
    n = np.array([-d[1], d[0]], dtype=float)
    rho = float(np.dot(n, p))
    # Flip the normal so almost identical lines stay in one alpha-rho bucket.
    if rho < 0:
        n = -n
        rho = -rho
    alpha = math.atan2(n[1], n[0])
    if alpha < 0:
        alpha += math.pi
    return alpha, rho, length, n, d


def _angle_diff(a: float, b: float) -> float:
    delta = abs(a - b)
    return min(delta, math.pi - delta)


def _line_from_alpha_rho(alpha: float, rho: float):
    n = np.array([math.cos(alpha), math.sin(alpha)], dtype=float)
    return n, float(rho)


def _folded_edge_angle_deg(d: np.ndarray) -> float:
    theta = abs(math.degrees(math.atan2(float(d[1]), float(d[0]))))
    theta = theta % 180.0
    if theta > 90.0:
        theta = 180.0 - theta
    return theta


def _orthogonal_family_from_direction(d: np.ndarray, tol_deg: float) -> str | None:
    theta = _folded_edge_angle_deg(d)
    if theta <= tol_deg:
        return "horizontal"
    if abs(90.0 - theta) <= tol_deg:
        return "vertical"
    return None


def _canonical_alpha_for_family(family: str) -> float | None:
    if family == "vertical":
        return 0.0
    if family == "horizontal":
        return math.pi / 2.0
    return None


def _plan_is_orthogonal(edges: List[Dict[str, Any]], tol_deg: float, min_edge_len_m: float) -> bool:
    significant_edges = [edge for edge in edges if edge["length"] >= min_edge_len_m]
    if not significant_edges:
        return False
    return all(
        (edge.get("family") or _orthogonal_family_from_direction(edge["d"], tol_deg)) is not None
        for edge in significant_edges
    )


def _cluster_edges(
    edges: List[Dict[str, Any]],
    angle_tol_deg: float,
    offset_tol_m: float,
    *,
    orthogonal_only: bool,
) -> List[Dict[str, Any]]:
    angle_tol = math.radians(angle_tol_deg)
    clusters: List[Dict[str, Any]] = []
    for edge in edges:
        family = edge.get("family")
        alpha = edge["alpha"]
        if orthogonal_only and family is not None:
            canonical_alpha = _canonical_alpha_for_family(family)
            if canonical_alpha is not None:
                alpha = canonical_alpha
        rho = edge["rho"]
        weight = max(edge["length"], MIN_EDGE_LEN_M)
        placed = False
        for cluster in clusters:
            if orthogonal_only and family is not None and cluster.get("family") != family:
                continue
            if _angle_diff(alpha, cluster["alpha"]) <= angle_tol and abs(rho - cluster["rho"]) <= offset_tol_m:
                total_weight = cluster["weight"] + weight
                if orthogonal_only and family is not None:
                    cluster["alpha"] = _canonical_alpha_for_family(family)
                else:
                    cluster["alpha"] = (cluster["alpha"] * cluster["weight"] + alpha * weight) / total_weight
                cluster["rho"] = (cluster["rho"] * cluster["weight"] + rho * weight) / total_weight
                cluster["weight"] = total_weight
                cluster["members"].append(edge)
                placed = True
                break
        if not placed:
            cluster = {"alpha": alpha, "rho": rho, "weight": weight, "members": [edge]}
            if orthogonal_only and family is not None:
                cluster["family"] = family
            clusters.append(cluster)
    for cluster in clusters:
        cluster["n"], cluster["rho"] = _line_from_alpha_rho(cluster["alpha"], cluster["rho"])
    return clusters


def _merged_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1e-9:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _cluster_support(cluster: Dict[str, Any]) -> Dict[str, float]:
    family = cluster.get("family")
    intervals = []
    max_member = 0.0
    for member in cluster["members"]:
        p = member["p"]
        q = member["q"]
        max_member = max(max_member, float(member["length"]))
        if family == "vertical":
            intervals.append((float(p[1]), float(q[1])))
        elif family == "horizontal":
            intervals.append((float(p[0]), float(q[0])))
    merged = _merged_intervals(intervals)
    union = sum(end - start for start, end in merged)
    longest = max((end - start) for start, end in merged) if merged else 0.0
    return {"union": union, "longest": longest, "max_member": max_member}


def _filter_clusters(
    clusters: List[Dict[str, Any]],
    *,
    min_axis_support_union_m: float,
    min_axis_support_longest_m: float,
    min_axis_member_len_m: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_family = {"vertical": [], "horizontal": [], "other": []}
    for cluster in clusters:
        family = cluster.get("family")
        if family not in {"vertical", "horizontal"}:
            family = "other"
        cluster["support"] = _cluster_support(cluster)
        by_family[family].append(cluster)

    kept: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    for family, family_clusters in by_family.items():
        if family == "other":
            kept.extend(family_clusters)
            continue
        if not family_clusters:
            continue

        min_rho = min(cluster["rho"] for cluster in family_clusters)
        max_rho = max(cluster["rho"] for cluster in family_clusters)
        for cluster in family_clusters:
            is_boundary = abs(cluster["rho"] - min_rho) <= 1e-9 or abs(cluster["rho"] - max_rho) <= 1e-9
            support = cluster["support"]
            keep = is_boundary or (
                support["longest"] >= min_axis_support_longest_m
                and (
                    support["union"] >= min_axis_support_union_m
                    or support["max_member"] >= min_axis_member_len_m
                )
            )
            if keep:
                kept.append(cluster)
            else:
                removed.append(cluster)

    kept.sort(key=lambda cluster: (cluster.get("family", ""), cluster["rho"]))
    removed.sort(key=lambda cluster: (cluster.get("family", ""), cluster["rho"]))
    return kept, removed


def plan_room_bounds(plan: Dict[str, Any], room_keys: Iterable[str]) -> Tuple[float, float, float, float]:
    polys = [poly for poly in iter_plan_room_polygons(plan, room_keys)]
    if not polys:
        return 0.0, 0.0, 1.0, 1.0
    minx = min(poly.bounds[0] for poly in polys)
    miny = min(poly.bounds[1] for poly in polys)
    maxx = max(poly.bounds[2] for poly in polys)
    maxy = max(poly.bounds[3] for poly in polys)
    return minx, miny, maxx, maxy


def infer_plan_axis_clusters(
    plan: Dict[str, Any],
    room_keys: Iterable[str],
    *,
    line_angle_tol_deg: float = 2.0,
    line_offset_tol_m: float = 0.5,
    orthogonal_plan_tol_deg: float = 5.0,
    orthogonal_min_edge_len_m: float = 0.15,
    min_axis_support_union_m: float = 1.9,
    min_axis_support_longest_m: float = 0.75,
    min_axis_member_len_m: float = 1.5,
) -> Dict[str, Any]:
    edges = []
    for room_key, poly in iter_plan_room_polygons(plan, room_keys, with_labels=True):
        pts = _points_array(list(poly.exterior.coords))
        for p, q in _ring_edges(pts):
            alpha, rho, length, n, d = _edge_params(p, q)
            edges.append(
                {
                    "alpha": alpha,
                    "rho": rho,
                    "length": length,
                    "n": n,
                    "d": d,
                    "family": _orthogonal_family_from_direction(d, orthogonal_plan_tol_deg),
                    "p": p,
                    "q": q,
                    "room_key": room_key,
                }
            )

    orthogonal_only = _plan_is_orthogonal(edges, orthogonal_plan_tol_deg, orthogonal_min_edge_len_m)
    clusters_before = _cluster_edges(
        edges,
        line_angle_tol_deg,
        line_offset_tol_m,
        orthogonal_only=orthogonal_only,
    )
    clusters_after, clusters_removed = _filter_clusters(
        clusters_before,
        min_axis_support_union_m=min_axis_support_union_m,
        min_axis_support_longest_m=min_axis_support_longest_m,
        min_axis_member_len_m=min_axis_member_len_m,
    )
    return {
        "id": plan.get("id"),
        "orthogonal_only": orthogonal_only,
        "clusters_before": clusters_before,
        "clusters_after": clusters_after,
        "clusters_removed": clusters_removed,
    }


def infer_floorplan_axes(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    return [infer_plan_axis_clusters(plan, room_keys, **kwargs) for plan in plans]


def summarize_floorplan_axes(axis_data: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [item["id"] for item in axis_data],
            "orthogonal_only": [item["orthogonal_only"] for item in axis_data],
            "axis_count_before": [len(item["clusters_before"]) for item in axis_data],
            "axis_count_after": [len(item["clusters_after"]) for item in axis_data],
            "vertical_axes_after": [
                sum(1 for cluster in item["clusters_after"] if cluster.get("family") == "vertical")
                for item in axis_data
            ],
            "horizontal_axes_after": [
                sum(1 for cluster in item["clusters_after"] if cluster.get("family") == "horizontal")
                for item in axis_data
            ],
        }
    )


def draw_cluster_axes(ax, clusters: List[Dict[str, Any]], bounds, *, orthogonal_only: bool) -> None:
    minx, miny, maxx, maxy = bounds
    pad_x = max(0.2, 0.05 * (maxx - minx))
    pad_y = max(0.2, 0.05 * (maxy - miny))
    xmin, xmax = minx - pad_x, maxx + pad_x
    ymin, ymax = miny - pad_y, maxy + pad_y

    for cluster in clusters:
        family = cluster.get("family")
        if orthogonal_only and family == "vertical":
            x = cluster["rho"]
            ax.plot([x, x], [ymin, ymax], linestyle="--", color="#111111", linewidth=1.0, alpha=0.45, zorder=10)
        elif orthogonal_only and family == "horizontal":
            y = cluster["rho"]
            ax.plot([xmin, xmax], [y, y], linestyle="--", color="#111111", linewidth=1.0, alpha=0.45, zorder=10)
        else:
            n = cluster["n"]
            rho = cluster["rho"]
            if abs(n[1]) > abs(n[0]):
                xs = np.array([xmin, xmax], dtype=float)
                ys = (rho - n[0] * xs) / n[1]
            else:
                ys = np.array([ymin, ymax], dtype=float)
                xs = (rho - n[1] * ys) / n[0]
            ax.plot(xs, ys, linestyle="--", color="#111111", linewidth=1.0, alpha=0.45, zorder=10)


def plot_floorplans_with_axes(
    plans: Sequence[Dict[str, Any]],
    room_keys: Iterable[str],
    axes_by_id: Dict[int, Dict[str, Any]],
    *,
    ncols: int = 4,
    title_prefix: str = "Rooms + axes",
) -> None:
    if not plans:
        return
    n = len(plans)
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 4, nrows * 4))
    axes = np.atleast_1d(axes).flatten()
    for idx, plan in enumerate(plans):
        ax = axes[idx]
        plot_plan(plan, ax=ax, legend=(idx == n - 1), title=f"{title_prefix} #{idx}")
        axis_info = axes_by_id.get(plan.get("id"))
        if axis_info is not None:
            draw_cluster_axes(
                ax,
                axis_info["clusters_after"],
                plan_room_bounds(plan, room_keys),
                orthogonal_only=axis_info["orthogonal_only"],
            )
        ax.set_axis_off()
    for ax in axes[n:]:
        ax.set_visible(False)
    plt.tight_layout()
    plt.show()
