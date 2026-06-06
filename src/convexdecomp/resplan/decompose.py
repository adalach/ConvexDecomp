from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import split

from convexdecomp.core.search import (
    AdaptiveSearchConfig,
    DecompositionSearchConfig,
    ExpandedState,
    StateExpansionResult,
    run_polygon_state_search,
    score_polygon_partition,
)
from convexdecomp.core.convexity import (
    convexity_mask,
    is_convex_polygon,
    polygon_turn_crosses,
)
from convexdecomp.resplan.preprocess import simplify_polygon_vertices

DEFAULT_ROOM_KEYS: Tuple[str, ...] = (
    "bathroom",
    "bedroom",
    "stair",
    "kitchen",
    "storage",
    "living",
)

FAILURE_REASON_LABELS: Dict[str, str] = {
    "min_area_dead_end": "all feasible branches create a sub-room below 2 m²",
    "depth_limit_dead_end": "the search hits the depth limit before reaching a fully convex split",
    "no_axis_aligned_split": "the reflex shape does not admit a useful horizontal or vertical split",
    "mixed_dead_end": "multiple dead-end mechanisms appear in the same search tree",
    "invalid_input": "the room polygon is invalid or empty for decomposition",
}

__all__ = [
    "DEFAULT_ROOM_KEYS",
    "FAILURE_REASON_LABELS",
    "HVConvexDecompositionConfig",
    "all_rooms_above_area",
    "all_rooms_convex",
    "decompose_polygon_hv_with_diagnostics",
    "plan_has_rooms",
    "plan_room_polygons",
    "pick_best_convex_variant_per_room",
]


@dataclass(frozen=True)
class HVConvexDecompositionConfig:
    min_area_m2: float = 2.0
    max_depth: int = 8
    search_width: int = 4
    max_reflex_per_node: Optional[int] = None
    max_variants_poly: int = 64
    max_failed_states: Optional[int] = None
    weight_rooms: float = 1.0
    weight_compact: float = 10.0
    track_time: bool = True
    room_keys: Tuple[str, ...] = DEFAULT_ROOM_KEYS
    adaptive_search: AdaptiveSearchConfig = field(default_factory=AdaptiveSearchConfig)

    def __post_init__(self) -> None:
        if self.max_reflex_per_node is None:
            object.__setattr__(self, "max_reflex_per_node", max(int(self.search_width) * 2, 8))


def _cut_line_through_point(pt, angle_rad: float, L: float = 1e4) -> LineString:
    direction = np.array([math.cos(angle_rad), math.sin(angle_rad)], dtype=float)
    point = np.asarray(pt, dtype=float)
    return LineString([tuple(point - direction * L), tuple(point + direction * L)])


def _split_once(poly: Polygon, vidx: int, ang: float) -> List[Polygon]:
    coords = np.asarray(poly.exterior.coords[:-1], dtype=float)
    cut = _cut_line_through_point(coords[vidx], ang)
    try:
        parts = split(poly, cut)
        return [
            simplify_polygon_vertices(geom, tol=1e-9)
            for geom in parts.geoms
            if isinstance(geom, Polygon) and not geom.is_empty
        ]
    except Exception:
        return [simplify_polygon_vertices(poly, tol=1e-9)]


