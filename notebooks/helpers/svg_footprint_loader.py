"""
Load footprint-like SVG elements into a GeoDataFrame.

The parser currently supports:
- `path` elements composed of straight segments (`M/L/H/V/Z`)
- `rect`
- nested SVG `matrix(a,b,c,d,e,f)` transforms

The main entry point is `load_svg_footprints`, which can either:
- load all supported path/rect elements,
- filter by fill/stroke styles,
- or accept a custom element predicate.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import affinity
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

_COMMAND_RE = re.compile(r"[MmLlHhVvZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")
_MATRIX_RE = re.compile(r"matrix\(([^)]+)\)")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_style(style: str | None) -> dict[str, str]:
    if not style:
        return {}
    items = {}
    for part in style.split(";"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        items[key.strip()] = value.strip()
    return items


def _svg_matrix(transform: str | None) -> np.ndarray:
    matrix = np.eye(3, dtype=float)
    if not transform:
        return matrix

    for match in _MATRIX_RE.finditer(transform):
        values = [float(value.strip()) for value in match.group(1).split(",")]
        if len(values) != 6:
            continue
        a, b, c, d, e, f = values
        current = np.array(
            [
                [a, c, e],
                [b, d, f],
                [0.0, 0.0, 1.0],
            ],
            dtype=float,
        )
        matrix = matrix @ current
    return matrix


def _apply_matrix(point: tuple[float, float], matrix: np.ndarray) -> tuple[float, float]:
    x, y = point
    out = matrix @ np.array([x, y, 1.0], dtype=float)
    return float(out[0]), float(out[1])


def _node_style_value(node: ET.Element, key: str) -> str:
    style = _parse_style(node.get("style"))
    return style.get(key, node.get(key, "")).lower()


def _matches_svg_style(
    node: ET.Element,
    *,
    fill: str | tuple[str, ...] | None = None,
    stroke: str | tuple[str, ...] | None = None,
) -> bool:
    actual_fill = _node_style_value(node, "fill")
    actual_stroke = _node_style_value(node, "stroke")

    if fill is not None:
        allowed_fill = (fill,) if isinstance(fill, str) else fill
        if actual_fill not in {value.lower() for value in allowed_fill}:
            return False

    if stroke is not None:
        allowed_stroke = (stroke,) if isinstance(stroke, str) else stroke
        if actual_stroke not in {value.lower() for value in allowed_stroke}:
            return False

    return True


def _parse_path_subpaths(d: str) -> list[list[tuple[float, float]]]:
    tokens = _COMMAND_RE.findall(d)
    if not tokens:
        return []

    subpaths: list[list[tuple[float, float]]] = []
    current_path: list[tuple[float, float]] = []
    cursor = (0.0, 0.0)
    start = (0.0, 0.0)
    command = ""
    i = 0

    def read_number() -> float:
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        token = tokens[i]
        if re.fullmatch(r"[MmLlHhVvZz]", token):
            command = token
            i += 1
        if not command:
            raise ValueError("SVG path command sequence is malformed.")

        if command in {"M", "m"}:
            if current_path and len(current_path) >= 3:
                subpaths.append(current_path)
            current_path = []

            x = read_number()
            y = read_number()
            if command == "m":
                cursor = (cursor[0] + x, cursor[1] + y)
            else:
                cursor = (x, y)
            start = cursor
            current_path.append(cursor)
            command = "l" if command == "m" else "L"
            continue

        if command in {"L", "l"}:
            x = read_number()
            y = read_number()
            if command == "l":
                cursor = (cursor[0] + x, cursor[1] + y)
            else:
                cursor = (x, y)
            current_path.append(cursor)
            continue

        if command in {"H", "h"}:
            x = read_number()
            if command == "h":
                cursor = (cursor[0] + x, cursor[1])
            else:
                cursor = (x, cursor[1])
            current_path.append(cursor)
            continue

        if command in {"V", "v"}:
            y = read_number()
            if command == "v":
                cursor = (cursor[0], cursor[1] + y)
            else:
                cursor = (cursor[0], y)
            current_path.append(cursor)
            continue

        if command in {"Z", "z"}:
            if current_path and current_path[0] != current_path[-1]:
                current_path.append(start)
            if current_path and len(current_path) >= 4:
                subpaths.append(current_path)
            current_path = []
            command = ""
            continue

    if current_path and len(current_path) >= 3:
        if current_path[0] != current_path[-1]:
            current_path.append(current_path[0])
        subpaths.append(current_path)
    return subpaths


def _rings_to_geometry(rings: list[list[tuple[float, float]]]) -> BaseGeometry | None:
    raw_polygons = []
    for ring in rings:
        try:
            polygon = Polygon(ring)
        except Exception:
            continue
        if polygon.is_empty:
            continue
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        raw_polygons.append(polygon)

    if not raw_polygons:
        return None

    parents: list[int | None] = [None] * len(raw_polygons)
    for idx, polygon in enumerate(raw_polygons):
        rep = polygon.representative_point()
        best_parent = None
        best_parent_area = None
        for jdx, candidate in enumerate(raw_polygons):
            if idx == jdx:
                continue
            if candidate.area <= polygon.area:
                continue
            if candidate.contains(rep):
                if best_parent_area is None or candidate.area < best_parent_area:
                    best_parent = jdx
                    best_parent_area = candidate.area
        parents[idx] = best_parent

    depths = [0] * len(raw_polygons)
    for idx in range(len(raw_polygons)):
        depth = 0
        parent = parents[idx]
        while parent is not None:
            depth += 1
            parent = parents[parent]
        depths[idx] = depth

    assembled: list[Polygon] = []
    for idx, polygon in enumerate(raw_polygons):
        if depths[idx] % 2 == 1:
            continue
        holes = []
        for jdx, hole in enumerate(raw_polygons):
            if parents[jdx] == idx and depths[jdx] % 2 == 1:
                holes.append(list(hole.exterior.coords))
        assembled.append(Polygon(list(polygon.exterior.coords), holes=holes))

    if not assembled:
        return None
    if len(assembled) == 1:
        return assembled[0]
    return unary_union(assembled)


def _geometry_from_path(node: ET.Element, matrix: np.ndarray) -> BaseGeometry | None:
    d = node.get("d", "")
    subpaths = _parse_path_subpaths(d)
    transformed = [[_apply_matrix(point, matrix) for point in ring] for ring in subpaths]
    return _rings_to_geometry(transformed)


def _geometry_from_rect(node: ET.Element, matrix: np.ndarray) -> BaseGeometry | None:
    x = float(node.get("x", "0"))
    y = float(node.get("y", "0"))
    width = float(node.get("width", "0"))
    height = float(node.get("height", "0"))
    ring = [
        (x, y),
        (x + width, y),
        (x + width, y + height),
        (x, y + height),
        (x, y),
    ]
    transformed = [_apply_matrix(point, matrix) for point in ring]
    return Polygon(transformed)


def _count_vertices(geom: BaseGeometry | None) -> int:
    if geom is None or geom.is_empty:
        return 0
    if isinstance(geom, Polygon):
        return max(0, len(list(geom.exterior.coords)) - 1)
    if isinstance(geom, MultiPolygon):
        return sum(max(0, len(list(part.exterior.coords)) - 1) for part in geom.geoms)
    return 0


def load_svg_footprints(
    svg_path: str | Path,
    *,
    meters_per_svg_unit: float = 1.0,
    flip_y: bool = True,
    fill: str | tuple[str, ...] | None = None,
    stroke: str | tuple[str, ...] | None = None,
    element_filter: Callable[[ET.Element], bool] | None = None,
    sample_prefix: str = "shape",
) -> gpd.GeoDataFrame:
    svg_path = Path(svg_path)
    root = ET.fromstring(svg_path.read_text())
    rows = []
    supported_tags = {"path", "rect"}

    def visit(node: ET.Element, parent_matrix: np.ndarray) -> None:
        local_matrix = _svg_matrix(node.get("transform"))
        total_matrix = parent_matrix @ local_matrix
        tag = _local_name(node.tag)

        geom: BaseGeometry | None = None
        should_include = False
        if tag in supported_tags:
            if element_filter is not None:
                should_include = bool(element_filter(node))
            elif fill is None and stroke is None:
                should_include = True
            else:
                should_include = _matches_svg_style(node, fill=fill, stroke=stroke)

        if should_include:
            if tag == "path":
                geom = _geometry_from_path(node, total_matrix)
            elif tag == "rect":
                geom = _geometry_from_rect(node, total_matrix)

        if geom is not None and not geom.is_empty:
            if flip_y:
                geom = affinity.scale(geom, xfact=meters_per_svg_unit, yfact=-meters_per_svg_unit, origin=(0, 0))
            else:
                geom = affinity.scale(geom, xfact=meters_per_svg_unit, yfact=meters_per_svg_unit, origin=(0, 0))

            rows.append(
                {
                    "sample_id": f"{sample_prefix}_{len(rows) + 1:02d}",
                    "shape_index": len(rows),
                    "svg_tag": tag,
                    "svg_id": node.get("id"),
                    "geometry": geom,
                    "area_m2": float(geom.area),
                    "n_vertices": _count_vertices(geom),
                }
            )

        for child in node:
            visit(child, total_matrix)

    visit(root, np.eye(3, dtype=float))
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=None)
    return gdf
