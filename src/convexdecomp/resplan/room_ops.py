"""Room-level utility functions reused across the ResPlan adapter."""

from convexdecomp.resplan.preprocess import (
    count_plan_room_polygons,
    count_room_vertices,
    explode_polygon_parts,
    iter_plan_room_polygons,
    plan_room_polygons,
    reduce_to_room_layers,
    scaled_room_polygons_above_area,
    simplify_polygon_vertices,
    split_room_plans,
)

__all__ = [
    "count_plan_room_polygons",
    "count_room_vertices",
    "explode_polygon_parts",
    "iter_plan_room_polygons",
    "plan_room_polygons",
    "reduce_to_room_layers",
    "scaled_room_polygons_above_area",
    "simplify_polygon_vertices",
    "split_room_plans",
]