def _split_once_bisector(poly: Polygon, vidx: int, L: float = 1e4) -> List[Polygon]:
    coords = np.asarray(poly.exterior.coords[:-1], dtype=float)
    if len(coords) <= 3 or vidx < 0 or vidx >= len(coords):
        return [simplify_polygon_vertices(poly, tol=1e-9)]

    prev_pt = coords[vidx - 1]
    curr_pt = coords[vidx]
    next_pt = coords[(vidx + 1) % len(coords)]

    vec_prev = prev_pt - curr_pt
    vec_next = next_pt - curr_pt
    len_prev = float(np.hypot(*vec_prev))
    len_next = float(np.hypot(*vec_next))
    if len_prev <= 1e-12 or len_next <= 1e-12:
        return [simplify_polygon_vertices(poly, tol=1e-9)]

    unit_prev = vec_prev / len_prev
    unit_next = vec_next / len_next
    direction = unit_prev + unit_next

    if float(np.hypot(*direction)) <= 1e-12:
        direction = np.array([-(unit_next[1] - unit_prev[1]), unit_next[0] - unit_prev[0]], dtype=float)

    norm = float(np.hypot(*direction))
    if norm <= 1e-12:
        return [simplify_polygon_vertices(poly, tol=1e-9)]

    direction = direction / norm
    cut = LineString([tuple(curr_pt - direction * L), tuple(curr_pt + direction * L)])
    try:
        parts = split(poly, cut)
        return [
            simplify_polygon_vertices(geom, tol=1e-9)
            for geom in parts.geoms
            if isinstance(geom, Polygon) and not geom.is_empty
        ]
    except Exception:
        return [simplify_polygon_vertices(poly, tol=1e-9)]


def _poly_key(poly: Polygon, ndigits: int = 3):
    ext = [(round(x, ndigits), round(y, ndigits)) for x, y in list(poly.exterior.coords)]
    return tuple(ext)


def _state_key(polys: Sequence[Polygon]):
    return tuple(sorted(_poly_key(poly) for poly in polys))


def _score_polys(polys: Sequence[Polygon], cfg: HVConvexDecompositionConfig) -> float:
    return score_polygon_partition(
        polys,
        weight_parts=cfg.weight_rooms,
        weight_compactness=cfg.weight_compact,
    )


def _reflex_vertices_scored(
    poly: Polygon,
    cfg: HVConvexDecompositionConfig,
    *,
    tol: float = 0.0,
) -> List[Tuple[int, float]]:
    coords, cross, orientation = polygon_turn_crosses(poly)
    if coords is None or len(coords) <= 3 or cross.size == 0:
        return []

    target_concavity = math.pi / 2.0
    scored: List[Tuple[int, float]] = []
    reflex_indices = np.where(cross * orientation < -tol)[0]

    for idx in reflex_indices:
        prev_pt = coords[idx - 1]
        curr_pt = coords[idx]
        next_pt = coords[(idx + 1) % len(coords)]

        v_in1 = prev_pt - curr_pt
        v_in2 = next_pt - curr_pt
        len1 = float(np.hypot(*v_in1))
        len2 = float(np.hypot(*v_in2))
        if len1 <= 1e-12 or len2 <= 1e-12:
            continue

        u1 = v_in1 / len1
        u2 = v_in2 / len2
        dot = max(-1.0, min(1.0, float(np.dot(u1, u2))))
        exterior_ang = math.acos(dot)
        concavity = math.pi - exterior_ang
        scored.append((int(idx), concavity))

    scored.sort(key=lambda item: abs(item[1] - target_concavity))
    max_reflex = max(1, int(cfg.max_reflex_per_node if cfg.max_reflex_per_node is not None else cfg.search_width))
    return scored[:max_reflex]


def _dominant_failure_reason(failure_counts: Counter) -> str:
    if not failure_counts:
        return "mixed_dead_end"
    priority = (
        "min_area_dead_end",
        "depth_limit_dead_end",
        "no_axis_aligned_split",
        "invalid_input",
    )
    ranked = sorted(
        failure_counts.items(),
        key=lambda item: (
            -item[1],
            priority.index(item[0]) if item[0] in priority else len(priority),
            item[0],
        ),
    )
    top_reason = ranked[0][0]
    distinct_positive = [reason for reason, count in ranked if count > 0]
    if len(distinct_positive) > 1 and top_reason not in {"invalid_input"}:
        top_count = ranked[0][1]
        next_count = ranked[1][1]
        if next_count == top_count:
            return "mixed_dead_end"
    return top_reason


