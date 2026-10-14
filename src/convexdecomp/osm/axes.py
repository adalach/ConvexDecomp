"""Architectural axes: the wall directions and wall lines of a footprint, and its outline normalized to them.

`find_axes` reads the axes off a footprint in two levels:

- **Directions.** Edges are taken longest first; an edge joins the nearest direction it fits and
  otherwise starts one of its own. An edge fits a direction when it is within
  ``angle_tolerance_deg`` of it, or when turning it onto the direction moves its ends by at most
  ``direction_tolerance_m`` (a short edge's direction is imprecise: 5 cm of mapping noise on a
  50 cm edge is 11 degrees). Directions are averaged as axes, so 0.5 and 179.5 degrees are one
  direction. With ``orthogonal_tolerance_deg``, the longest direction and every direction within
  that tolerance of its perpendicular become exactly perpendicular.
- **Lines.** Within a direction, edges facing the same way (outward normal) and whose offsets
  differ by at most ``offset_tolerance_m`` share one wall line. The longest edge sets the line, so
  main walls stay where they are and short runs a few decimetres out of line move onto them; edges
  facing each other (the two sides of a part of the building) are never merged.

`snap_to_axes` moves every edge onto its wall line and puts each vertex where the lines of its two
edges cross; consecutive edges on one line become one, and a step between two lines that became one
disappears. The result is kept only when it passes one acceptance test against the footprint it
started from (`AxesSettings`): one valid polygon with the same holes, moved by at most
``max_move_m``, with an area change of at most ``max_area_change``, and no corner sharper than
``min_corner_deg`` that was not there before. Otherwise the footprint stays as it was, and the
status says which test failed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient

from convexdecomp.osm.vertices import VertexCleanup, remove_redundant_vertices

__all__ = ["Axes", "AxesSettings", "find_axes", "residual_steps", "snap_to_axes"]


@dataclass(frozen=True)
class AxesSettings:
    """How edges are grouped into directions and wall lines, and the acceptance test of the normalized outline."""

    angle_tolerance_deg: float = 3.0
    direction_tolerance_m: float = 0.1
    #: Facade runs at most this far apart become one wall (BEM4AI closes facade offsets up to 0.5 m).
    offset_tolerance_m: float = 0.5
    #: None: directions are not made perpendicular.
    orthogonal_tolerance_deg: float | None = None
    #: A vertex moved further than this by the intersection of its lines keeps its place on its edge's line.
    max_vertex_shift_m: float = 1.0
    max_move_m: float = 0.7
    max_area_change: float = 0.05
    min_corner_deg: float = 30.0
    cleanup: VertexCleanup = field(default_factory=lambda: VertexCleanup(0.01, 0.01))


@dataclass
class Axes:
    """The axes of one footprint: per edge (ring, index) its direction and line, and the lines themselves."""

    directions: list[float]           # radians in [0, pi)
    lines: list[dict]                 # {"direction": k, "facing": +1/-1, "rho": offset, "length": total}
    edge_line: dict                   # (ring, index) -> line id

    def line_geometries(self, bounds, pad: float = 2.0) -> list[tuple[int, LineString]]:
        """Every wall line clipped to `bounds` (plus `pad`), with its direction index, for figures."""
        minx, miny, maxx, maxy = bounds
        box = shapely.box(minx - pad, miny - pad, maxx + pad, maxy + pad)
        span = 2 * math.hypot(maxx - minx, maxy - miny) + 4 * pad
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        out = []
        for line in self.lines:
            theta = self.directions[line["direction"]]
            d = np.array([math.cos(theta), math.sin(theta)])
            nrm = np.array([-d[1], d[0]])
            base = nrm * line["rho"]
            # The point of the line nearest the centre, then far along it both ways.
            centre = base + d * (np.array([cx, cy]) @ d)
            seg = LineString([tuple(centre - d * span), tuple(centre + d * span)]).intersection(box)
            if not seg.is_empty:
                out.append((line["direction"], seg))
        return out


# ------------------------------------------------------------------------------------------------- edges
def _edges(poly: Polygon) -> list[dict]:
    """Every edge of every ring, oriented with the building on its left."""
    poly = orient(poly, 1.0)
    edges = []
    for r, ring in enumerate([poly.exterior, *poly.interiors]):
        pts = np.asarray(ring.coords[:-1], dtype=float)
        n = len(pts)
        for i in range(n):
            p, q = pts[i], pts[(i + 1) % n]
            v = q - p
            length = float(math.hypot(*v))
            if length <= 1e-12:
                continue
            d = v / length
            edges.append({"ring": r, "index": i, "p": p, "q": q, "length": length,
                          "theta": math.atan2(d[1], d[0]) % math.pi, "outward": np.array([d[1], -d[0]]),
                          "mid": (p + q) / 2})
    return edges


def _axial_gap(a: float, b: float) -> float:
    gap = abs(a - b) % math.pi
    return min(gap, math.pi - gap)


def _axial_mean(thetas, weights, fold: int = 2) -> float:
    """Weighted mean of axial directions (fold 2: lines; fold 4: lines up to right angles)."""
    thetas, weights = np.asarray(thetas), np.asarray(weights)
    c, s = (weights * np.cos(fold * thetas)).sum(), (weights * np.sin(fold * thetas)).sum()
    return (math.atan2(s, c) / fold) % (math.pi if fold == 2 else math.pi / 2)


def _fits(edge: dict, theta: float, settings: AxesSettings) -> float | None:
    """How far `edge` is from direction `theta` (radians) when it fits it, else None."""
    gap = _axial_gap(edge["theta"], theta)
    if gap <= math.radians(settings.angle_tolerance_deg) or edge["length"] / 2 * math.sin(gap) <= settings.direction_tolerance_m:
        return gap
    return None


# -------------------------------------------------------------------------------------------------- axes
def find_axes(poly: Polygon, settings: AxesSettings = AxesSettings()) -> Axes:
    """The directions and wall lines of a polygon (see the module docstring)."""
    edges = sorted(_edges(poly), key=lambda e: -e["length"])
    members: list[list[dict]] = []
    directions: list[float] = []
    for edge in edges:
        fits = [(gap, k) for k, theta in enumerate(directions) if (gap := _fits(edge, theta, settings)) is not None]
        if fits:
            k = min(fits)[1]
            members[k].append(edge)
            # Long edges set the direction; a short one fitted by its ends only joins it.
            long_members = [e for e in members[k] if e["length"] >= 2 * settings.direction_tolerance_m / max(
                math.sin(math.radians(settings.angle_tolerance_deg)), 1e-9)] or members[k]
            directions[k] = _axial_mean([e["theta"] for e in long_members], [e["length"] for e in long_members])
        else:
            members.append([edge])
            directions.append(edge["theta"])

    if settings.orthogonal_tolerance_deg is not None and directions:
        weights = [sum(e["length"] for e in m) for m in members]
        main = int(np.argmax(weights))
        tol = math.radians(settings.orthogonal_tolerance_deg)
        grid = [k for k, theta in enumerate(directions)
                if k == main or abs(_axial_gap(theta, directions[main]) - math.pi / 2) <= tol]
        if len(grid) > 1:
            pool = [e for k in grid for e in members[k]]
            base = _axial_mean([e["theta"] for e in pool], [e["length"] for e in pool], fold=4)
            for k in grid:
                directions[k] = min((base, (base + math.pi / 2) % math.pi), key=lambda t: _axial_gap(t, directions[k]))

    # Edges that meet, run in one direction and face the same way are one (slightly bent) wall: one run.
    direction_of = {(e["ring"], e["index"]): k for k, m in enumerate(members) for e in m}
    by_key = {(e["ring"], e["index"]): e for e in edges}
    runs: list[list[dict]] = []
    run_of: dict = {}
    for k, theta in enumerate(directions):
        normal = np.array([-math.sin(theta), math.cos(theta)])
        for edge in members[k]:
            edge["facing"] = 1 if float(edge["outward"] @ normal) >= 0 else -1
            edge["rho"] = float(edge["mid"] @ normal)
    oriented = orient(poly, 1.0)
    ring_sizes = {r: len(ring.coords) - 1 for r, ring in enumerate([oriented.exterior, *oriented.interiors])}
    for key in sorted(by_key, key=lambda key: key):
        edge = by_key[key]
        prev_key = (key[0], (key[1] - 1) % ring_sizes[key[0]])
        prev = by_key.get(prev_key)
        if (prev is not None and prev_key in run_of and direction_of[prev_key] == direction_of[key]
                and prev["facing"] == edge["facing"]):
            run = run_of[prev_key]
        else:
            runs.append([])
            run = len(runs) - 1
        runs[run].append(edge)
        run_of[key] = run
    # Close a run that wraps around the start of its ring.
    for r, size in ring_sizes.items():
        first, last = (r, 0), (r, size - 1)
        if (first in run_of and last in run_of and run_of[first] != run_of[last]
                and direction_of[first] == direction_of[last] and by_key[first]["facing"] == by_key[last]["facing"]):
            keep, gone = run_of[last], run_of[first]
            for e in runs[gone]:
                run_of[(e["ring"], e["index"])] = keep
            runs[keep].extend(runs[gone])
            runs[gone] = []

    # Runs of one direction and facing whose offsets differ by at most the tolerance share a line;
    # the longest run sets it.
    lines: list[dict] = []
    edge_line: dict = {}
    summaries = []
    for run in runs:
        if not run:
            continue
        length = sum(e["length"] for e in run)
        rho = sum(e["rho"] * e["length"] for e in run) / length
        summaries.append((length, rho, direction_of[(run[0]["ring"], run[0]["index"])], run[0]["facing"], run))
    for length, rho, k, facing, run in sorted(summaries, key=lambda s: -s[0]):
        near = [(abs(rho - line["rho"]), j) for j, line in enumerate(lines)
                if line["direction"] == k and line["facing"] == facing
                and abs(rho - line["rho"]) <= settings.offset_tolerance_m]
        if near:
            j = min(near)[1]
            lines[j]["length"] += length
        else:
            lines.append({"direction": k, "facing": facing, "rho": rho, "length": length})
            j = len(lines) - 1
        for e in run:
            edge_line[(e["ring"], e["index"])] = j
    return Axes(directions, lines, edge_line)


# ---------------------------------------------------------------------------------------------- snapping
def _line(axes: Axes, j: int) -> tuple[np.ndarray, float]:
    theta = axes.directions[axes.lines[j]["direction"]]
    return np.array([-math.sin(theta), math.cos(theta)]), axes.lines[j]["rho"]


def _project(point: np.ndarray, normal: np.ndarray, rho: float) -> np.ndarray:
    return point - normal * (float(normal @ point) - rho)


def _snap_ring(pts: np.ndarray, ring: int, axes: Axes, settings: AxesSettings) -> list:
    n = len(pts)
    lines = [axes.edge_line.get((ring, i)) for i in range(n)]
    out = []
    for i in range(n):
        a, b = lines[i - 1], lines[i]
        p = pts[i]
        if a is None or b is None:
            out.append(tuple(p))
            continue
        (na, ra), (nb, rb) = _line(axes, a), _line(axes, b)
        det = na[0] * nb[1] - na[1] * nb[0]
        if a == b:
            out.append(tuple(_project(p, nb, rb)))
            continue
        if abs(det) < math.sin(math.radians(1.0)):
            # Two parallel lines meet here (the step between them vanished): keep both, joined across.
            out.append(tuple(_project(p, na, ra)))
            out.append(tuple(_project(p, nb, rb)))
            continue
        x = np.array([(ra * nb[1] - rb * na[1]) / det, (na[0] * rb - nb[0] * ra) / det])
        if math.dist(x, p) <= settings.max_vertex_shift_m:
            out.append(tuple(x))
        else:
            out.append(tuple(_project(p, nb, rb)))
    return out


def _sharpest_corner(poly: Polygon) -> float:
    pts = np.asarray(orient(poly, 1.0).exterior.coords[:-1])
    v1 = pts - np.roll(pts, 1, axis=0)
    v2 = np.roll(pts, -1, axis=0) - pts
    turn = np.degrees(np.arctan2(v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0], (v1 * v2).sum(axis=1)))
    return float(np.min(180.0 - turn))


def snap_to_axes(poly, settings: AxesSettings = AxesSettings(), axes: Axes | None = None) -> tuple[object, str, Axes | None]:
    """A polygon normalized to its architectural axes (`find_axes` when `axes` is not given), its status, and the axes.

    Status: ``snapped``, ``unchanged`` (already on its axes), or ``kept_<test>`` when the acceptance
    test failed (``invalid``, ``holes``, ``move``, ``area``, ``corner``) and the polygon stays as it was.
    """
    if poly is None or poly.geom_type != "Polygon" or poly.is_empty:
        return poly, "not_polygon", None
    axes = axes if axes is not None else find_axes(poly, settings)
    oriented = orient(poly, 1.0)
    rings = [np.asarray(r.coords[:-1], dtype=float) for r in [oriented.exterior, *oriented.interiors]]
    snapped = [_snap_ring(pts, r, axes, settings) for r, pts in enumerate(rings)]
    try:
        candidate = Polygon(snapped[0], snapped[1:])
    except (ValueError, shapely.errors.GEOSException):
        return poly, "kept_invalid", axes
    candidate, _ = remove_redundant_vertices(candidate, settings.cleanup)
    if not candidate.is_valid or candidate.geom_type != "Polygon":
        return poly, "kept_invalid", axes
    if len(candidate.interiors) != len(poly.interiors):
        return poly, "kept_holes", axes
    if candidate.hausdorff_distance(poly) > settings.max_move_m:
        return poly, "kept_move", axes
    if abs(candidate.area - poly.area) > settings.max_area_change * poly.area:
        return poly, "kept_area", axes
    if _sharpest_corner(candidate) < min(settings.min_corner_deg, _sharpest_corner(poly)) - 0.5:
        return poly, "kept_corner", axes
    if candidate.equals_exact(poly, 1e-9) or candidate.hausdorff_distance(poly) < 1e-9:
        return poly, "unchanged", axes
    return candidate, "snapped", axes


# ---------------------------------------------------------------------------------------------- measures
def residual_steps(poly, max_step_m: float = 0.5, max_jog_m: float = 1.0, angle_deg: float = 2.0) -> int:
    """Facade steps left in an outline: a short edge between two parallel edges facing the same way at most `max_step_m` apart.

    These are what the facade-offset step of BEM4AI closes; the axes should leave none.
    """
    if poly is None or poly.geom_type != "Polygon":
        return 0
    count = 0
    for ring in [orient(poly, 1.0).exterior, *orient(poly, 1.0).interiors]:
        pts = np.asarray(ring.coords[:-1], dtype=float)
        n = len(pts)
        if n < 4:
            continue
        v = np.roll(pts, -1, axis=0) - pts
        length = np.hypot(*v.T)
        for i in range(n):
            a, b, c = (i - 1) % n, i, (i + 1) % n
            if length[b] > max_jog_m or length[a] < 1e-9 or length[c] < 1e-9:
                continue
            da, dc = v[a] / length[a], v[c] / length[c]
            if float(da @ dc) < math.cos(math.radians(angle_deg)):
                continue  # not parallel, or facing the other way
            offset = abs(da[0] * v[b][1] - da[1] * v[b][0])  # the step across the walls' direction
            if 1e-6 < offset <= max_step_m:
                count += 1
    return count