def iter_room_polygons(
    plan: Dict[str, Any],
    room_keys: Sequence[str] = DEFAULT_ROOM_KEYS,
):
    for key, polys in plan.items():
        if key not in room_keys or not isinstance(polys, list):
            continue
        for poly in polys:
            if isinstance(poly, Polygon) and not poly.is_empty:
                yield poly


def plan_room_polygons(
    plan: Dict[str, Any],
    room_keys: Sequence[str] = DEFAULT_ROOM_KEYS,
) -> list[Polygon]:
    return list(iter_room_polygons(plan, room_keys))


def plan_has_rooms(
    plan: Dict[str, Any],
    room_keys: Sequence[str] = DEFAULT_ROOM_KEYS,
) -> bool:
    return bool(plan_room_polygons(plan, room_keys))


def all_rooms_convex(
    plan: Dict[str, Any],
    room_keys: Sequence[str] = DEFAULT_ROOM_KEYS,
) -> bool:
    polys = plan_room_polygons(plan, room_keys)
    if not polys:
        return False
    return bool(convexity_mask(polys).all())


def all_rooms_above_area(
    plan: Dict[str, Any],
    min_area: float,
    room_keys: Sequence[str] = DEFAULT_ROOM_KEYS,
) -> bool:
    polys = plan_room_polygons(plan, room_keys)
    if not polys:
        return False
    areas = np.fromiter((poly.area for poly in polys), dtype=float)
    return bool((areas >= min_area).all())


def decompose_polygon_hv_with_diagnostics(
    poly: Polygon,
    cfg: HVConvexDecompositionConfig,
) -> Dict[str, Any]:
    angle_h = 0.0
    angle_v = math.pi / 2.0

    def _expand_state(pieces: List[Polygon]) -> StateExpansionResult:
        result = StateExpansionResult()
        target_idx: Optional[int] = None
        target_reflexes: List[Tuple[int, float]] = []
        target_area = -1.0

        for idx, piece in enumerate(pieces):
            if not isinstance(piece, Polygon) or piece.is_empty or is_convex_polygon(piece):
                continue
            reflexes = _reflex_vertices_scored(piece, cfg)
            if reflexes and piece.area > target_area:
                target_idx = idx
                target_reflexes = reflexes
                target_area = float(piece.area)

        if target_idx is None:
            result.dead_end_counts.update(["mixed_dead_end"])
            return result

        target_poly = pieces[target_idx]
        if not target_reflexes:
            result.dead_end_counts.update(["no_axis_aligned_split"])
            return result

        any_effective_split = False
        primary_children_survived = False
        children_by_key: Dict[Any, ExpandedState] = {}

        def _register_child(parts: Sequence[Polygon], *, fallback_label: str | None = None) -> None:
            new_pieces = [
                simplify_polygon_vertices(poly, tol=1e-9)
                for poly in (pieces[:target_idx] + list(parts) + pieces[target_idx + 1 :])
                if isinstance(poly, Polygon) and not poly.is_empty
            ]
            child_key = _state_key(new_pieces)
            if not child_key:
                return
            child = ExpandedState(tuple(new_pieces), (fallback_label,) if fallback_label else ())
            existing = children_by_key.get(child_key)
            if existing is None or (existing.fallback_labels and not child.fallback_labels):
                children_by_key[child_key] = child

        for vidx, _ in target_reflexes:
            for ang in (angle_h, angle_v):
                result.metrics.update(["search_split_attempts"])
                parts = _split_once(target_poly, vidx, ang)
                if len(parts) <= 1:
                    continue
                any_effective_split = True
                if any(part.area < cfg.min_area_m2 for part in parts):
                    result.dead_end_counts.update(["min_area_dead_end"])
                    continue

                result.metrics.update(["search_successful_splits"])
                primary_children_survived = True
                _register_child(parts)

        if not primary_children_survived:
            result.metrics.update(["bisector_fallback_attempts"])
            fallback_before = len(children_by_key)
            for vidx, _ in target_reflexes:
                bisector_parts = _split_once_bisector(target_poly, vidx)
                if len(bisector_parts) <= 1:
                    continue
                if any(part.area < cfg.min_area_m2 for part in bisector_parts):
                    result.dead_end_counts.update(["min_area_dead_end"])
                    continue
                _register_child(bisector_parts, fallback_label="bisector_dead_end")
            if len(children_by_key) > fallback_before:
                result.metrics.update(["bisector_fallback_successes"])

        if not any_effective_split:
            result.dead_end_counts.update(["no_axis_aligned_split"])

        result.children.extend(children_by_key.values())
        return result

    if not isinstance(poly, Polygon) or poly.is_empty:
        return {
            "variant_records": [],
            "success": False,
            "failure_reason": "invalid_input",
            "failure_counts": {"invalid_input": 1},
            "n_variants": 0,
            "terminated_by": "invalid_input",
            "search_total_states": 0,
            "search_max_depth": 0,
            "search_max_width": 0,
            "search_split_attempts": 0,
            "search_successful_splits": 0,
            "bisector_fallback_attempts": 0,
            "bisector_fallback_successes": 0,
            "variant_cap_hit": False,
            "search_depth_used": None,
            "search_width_used": None,
            "search_attempt_count": 0,
            "search_attempt_history": [],
        }

    poly = simplify_polygon_vertices(poly, tol=1e-9)

    if poly.area < cfg.min_area_m2:
        return {
            "variant_records": [],
            "success": False,
            "failure_reason": "min_area_dead_end",
            "failure_counts": {"min_area_dead_end": 1},
            "n_variants": 0,
            "terminated_by": "min_area_dead_end",
            "search_total_states": 0,
            "search_max_depth": 0,
            "search_max_width": 0,
            "search_split_attempts": 0,
            "search_successful_splits": 0,
            "bisector_fallback_attempts": 0,
            "bisector_fallback_successes": 0,
            "variant_cap_hit": False,
            "search_depth_used": None,
            "search_width_used": None,
            "search_attempt_count": 0,
            "search_attempt_history": [],
        }

    if is_convex_polygon(poly):
        return {
            "variant_records": [{"parts": [poly], "fallback_used": False}],
            "success": True,
            "failure_reason": None,
            "failure_counts": {},
            "n_variants": 1,
            "terminated_by": "already_convex",
            "search_total_states": 1,
            "search_max_depth": 0,
            "search_max_width": 0,
            "search_split_attempts": 0,
            "search_successful_splits": 0,
            "bisector_fallback_attempts": 0,
            "bisector_fallback_successes": 0,
            "variant_cap_hit": False,
            "search_depth_used": None,
            "search_width_used": None,
            "search_attempt_count": 0,
            "search_attempt_history": [],
        }

    search = run_polygon_state_search(
        [poly],
        cfg=DecompositionSearchConfig(
            max_depth=cfg.max_depth,
            max_variants=cfg.max_variants_poly,
            max_failed_states=cfg.max_failed_states,
        ),
        is_convex_piece=is_convex_polygon,
        state_key_fn=_state_key,
        expand_state_fn=_expand_state,
        adaptive_cfg=cfg.adaptive_search,
        default_width=cfg.search_width,
        state_score_fn=lambda pieces: _score_polys(pieces, cfg),
        min_piece_area=cfg.min_area_m2,
    )
    variant_records = list(search["variants"])
    success = bool(variant_records)
    failure_counts = Counter(search.get("dead_end_counts") or {})
    failure_reason = None if success else _dominant_failure_reason(failure_counts)
    result = {
        "variant_records": variant_records,
        "success": success,
        "failure_reason": failure_reason,
        "failure_counts": dict(failure_counts),
        "n_variants": len(variant_records),
        "terminated_by": str(search.get("terminated_by", "queue_exhausted")),
        "search_total_states": int(search.get("search_total_states", 0)),
        "search_max_depth": int(search.get("search_max_depth", 0)),
        "search_max_width": int(search.get("search_max_width", 0)),
        "search_split_attempts": int(search.get("search_split_attempts", 0)),
        "search_successful_splits": int(search.get("search_successful_splits", 0)),
        "bisector_fallback_attempts": int(search.get("bisector_fallback_attempts", 0)),
        "bisector_fallback_successes": int(search.get("bisector_fallback_successes", 0)),
        "variant_cap_hit": bool(search.get("terminated_by") == "max_success_variants"),
        "search_depth_used": search.get("search_depth_used"),
        "search_width_used": search.get("search_width_used"),
        "search_attempt_count": int(search.get("search_attempt_count", 0)),
        "search_attempt_history": list(search.get("search_attempt_history") or []),
    }
    return result


def pick_best_convex_variant_per_room(
    plan: Dict[str, Any],
    cfg: HVConvexDecompositionConfig,
    *,
    plot_variants: bool = False,
    max_previews: int = 48,
) -> Tuple[Dict[str, Any], float, Dict[str, float], List[Dict[str, Any]], List[Dict[str, Any]]]:
    best_plan = {"id": plan.get("id")}
    for extra_key, extra_value in plan.items():
        if extra_key != "id" and extra_key not in cfg.room_keys:
            best_plan[extra_key] = extra_value
    room_scores: Dict[str, float] = {}
    previews: List[Dict[str, Any]] = []
    room_search_records: List[Dict[str, Any]] = []

    def _copy_room_polys(polys: Any) -> List[Polygon]:
        if not isinstance(polys, list):
            return []
        return [
            simplify_polygon_vertices(poly, tol=1e-9)
            for poly in polys
            if isinstance(poly, Polygon) and not poly.is_empty
        ]

    for room_key in cfg.room_keys:
        cur_polys = _copy_room_polys(plan.get(room_key, []))
        if not cur_polys:
            continue

        chosen: List[Polygon] = []
        score_sum = 0.0
        convex_mask_local = convexity_mask(cur_polys)

        for part_index, poly in enumerate(cur_polys):
            input_vertices = len(list(poly.exterior.coords))
            input_area = float(poly.area)

            if convex_mask_local[part_index]:
                chosen.append(poly)
                score = _score_polys([poly], cfg)
                score_sum += score
                room_search_records.append(
                    {
                        "plan_id": plan.get("id"),
                        "room_key": room_key,
                        "part_index": part_index,
                        "input_area_m2": input_area,
                        "input_vertices": input_vertices,
                        "input_is_convex": True,
                        "success": True,
                        "failure_reason": None,
                        "n_variants": 1,
                        "chosen_parts": 1,
                        "score": score,
                        "search_total_states": 0,
                        "search_max_depth": 0,
                        "search_max_width": 0,
                        "search_split_attempts": 0,
                        "search_successful_splits": 0,
                        "bisector_fallback_attempts": 0,
                        "bisector_fallback_successes": 0,
                        "variant_cap_hit": False,
                        "search_depth_used": None,
                        "search_width_used": None,
                        "search_attempt_count": 0,
                        "output_nonconvex": False,
                        "fallback_used": None,
                        "search_terminated_by": "already_convex",
                        "time_seconds": 0.0,
                        "failure_counts": {},
                        "geometry": poly,
                    }
                )
                continue

            started_at = time.perf_counter()
            search = decompose_polygon_hv_with_diagnostics(poly, cfg)
            variants = search["variant_records"]
            elapsed = time.perf_counter() - started_at

            if not variants:
                chosen.append(poly)
                score = _score_polys([poly], cfg)
                score_sum += score
                room_search_records.append(
                    {
                        "plan_id": plan.get("id"),
                        "room_key": room_key,
                        "part_index": part_index,
                        "input_area_m2": input_area,
                        "input_vertices": input_vertices,
                        "input_is_convex": False,
                        "success": False,
                        "failure_reason": search["failure_reason"],
                        "n_variants": 0,
                        "chosen_parts": 1,
                        "score": score,
                        "search_total_states": int(search["search_total_states"]),
                        "search_max_depth": int(search["search_max_depth"]),
                        "search_max_width": int(search["search_max_width"]),
                        "search_split_attempts": int(search["search_split_attempts"]),
                        "search_successful_splits": int(search["search_successful_splits"]),
                        "bisector_fallback_attempts": int(search["bisector_fallback_attempts"]),
                        "bisector_fallback_successes": int(search["bisector_fallback_successes"]),
                        "variant_cap_hit": bool(search["variant_cap_hit"]),
                        "search_depth_used": search.get("search_depth_used"),
                        "search_width_used": search.get("search_width_used"),
                        "search_attempt_count": int(search.get("search_attempt_count", 0)),
                        "output_nonconvex": True,
                        "fallback_used": None,
                        "search_terminated_by": search.get("terminated_by"),
                        "time_seconds": elapsed if cfg.track_time else None,
                        "failure_counts": search["failure_counts"],
                        "geometry": poly,
                    }
                )
                continue

            variant_scores = [
                (
                    _score_polys(variant["parts"], cfg),
                    bool(variant.get("fallback_used")),
                )
                for variant in variants
            ]
            best_idx = min(
                range(len(variant_scores)),
                key=lambda idx: (variant_scores[idx][0], variant_scores[idx][1]),
            )
            chosen_variant = variants[best_idx]["parts"]
            chosen.extend(chosen_variant)
            score = float(variant_scores[best_idx][0])
            score_sum += score

            room_search_records.append(
                {
                    "plan_id": plan.get("id"),
                    "room_key": room_key,
                    "part_index": part_index,
                    "input_area_m2": input_area,
                    "input_vertices": input_vertices,
                    "input_is_convex": False,
                    "success": True,
                    "failure_reason": None,
                    "n_variants": int(search["n_variants"]),
                    "chosen_parts": len(chosen_variant),
                    "score": score,
                    "search_total_states": int(search["search_total_states"]),
                    "search_max_depth": int(search["search_max_depth"]),
                    "search_max_width": int(search["search_max_width"]),
                    "search_split_attempts": int(search["search_split_attempts"]),
                    "search_successful_splits": int(search["search_successful_splits"]),
                    "bisector_fallback_attempts": int(search["bisector_fallback_attempts"]),
                    "bisector_fallback_successes": int(search["bisector_fallback_successes"]),
                    "variant_cap_hit": bool(search["variant_cap_hit"]),
                    "search_depth_used": search.get("search_depth_used"),
                    "search_width_used": search.get("search_width_used"),
                    "search_attempt_count": int(search.get("search_attempt_count", 0)),
                    "output_nonconvex": False,
                    "fallback_used": variants[best_idx].get("fallback_used"),
                    "search_terminated_by": search.get("terminated_by"),
                    "time_seconds": elapsed if cfg.track_time else None,
                    "failure_counts": {},
                    "geometry": poly,
                }
            )

            if plot_variants and len(previews) < max_previews:
                remaining = max_previews - len(previews)
                for variant in variants[:remaining]:
                    preview = {"id": f"{plan.get('id', 'plan')}-{room_key}-{part_index}"}
                    for extra_key, extra_value in plan.items():
                        if extra_key != "id" and extra_key not in cfg.room_keys:
                            preview[extra_key] = extra_value
                    for rk in cfg.room_keys:
                        room_polys = _copy_room_polys(plan.get(rk, []))
                        if not room_polys:
                            continue
                        if rk == room_key:
                            others = [candidate for k, candidate in enumerate(room_polys) if k != part_index]
                            preview[rk] = others + list(variant["parts"])
                        else:
                            preview[rk] = room_polys
                    previews.append(preview)

        if chosen:
            best_plan[room_key] = chosen
            room_scores[room_key] = score_sum

    total_score = float(sum(room_scores.values()))
    return best_plan, total_score, room_scores, previews, room_search_records
