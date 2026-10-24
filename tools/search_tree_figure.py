"""Draw the search tree the convex decomposer walks for one polygon.

A badge per state, the cut on every edge, and what the search did with each
state, in the layout of the EG-ICE 2026 search-tree figure. A thumbnail puts
the piece being decomposed in colour - the one the search cuts next, or at a
leaf the pieces its cut made - and the rest of the state in grey with the
earlier cuts as lines, the cut that made the state in orange. The tree comes
from `search_trace.trace_search`, which records the search without changing
it; every run here checks that against an untraced run.

Usage, from the repository root::

    python tools/search_tree_figure.py --input footprint.wkt -o tree.pdf tree.png

    python tools/search_tree_figure.py --sample-id Gorzow_Wielkopolski_0007 --input-index 2 \\
        -o temp/gorzow.pdf

The second form reads the BEM4AI decomposition inputs,
``data/bem4ai/decomposition_inputs.gpkg`` (local, not in the repository).
Coordinates are taken to be metres.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import shapely  # noqa: E402
from shapely import affinity  # noqa: E402
from shapely import wkb as shapely_wkb  # noqa: E402
from shapely import wkt as shapely_wkt  # noqa: E402
from shapely.geometry import LineString, MultiPolygon, Polygon, shape  # noqa: E402
from shapely.geometry import box as shapely_box  # noqa: E402
from shapely.geometry.polygon import orient  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402

from search_trace import (  # noqa: E402
    ENGINE,
    REPO_ROOT,
    SearchNode,
    SearchTrace,
    best_path,
    bem4ai_notebook_config,
    compare_with_untraced,
    focus,
    incoming_chord,
    node_status,
    piece_counts,
    trace_search,
    walk,
)

__all__ = ["FigureStyle", "draw_search_tree", "gpkg_polygon", "load_polygons", "main"]

DEFAULT_GPKG = REPO_ROOT / "data" / "bem4ai" / "decomposition_inputs.gpkg"


# ---------------------------------------------------------------------------
# Figure style (millimetres and points, at print size)
# ---------------------------------------------------------------------------

_FONT_FAMILY = ["TUM Neue Helvetica", "Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]
_PT = 25.4 / 72.0  # mm per point


@dataclass(frozen=True)
class FigureStyle:
    """Sizes and colours. Colours follow the paper figure; text follows the TUM paper-figure style."""

    radius: float = 8.0  # badge radius, mm
    thumb_fill: float = 0.78  # longest side of the thumbnail, as a share of the badge diameter
    caption_gap: float = 1.0
    sibling_gap: float = 3.0
    band: float = 15.0  # vertical room for the edges between two levels, at least
    fan_slope: float = 0.2  # ... and at least this share of the widest fan's horizontal reach
    max_band: float = 60.0
    margin: float = 6.0
    id_size: float = 9.5
    text_size: float = 7.5
    edge_label_size: float = 7.5
    box_title_size: float = 9.5
    box_text_size: float = 8.0
    line_spacing: float = 1.18
    duplicate_radius: float = 6.4
    base_blue: str = "#5AA7C7"
    # Convex pieces; none of them may read as the non-convex blue at thumbnail size.
    pastels: Tuple[str, ...] = (
        "#C7E9C0", "#FDD49E", "#DECBE4", "#FFF2AE", "#FDDAEC",
        "#B3E2CD", "#E5D8BD", "#CBD5E8", "#9DD3E8",
    )
    edge_primary: str = "#4C4C4C"
    edge_diagonal: str = "#1B9E77"
    edge_bisector: str = "#7570B3"
    edge_hole_edges: str = "#64A0C8"
    # Thumbnails: the piece in focus in colour, the rest of the state grey with its cuts as lines,
    # the cut that made the state in TUM orange.
    rest_fill: str = "#E4E4E4"
    rest_line: str = "#4D4D4D"
    chord: str = "#E37222"
    grey: str = "#9C9E9F"
    ring_expanded: str = "#636363"
    ring_parked: str = "#0065BD"
    ring_success: str = "#009456"
    ring_dead_end: str = "#C4071B"
    text_grey: str = "#6E6E6E"


@lru_cache(maxsize=None)
def _font(weight: str = "normal", style: str = "normal"):
    # Cached: a Text copies the properties it is given, so sharing them is safe.
    from matplotlib.font_manager import FontProperties

    return FontProperties(family=_FONT_FAMILY, weight=weight, style=style)


@lru_cache(maxsize=4096)
def _text_width(text: str, size: float, weight: str = "normal", style: str = "normal") -> float:
    """Width of a text run in mm, from the glyph outlines of the font that will draw it."""
    from matplotlib.textpath import TextPath

    if not text:
        return 0.0
    ext = TextPath((0, 0), text, size=size, prop=_font(weight, style)).get_extents()
    trailing_space = 0.28 * size if text.endswith(" ") else 0.0
    return (max(ext.x1, 0.0) - min(ext.x0, 0.0) + trailing_space) * _PT


# A caption line is a list of runs: (text, size, weight, colour).
_Run = Tuple[str, float, str, str]


def _line_width(runs: Sequence[_Run]) -> float:
    return sum(_text_width(text, size, weight) for text, size, weight, _ in runs)


def _line_height(runs: Sequence[_Run], style: FigureStyle) -> float:
    return max(size for _, size, _, _ in runs) * style.line_spacing * _PT


# ---------------------------------------------------------------------------
# Which nodes to draw
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Marker:
    """Stands in for the hidden children of a node: "+N more"."""

    parent: SearchNode
    count: int
    depth: int


def _subtree_size(node: SearchNode, include, until) -> int:
    return sum(1 for n in walk(node) if include(n, until))


def _select_nodes(
    trace: SearchTrace,
    *,
    until: Optional[int],
    max_nodes: Optional[int],
    max_depth: Optional[int],
    show_duplicates: bool,
    max_children: Optional[int] = None,
) -> Dict[Any, List[Any]]:
    """The drawn tree as parent -> visible children (nodes and "+N more" markers).

    The path to the chosen variant is always drawn, depth limit permitting.
    After that the states the search expanded come first, then the rest, each
    in generation order, so a truncated figure shows where the search went
    rather than a sample of what it left lying. `max_children` caps how many children of one state are
    drawn, so a state with fifty ways to cut it does not swallow the figure.
    """

    def include(n: SearchNode, horizon) -> bool:
        if horizon is not None and n.created > horizon:
            return False
        return show_duplicates or not n.is_duplicate

    def depth_ok(n: SearchNode) -> bool:
        return max_depth is None or n.depth <= max_depth

    def expanded(n: SearchNode) -> bool:
        return any(c.created <= (until if until is not None else math.inf) for c in n.children)

    visible: set = {trace.root}
    visible.update(n for n in best_path(trace, until) if depth_ok(n))
    shown_per_parent: Dict[SearchNode, int] = {}
    for n in visible:
        if n.parent is not None:
            shown_per_parent[n.parent] = shown_per_parent.get(n.parent, 0) + 1

    budget = max_nodes if max_nodes is not None else math.inf
    cap = max_children if max_children is not None else math.inf
    in_order = sorted(trace.nodes, key=lambda n: n.created)
    for candidates in ([n for n in in_order if expanded(n)], in_order):
        for n in candidates:
            if len(visible) >= budget:
                break
            if n in visible or not include(n, until) or not depth_ok(n):
                continue
            if n.parent is not None and (n.parent not in visible or shown_per_parent.get(n.parent, 0) >= cap):
                continue
            visible.add(n)
            if n.parent is not None:
                shown_per_parent[n.parent] = shown_per_parent.get(n.parent, 0) + 1

    children: Dict[Any, List[Any]] = {}
    for n in sorted(visible, key=lambda n: n.created):
        shown = [c for c in n.children if c in visible]
        hidden = sum(_subtree_size(c, include, until) for c in n.children if c not in visible and include(c, until))
        if hidden:
            shown.append(_Marker(parent=n, count=hidden, depth=n.depth + 1))
        children[n] = shown
    return children


# ---------------------------------------------------------------------------
# Layout: a tidy tree with captions to the left of the badges
# ---------------------------------------------------------------------------


@dataclass
class _Slot:
    """Where one drawn item sits, and how much room it needs around its centre.

    A state with drawn children has its caption to the left of the badge, out
    of the way of the edges leaving its bottom; a leaf has it underneath, which
    keeps a row of leaves narrow. That is the paper's arrangement too.
    """

    item: Any
    left: float
    right: float
    below: float  # how far the item reaches below its centre
    caption: List[List[_Run]] = field(default_factory=list)
    caption_width: float = 0.0
    caption_below: bool = False
    x: float = 0.0
    y: float = 0.0


def _tidy_x(root: Any, children: Dict[Any, List[Any]], slots: Dict[Any, _Slot], gap: float) -> None:
    """Place every item horizontally, Reingold-Tilford style.

    Subtrees are laid out bottom-up and pushed apart until their contours keep
    `gap` at every level, and each parent is centred over its first and last
    child. Contours use the real extents of the badges and their captions, so
    nothing on a level overlaps.
    """
    rel: Dict[Any, float] = {}

    def contour(item) -> Dict[int, List[float]]:
        slot = slots[item]
        kids = children.get(item, [])
        own = {0: [-slot.left, slot.right]}
        if not kids:
            return own
        merged: Dict[int, List[float]] = {}
        offsets = []
        for kid in kids:
            c = contour(kid)
            if not merged:
                offset = 0.0
            else:
                offset = max(merged[d][1] - c[d][0] + gap for d in c if d in merged)
            for d, (lo, hi) in c.items():
                if d in merged:
                    merged[d][1] = hi + offset
                else:
                    merged[d] = [lo + offset, hi + offset]
            offsets.append(offset)
        middle = 0.5 * (offsets[0] + offsets[-1])
        for kid, offset in zip(kids, offsets):
            rel[kid] = offset - middle
        for d, (lo, hi) in merged.items():
            own[d + 1] = [lo - middle, hi - middle]
        return own

    contour(root)
    slots[root].x = 0.0
    stack = [root]
    while stack:
        item = stack.pop()
        for kid in children.get(item, []):
            slots[kid].x = slots[item].x + rel[kid]
            stack.append(kid)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


class _TreeFigure:
    """Lays out and draws one recorded tree. Coordinates are mm, y pointing down."""

    def __init__(
        self,
        trace: SearchTrace,
        *,
        style: FigureStyle,
        until: Optional[int],
        attempt: Optional[int],
        max_nodes: Optional[int],
        max_depth: Optional[int],
        max_children: Optional[int],
        show_duplicates: bool,
        explain_root: bool,
        source_note: Optional[str],
    ) -> None:
        self.trace = trace
        self.style = style
        self.until = until
        self.attempt = attempt
        self.explain_root = explain_root
        self.source_note = source_note
        self.children = _select_nodes(
            trace, until=until, max_nodes=max_nodes, max_depth=max_depth, show_duplicates=show_duplicates,
            max_children=max_children,
        )
        # The chosen variant's path, as far down as it is drawn.
        self.best_path = {n for n in best_path(trace, until) if n in self.children}
        self.is_convex = trace.engine.is_convex
        self.slots: Dict[Any, _Slot] = {}
        self.colours: Dict[Any, str] = {}

    # -- captions ------------------------------------------------------------

    def _caption(self, node: SearchNode) -> List[List[_Run]]:
        st = self.style
        n_parts, n_nc, n_rv = piece_counts(node.pieces, self.trace.engine)
        status = node_status(node, self.until)
        black = "#000000"
        if node is self.trace.root and self.explain_root:
            # The root spells its numbers out, which the paper uses in place of a key.
            def s(n, one, many):
                return f"{n} {one if n == 1 else many}"

            lines = [
                [(f"S{node.number} ", st.id_size, "bold", black), ("[state 0]", st.id_size, "normal", black)],
                [(f"{n_parts}p ", st.text_size, "bold", black),
                 (f"[{s(n_parts, 'polygon', 'polygons')} in current state]", st.text_size, "normal", black)],
                [(f"{n_nc}nc ", st.text_size, "bold", black),
                 (f"[{s(n_nc, 'remaining non-convex part', 'remaining non-convex parts')}]", st.text_size, "normal", black)],
                [(f"{n_rv}rv ", st.text_size, "bold", black),
                 (f"[{s(n_rv, 'reflex vertex', 'reflex vertices')} in", st.text_size, "normal", black)],
                [("non-convex parts]", st.text_size, "normal", black)],
            ]
            lines += [[(word, st.text_size, "bold", black)] for word in status.lines]
            return lines
        lines = [[(node.name, st.id_size, "bold" if node is self.trace.root else "normal", black)]]
        lines += [[(f"{n_parts}p", st.text_size, "normal", black)],
                  [(f"{n_nc}nc", st.text_size, "normal", black)],
                  [(f"{n_rv}rv", st.text_size, "normal", black)]]
        lines += [[(word, st.text_size, "normal", black)] for word in status.lines]
        return lines

    def _make_slots(self) -> None:
        for item in self.children:
            self._slot_for(item)
            for kid in self.children[item]:
                self._slot_for(kid)

    def _slot_for(self, item) -> _Slot:
        if item in self.slots:
            return self.slots[item]
        st = self.style
        r = st.radius
        if isinstance(item, _Marker):
            w = _text_width(f"+{item.count} more", st.text_size) + 4.0
            slot = _Slot(item, left=w / 2, right=w / 2, below=2.5)
        elif item.is_duplicate:
            rd = st.duplicate_radius
            slot = _Slot(item, left=rd, right=rd, below=rd)
        else:
            caption = self._caption(item)
            width = max(_line_width(line) for line in caption)
            height = sum(_line_height(line, st) for line in caption)
            if self.children.get(item) or item is self.trace.root:
                top = -0.72 * r
                slot = _Slot(item, left=r + st.caption_gap + width, right=r, below=max(r, top + height),
                             caption=caption, caption_width=width)
            else:
                half = max(r, width / 2)
                slot = _Slot(item, left=half, right=half, below=r + st.caption_gap + height,
                             caption=caption, caption_width=width, caption_below=True)
        self.slots[item] = slot
        return slot

    def layout(self) -> None:
        st = self.style
        self._make_slots()
        root = self.trace.root
        _tidy_x(root, self.children, self.slots, st.sibling_gap)
        # Levels: each level is as deep as its deepest caption, then the edge band.
        # A wide fan gets a taller band: nearly flat edges run almost parallel
        # and leave no room between them for their labels.
        by_depth: Dict[int, List[_Slot]] = {}
        for item, slot in self.slots.items():
            by_depth.setdefault(item.depth, []).append(slot)
        spread: Dict[int, float] = {}
        for parent, kids in self.children.items():
            for kid in kids:
                dx = abs(self.slots[kid].x - self.slots[parent].x)
                spread[parent.depth] = max(spread.get(parent.depth, 0.0), dx)
        self.level_y: Dict[int, float] = {}
        self.level_port: Dict[int, float] = {}
        y = 0.0
        for d in sorted(by_depth):
            self.level_y[d] = y
            below = max(s.below for s in by_depth[d])
            self.level_port[d] = y + below + 1.0
            band = min(max(st.band, st.fan_slope * spread.get(d, 0.0)), st.max_band)
            y = y + below + band + st.radius
        for item, slot in self.slots.items():
            slot.y = self.level_y[item.depth]
        self._place_ports()

    def _place_ports(self) -> None:
        """Where each parent's edges fan out: as high under the badge as they can.

        Edges leave a badge straight down and fan out from a port. The port
        starts at the bottom of the badge and moves down until no edge of the
        fan crosses a badge or caption on the parent's level, its own caption
        included. Below the level's deepest caption nothing is in the way, so
        the search for a port always ends.
        """
        st = self.style
        r = st.radius
        shapes_by_depth: Dict[int, List[Tuple[Any, Any]]] = {}
        for item, slot in self.slots.items():
            for shape_ in self._footprint(item, slot):
                shapes_by_depth.setdefault(item.depth, []).append((item, shape_))
        self.port: Dict[Any, float] = {}
        for parent, kids in self.children.items():
            if not kids:
                continue
            ps = self.slots[parent]
            obstacles = [s for item, s in shapes_by_depth[parent.depth] if item is not parent]
            obstacles += self._footprint(parent, ps)[1:]  # its own caption, not its badge
            tree = STRtree(obstacles) if obstacles else None
            y, y_max = ps.y + r, self.level_port[parent.depth]
            while y < y_max:
                fan = [LineString([(ps.x, y), self._entry(kid, (ps.x, y))]) for kid in kids]
                if tree is None or not any(len(tree.query(seg, predicate="intersects")) for seg in fan):
                    break
                y += 0.5
            self.port[parent] = min(y, y_max)

    def _footprint(self, item, slot: _Slot) -> List[Any]:
        """The badge (first) and the caption of a drawn item, as rectangles."""
        st = self.style
        r = st.radius
        if isinstance(item, _Marker):
            return [shapely_box(slot.x - slot.left, slot.y - 2.5, slot.x + slot.right, slot.y + 2.5)]
        if item.is_duplicate:
            rd = st.duplicate_radius
            return [shapely_box(slot.x - rd, slot.y - rd, slot.x + rd, slot.y + rd)]
        badge = shapely_box(slot.x - r, slot.y - r, slot.x + r, slot.y + r)
        if slot.caption_below:
            caption = shapely_box(slot.x - slot.caption_width / 2, slot.y + r, slot.x + slot.caption_width / 2,
                                  slot.y + slot.below)
        else:
            caption = shapely_box(slot.x - slot.left, slot.y - 0.72 * r, slot.x - r - 0.2, slot.y + slot.below)
        return [badge, caption]

    def _entry(self, child, port: Tuple[float, float]) -> Tuple[float, float]:
        """Where an edge from `port` meets the child: the badge top, at most 40 degrees off vertical.

        Entering from above keeps every incoming edge clear of the caption
        beside or under the badge.
        """
        st = self.style
        cs = self.slots[child]
        if isinstance(child, _Marker):
            return (cs.x, cs.y - 2.5)
        rc = st.duplicate_radius if child.is_duplicate else st.radius
        angle = math.atan2(port[0] - cs.x, cs.y - port[1])
        angle = max(-math.radians(40), min(math.radians(40), angle))
        return (cs.x + rc * math.sin(angle), cs.y - rc * math.cos(angle))

    # -- thumbnails ----------------------------------------------------------

    def _thumb_transform(self):
        """One transform for every thumbnail, from the input's bounds: same scale and orientation throughout."""
        st = self.style
        minx, miny, maxx, maxy = self.trace.polygon.bounds
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        scale = st.thumb_fill * 2 * st.radius / max(maxx - minx, maxy - miny, 1e-9)
        return cx, cy, scale

    def _colour_pieces(self) -> None:
        """One colour per convex piece, kept wherever the piece reappears.

        A convex piece is never cut again, so it survives unchanged down a
        branch; giving it one colour lets the eye follow it from node to node.
        """
        st = self.style
        usage: Dict[str, int] = {c: 0 for c in st.pastels}
        for item in sorted((i for i in self.slots if isinstance(i, SearchNode)), key=lambda n: n.created):
            in_node = set()
            for piece in item.pieces:
                if not self.is_convex(piece):
                    continue
                k = _piece_id(piece)
                if k in self.colours:
                    in_node.add(self.colours[k])
            for piece in item.pieces:
                if not self.is_convex(piece):
                    continue
                k = _piece_id(piece)
                if k in self.colours:
                    continue
                free = [c for c in st.pastels if c not in in_node]
                colour = min(free or st.pastels, key=lambda c: usage[c])
                self.colours[k] = colour
                usage[colour] += 1
                in_node.add(colour)

    def _thumbnail(self, node: SearchNode, x: float, y: float):
        """A badge's picture: (fill patches, chord lines, vertex marks), in badge coordinates.

        The piece in focus is in colour (non-convex blue, convex pastel); the
        rest of the state is grey with every edge drawn, so the cuts made so
        far stay visible.
        """
        from matplotlib.path import Path as MplPath

        st = self.style
        cx, cy, scale = self._thumb_transform()

        def to_badge(px, py):
            return x + (px - cx) * scale, y - (py - cy) * scale

        def path(piece):
            oriented = orient(piece, 1.0)
            verts, codes = [], []
            for ring in [oriented.exterior] + list(oriented.interiors):
                pts = [to_badge(px, py) for px, py in ring.coords]
                verts += pts
                codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 2) + [MplPath.CLOSEPOLY]
            return MplPath(verts, codes)

        coloured, starts = focus(node, self.trace.root, self.until)
        rest = [p for p in node.pieces if not any(p is q for q in coloured)]
        patches = [(path(p), st.rest_fill, st.rest_line, 0.35) for p in rest]
        for p in coloured:
            fill = self.colours.get(_piece_id(p), st.pastels[0]) if self.is_convex(p) else st.base_blue
            patches.append((path(p), fill, "black", 0.6))
        chord = incoming_chord(node)
        lines = [[to_badge(px, py) for px, py in g.coords]
                 for g in getattr(chord, "geoms", [chord]) if chord is not None and g.length > 0]
        marks = [to_badge(px, py) for px, py in starts]
        return patches, lines, marks

    # -- edges ---------------------------------------------------------------

    def _edge_geometry(self, parent: SearchNode, child) -> List[Tuple[float, float]]:
        """The fanning part of an edge, from the parent's port to the child."""
        port = (self.slots[parent].x, self.port[parent])
        return [port, self._entry(child, port)]

    def _stub_style(self, parent: SearchNode) -> Optional[Dict[str, Any]]:
        """The style of the shared stub from a badge down to its port: that of its strongest edge."""
        kids = self.children.get(parent, [])
        on_path = [k for k in kids if k in self.best_path]
        if on_path:
            return self._edge_style(on_path[0])
        real = [k for k in kids if isinstance(k, SearchNode) and not k.is_duplicate]
        if real:
            s = self._edge_style(real[0])
            return {**s, "color": self.style.edge_primary, "lw": 1.0} if len({k.cut_kind for k in real}) > 1 else s
        return self._edge_style(kids[0]) if kids else None

    def _edge_style(self, child) -> Dict[str, Any]:
        st = self.style
        if isinstance(child, _Marker):
            return {"color": st.grey, "lw": 0.9, "ls": (0, (1.0, 1.6))}
        if child.is_duplicate:
            return {"color": st.grey, "lw": 1.0, "ls": (0, (4.0, 2.5))}
        colour = {"bisector": st.edge_bisector, "diagonal": st.edge_diagonal,
                  "hole_edge_split": st.edge_hole_edges}.get(child.cut_kind, st.edge_primary)
        lw = 1.6 if child.cut_kind in ("bisector", "diagonal", "hole_edge_split") else 1.0
        if child in self.best_path:
            lw = 4.0
        return {"color": colour, "lw": lw, "ls": "-"}

    # -- drawing -------------------------------------------------------------

    def draw(self):
        from matplotlib import patheffects as pe
        from matplotlib.figure import Figure
        from matplotlib.patches import Circle, FancyBboxPatch

        st = self.style
        r = st.radius
        self.layout()
        self._colour_pieces()

        # Everything placed so far, for fitting the boxes into free corners.
        items = [shape_ for item, slot in self.slots.items() for shape_ in self._footprint(item, slot)]

        stubs = []
        for parent, kids in self.children.items():
            ps = self.slots[parent]
            if kids and self.port[parent] > ps.y + r + 0.2:
                stubs.append((parent, [(ps.x, ps.y + r), (ps.x, self.port[parent])]))
                items.append(LineString(stubs[-1][1]).buffer(0.6))
        edges = []
        for parent, kids in self.children.items():
            for kid in kids:
                points = self._edge_geometry(parent, kid)
                edges.append((parent, kid, points))
                items.append(LineString(points).buffer(0.6))

        labels = []
        nodes_tree = STRtree([s for item, slot in self.slots.items() for s in self._footprint(item, slot)])
        for _, kid, points in edges:
            if isinstance(kid, _Marker) or not kid.cut:
                continue
            placement = self._label_placement(points, kid.cut, self._edge_style(kid)["lw"], nodes_tree,
                                              [lab["shape"] for _, lab in labels])
            labels.append((kid, placement))
            items.append(placement["shape"])

        tree_bounds = _bounds_of(items)
        boxes = self._boxes()
        placed = _place_boxes(boxes, items, tree_bounds, margin=st.margin)

        all_bounds = _bounds_of(items + [shapely_box(*b["rect"]) for b in placed])
        x0, y0, x1, y1 = all_bounds
        x0 -= st.margin
        y0 -= st.margin
        x1 += st.margin
        y1 += st.margin
        width, height = x1 - x0, y1 - y0

        fig = Figure(figsize=(width / 25.4, height / 25.4))
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(x0, x1)
        ax.set_ylim(y1, y0)
        ax.set_aspect("equal")
        ax.axis("off")
        halo = [pe.withStroke(linewidth=2.6, foreground="white")]

        # Edges: the chosen path last, so it lies on top where edges cross.
        for parent, points in stubs:
            s = self._stub_style(parent)
            xs, ys = zip(*points)
            ax.plot(xs, ys, color=s["color"], lw=s["lw"], ls=s["ls"], solid_capstyle="butt",
                    zorder=1.5 if parent in self.best_path else 1)
        for parent, kid, points in sorted(edges, key=lambda e: e[1] in self.best_path):
            s = self._edge_style(kid)
            if kid in self.best_path:
                # One polyline from the badge, so the thick line has a clean joint at the port.
                points = [(self.slots[parent].x, self.slots[parent].y + r)] + points
            xs, ys = zip(*points)
            ax.plot(xs, ys, color=s["color"], lw=s["lw"], ls=s["ls"], solid_capstyle="butt", solid_joinstyle="round",
                    zorder=1.5 if kid in self.best_path else 1)
        for kid, lab in labels:
            colour = "#000000" if not kid.is_duplicate else st.text_grey
            ax.text(lab["x"], lab["y"], kid.cut, rotation=lab["rotation"], rotation_mode="anchor", ha="center",
                    va="center", fontsize=st.edge_label_size, color=colour, fontproperties=_font(), zorder=6,
                    path_effects=halo)

        for item, slot in self.slots.items():
            if isinstance(item, _Marker):
                text = f"+{item.count} more"
                w = slot.left + slot.right
                ax.add_patch(FancyBboxPatch((slot.x - w / 2, slot.y - 2.2), w, 4.4,
                                            boxstyle="round,pad=0,rounding_size=2.2", fc="white", ec=st.grey,
                                            lw=0.8, ls=(0, (1.0, 1.6)), zorder=2))
                ax.text(slot.x, slot.y, text, ha="center", va="center", fontsize=st.text_size,
                        color=st.text_grey, fontproperties=_font(), zorder=5)
                continue
            if item.is_duplicate:
                of = item.duplicate_of
                while of is not None and of.is_duplicate:  # a sibling cut that was itself a duplicate
                    of = of.duplicate_of
                ax.add_patch(Circle((slot.x, slot.y), st.duplicate_radius, fc="white", ec=st.grey, lw=1.2,
                                    ls=(0, (3.0, 2.0)), zorder=2))
                ax.text(slot.x, slot.y - (1.3 if of is not None else 0), "duplicate", ha="center",
                        va="center", fontsize=7.0, color=st.text_grey, fontproperties=_font(), zorder=5)
                if of is not None:
                    ax.text(slot.x, slot.y + 1.6, f"of {of.name}", ha="center", va="center",
                            fontsize=7.0, color=st.text_grey, fontproperties=_font(), zorder=5)
                continue
            self._draw_badge(ax, item, slot)
            self._draw_caption(ax, slot)

        for b in placed:
            b["draw"](ax, b["rect"])

        if self.trace.notes:
            ax.text(x0 + 2, y1 - 2, "; ".join(self.trace.notes), fontsize=7.0, color=st.ring_dead_end,
                    va="bottom", fontproperties=_font())
        return fig

    def _draw_badge(self, ax, node: SearchNode, slot: _Slot) -> None:
        from matplotlib.patches import Circle, PathPatch

        st = self.style
        r = st.radius
        status = node_status(node, self.until)
        ring = {
            "success": dict(ec=st.ring_success, lw=2.2, ls="-"),
            "dead_end": dict(ec=st.ring_dead_end, lw=2.2, ls="-"),
            "merged": dict(ec=st.grey, lw=1.5, ls=(0, (3.0, 2.0))),
            "parked": dict(ec=st.ring_parked, lw=1.6, ls=(0, (3.2, 2.0))),
            "expanded": dict(ec=st.ring_expanded, lw=1.6, ls="-"),
            "active": dict(ec=st.ring_expanded, lw=1.0, ls=(0, (1.0, 1.5))),
        }[status.ring]
        ax.add_patch(Circle((slot.x, slot.y), r, fc="white", ec="none", zorder=2))
        ax.add_patch(Circle((slot.x, slot.y), r, fill=False, zorder=3, **ring))
        patches, chord, marks = self._thumbnail(node, slot.x, slot.y)
        for path, fill, outline, lw in patches:
            ax.add_patch(PathPatch(path, fc=fill, ec=outline, lw=lw, joinstyle="miter", zorder=4))
        for line in chord:
            xs, ys = zip(*line)
            ax.plot(xs, ys, color=st.chord, lw=1.4, solid_capstyle="round", zorder=4.6)
        if marks:
            xs, ys = zip(*marks)
            ax.plot(xs, ys, "o", ms=2.4, mfc="white", mec="black", mew=0.5, zorder=4.8)

    def _draw_caption(self, ax, slot: _Slot) -> None:
        st = self.style
        r = st.radius
        if slot.caption_below:
            centre = slot.x
            y = slot.y + r + st.caption_gap
        else:
            centre = slot.x - r - st.caption_gap - slot.caption_width / 2
            y = slot.y - 0.72 * r
        for line in slot.caption:
            h = _line_height(line, st)
            x = centre - _line_width(line) / 2
            for text, size, weight, colour in line:
                ax.text(x, y + h / 2, text, ha="left", va="center", fontsize=size, color=colour,
                        fontproperties=_font(weight), zorder=5)
                x += _text_width(text, size, weight)
            y += h

    def _label_placement(self, points, text: str, line_width: float, avoid: Optional[STRtree] = None,
                         placed=()) -> Dict[str, Any]:
        """Edge labels sit along the fanning segment, above the line and close to the child.

        Near the parent the edges of siblings converge and their labels would
        collide; near the children they are a node's width apart. Where shallow
        edges still crowd them, a label slides back along its edge until it is
        clear of the labels already placed and of every badge and caption.
        """
        st = self.style
        (ax_, ay), (bx, by) = points[-2], points[-1]
        dx, dy = bx - ax_, by - ay
        length = math.hypot(dx, dy) or 1.0
        ux, uy = dx / length, dy / length
        w = _text_width(text, st.edge_label_size)
        h = st.edge_label_size * _PT
        nx, ny = uy, -ux  # one of the two normals; take the one pointing up the page
        if ny > 0 or (abs(ny) < 1e-9 and nx < 0):
            nx, ny = -nx, -ny
        offset = 0.5 * h + 0.6 + 0.5 * line_width * _PT  # clear of the line, however thick it is drawn
        rotation = math.degrees(math.atan2(-dy, dx))
        if rotation > 90:
            rotation -= 180
        elif rotation <= -90:
            rotation += 180
        # Screen rotation is counter-clockwise with y up; data y points down.
        local = affinity.rotate(shapely_box(-w / 2 - 0.3, -h / 2 - 0.2, w / 2 + 0.3, h / 2 + 0.2), -rotation, origin=(0, 0))

        first = min(0.5 * w + 2.5, 0.55 * length)
        backs = [first] + [b for b in np.arange(first + 2.0, 0.85 * length - 0.5 * w, 2.0)]
        best = None
        for back in backs:
            cx, cy = bx - ux * back + nx * offset, by - uy * back + ny * offset
            rect = affinity.translate(local, cx, cy)
            clash = sum(rect.intersection(p).area for p in placed if rect.intersects(p))
            if avoid is not None:
                clash += sum(1.0 for _ in avoid.query(rect, predicate="intersects"))
            if best is None or clash < best[0]:
                best = (clash, cx, cy, rect)
            if clash == 0:
                break
        _, cx, cy, rect = best
        return {"x": cx, "y": cy, "rotation": rotation, "shape": rect}

    # -- boxes ----------------------------------------------------------------

    def _boxes(self) -> List[Dict[str, Any]]:
        return [
            self._properties_box(),
            self._stats_box(),
            self._legend_box(),
        ]

    def _rows_box(self, title: str, rows: List[Tuple[str, str]], corners: Tuple[str, ...], extra=None) -> Dict[str, Any]:
        st = self.style
        lh = st.box_text_size * st.line_spacing * _PT
        th = st.box_title_size * st.line_spacing * _PT
        key_w = max((_text_width(k, st.box_text_size) for k, _ in rows), default=0.0) + 2.0
        val_w = max((_text_width(v, st.box_text_size) for _, v in rows), default=0.0)
        width = max(_text_width(title, st.box_title_size, "bold"), key_w + val_w)
        extra_w, extra_h = (extra["size"] if extra else (0.0, 0.0))
        width = max(width, extra_w)
        height = th + lh * len(rows) + (extra_h + 1.5 if extra else 0.0)

        def draw(ax, rect):
            x, y = rect[0], rect[1]
            ax.text(x, y + th / 2, title, ha="left", va="center", fontsize=st.box_title_size,
                    fontproperties=_font("bold"), zorder=7)
            yy = y + th
            for k, v in rows:
                ax.text(x, yy + lh / 2, k, ha="left", va="center", fontsize=st.box_text_size, fontproperties=_font(), zorder=7)
                ax.text(x + key_w, yy + lh / 2, v, ha="left", va="center", fontsize=st.box_text_size,
                        fontproperties=_font(), zorder=7)
                yy += lh
            if extra:
                extra["draw"](ax, x, yy + 1.5)

        return {"size": (width, height), "corners": corners, "draw": draw}

    def _properties_box(self) -> Dict[str, Any]:
        poly = self.trace.polygon
        rows = [
            ("area", f"= {poly.area:.1f} m²"),
            ("vertices", f"= {len(self.trace.root_piece.exterior.coords) - 1}"),
            ("holes", f"= {len(poly.interiors)}"),
        ]
        if self.source_note:
            rows.append(("input", f"= {self.source_note}"))
        return self._rows_box("Polygon properties:", rows, ("top-right", "top-left"), extra=self._vertex_key())

    def _vertex_key(self) -> Dict[str, Any]:
        """The polygon with its vertices numbered as the first cuts' edge labels use them.

        Reflex vertices are r0, r1, ... on the ring as the search starts from
        it; the first hole's vertices are h0, h1, ... in the engine's own order
        (on the polygon oriented counter-clockwise, hole clockwise).
        """
        st = self.style
        polygons = [self.trace.root_piece]
        reflex_of = []
        for poly in polygons:
            coords = list(poly.exterior.coords)[:-1]
            n = len(coords)
            area2 = sum(coords[i][0] * coords[(i + 1) % n][1] - coords[(i + 1) % n][0] * coords[i][1] for i in range(n))
            reflex = []
            for i in range(n):
                (x0, y0), (x1, y1), (x2, y2) = coords[i - 1], coords[i], coords[(i + 1) % n]
                cross = (x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1)
                if cross * (1 if area2 > 0 else -1) < 0:
                    reflex.append((coords, i, "r"))
            if poly.interiors:
                hole = list(orient(poly, 1.0).interiors[0].coords)[:-1]
                reflex += [(hole, i, "h") for i in range(len(hole))]
            reflex_of.append(reflex)
        minx, miny, maxx, maxy = _bounds_of(polygons)
        size = 34.0
        scale = size / max(maxx - minx, maxy - miny, 1e-9)
        w, h = (maxx - minx) * scale, (maxy - miny) * scale
        pad = 3.5

        def draw(ax, x, y):
            from matplotlib import patheffects as pe
            from matplotlib.patches import PathPatch
            from matplotlib.path import Path as MplPath

            ox, oy = x + pad, y + pad

            def tf(px, py):
                return ox + (px - minx) * scale, oy + (maxy - py) * scale

            drawn = []
            for poly in polygons:
                shape_ = orient(poly, 1.0)
                verts, codes = [], []
                for ring in [shape_.exterior] + list(shape_.interiors):
                    pts = [tf(px, py) for px, py in ring.coords]
                    verts += pts
                    codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 2) + [MplPath.CLOSEPOLY]
                ax.add_patch(PathPatch(MplPath(verts, codes), fc=st.base_blue, ec="black", lw=0.6, zorder=7))
                drawn.append(affinity.affine_transform(poly, [scale, 0, 0, -scale, ox - minx * scale, oy + maxy * scale]))
            drawn_union = shapely.union_all(drawn)
            taken: List[Any] = []
            for coords, i, prefix in [item for reflex in reflex_of for item in reflex]:
                n = len(coords)
                (x0, y0), (x1, y1), (x2, y2) = coords[i - 1], coords[i], coords[(i + 1) % n]
                a = np.array([x0 - x1, y0 - y1]) / max(math.hypot(x0 - x1, y0 - y1), 1e-12)
                b = np.array([x2 - x1, y2 - y1]) / max(math.hypot(x2 - x1, y2 - y1), 1e-12)
                # At a reflex vertex, and at a hole's corner, the short way between
                # the edges points off the floor: outside, or into the hole.
                out = a + b
                if np.linalg.norm(out) < 1e-9:
                    out = np.array([-a[1], a[0]])
                out = out / np.linalg.norm(out)
                px, py = tf(x1, y1)
                ax.plot([px], [py], "o", ms=2.2, color=st.ring_parked, zorder=8)
                text = f"{prefix}{i}"
                lw_, lh_ = _text_width(text, 7.0), 7.0 * _PT
                # Try the outward direction first, then turn and step away until
                # the label clears the polygon and the labels already placed.
                spot = None
                for dist in (2.4, 3.4, 4.6):
                    for turn in (0, 35, -35, 70, -70, 110, -110):
                        c, s_ = math.cos(math.radians(turn)), math.sin(math.radians(turn))
                        dxo, dyo = out[0] * c - out[1] * s_, out[0] * s_ + out[1] * c
                        cx, cy = px + dxo * dist, py - dyo * dist
                        rect = shapely_box(cx - lw_ / 2, cy - lh_ / 2, cx + lw_ / 2, cy + lh_ / 2)
                        if any(rect.intersects(t) for t in taken) or rect.intersection(drawn_union).area > 0.25 * rect.area:
                            continue
                        spot = (cx, cy, rect)
                        break
                    if spot:
                        break
                if spot is None:
                    spot = (px + out[0] * 2.4, py - out[1] * 2.4, None)
                cx, cy, rect = spot
                if rect is not None:
                    taken.append(rect.buffer(0.5))
                ax.text(cx, cy, text, ha="center", va="center", fontsize=7.0,
                        color=st.ring_parked, fontproperties=_font(), zorder=8,
                        path_effects=[pe.withStroke(linewidth=2, foreground="white")])

        return {"size": (w + 2 * pad, h + 2 * pad), "draw": draw}

    def _stats_box(self) -> Dict[str, Any]:
        s = self.trace.stats
        if self.attempt is not None:
            a = self.trace.attempts[self.attempt - 1]
            rows = [
                ("attempt", f"= {self.attempt} of {len(self.trace.attempts)}"),
                ("successes", f"= {a['n_variants']}"),
                ("depth", f"= {a['depth']}"),
                ("width", f"= {a['width']}"),
            ]
        else:
            terminated = str(s.get("search_terminated_by", "")).replace("_", " ")
            rows = [
                ("successes", f"= {s.get('n_variants', 0)}"),
                ("failed", f"= {s.get('n_failed_terminal_states', 0)}"),
                ("terminated", f"= {terminated}"),
                ("depth", f"= {s.get('search_depth_used')}"),
                ("width", f"= {s.get('search_width_used')}"),
                ("attempts", f"= {s.get('search_attempt_count', 0)}"),
                ("states", f"= {s.get('n_states_seen', 0)} seen, {s.get('search_total_states', 0)} processed"),
            ]
            n_duplicates = sum(1 for n in self.trace.nodes if n.is_duplicate)
            if n_duplicates:
                rows.append(("duplicates", f"= {n_duplicates} cuts led to a state already reached"))
            if self.trace.best_score is not None:
                rows.insert(0, ("chosen", f"= {s.get('n_parts')} zones, score {self.trace.best_score:.2f}"))
            if not s.get("convex_success", True):
                rows.insert(0, ("result", "= no convex decomposition"))
        rows.append(("cuts", f"= {self.trace.engine.cut_source}"))
        n_total = sum(1 for n in self.trace.nodes if not n.is_duplicate and (self.until is None or n.created <= self.until))
        n_shown = sum(1 for i in self.slots if isinstance(i, SearchNode) and not i.is_duplicate)
        if n_shown < n_total:
            rows.append(("drawn", f"= {n_shown} of {n_total} states"))
        return self._rows_box("Decomposition statistics:", rows, ("bottom-right", "bottom-left", "top-right"))

    def _legend_box(self) -> Dict[str, Any]:
        st = self.style
        size = st.box_text_size
        lh = size * st.line_spacing * _PT
        th = st.box_title_size * st.line_spacing * _PT
        sym_w = 12.0
        used = {n.cut_kind for n in self.trace.nodes}
        rows: List[Tuple[str, Any, List[str]]] = [
            ("text", ("Si", "italic"), ["search state i, numbered in generation order"]),
            ("text", ("p", "normal"), ["number of polygon parts", "in the current state"]),
            ("text", ("nc", "normal"), ["number of remaining", "non-convex parts"]),
            ("text", ("rv", "normal"), ["total number of reflex vertices", "in the remaining non-convex parts",
                                        "(the corners of a hole count too)"]),
            ("text", ("ri prev", "normal"), ["cut extends the incoming edge", "at reflex vertex i of the cut piece"]),
            ("text", ("ri next", "normal"), ["cut extends the outgoing edge"]),
            ("text", ("ri bis", "normal"), ["cut along the angle bisector at i"]),
            ("text", ("ri–rj", "normal"), ["diagonal between vertices i and j"]),
        ]
        if "opening" in used:
            rows.append(("text", ("hi + hj", "normal"),
                         ["opens a hole: chords from its vertices", "i and j to the outer ring (prev, next,",
                          "bis as above; touch: vertex on the ring)"]))
        rows += [
            ("line", self._edge_style_sample("primary"), ["primary split"]),
            ("line", self._edge_style_sample("diagonal"), ["diagonal fallback"]),
            ("line", self._edge_style_sample("bisector"), ["bisector fallback"]),
        ]
        if "hole_edge_split" in used:
            rows.append(("line", self._edge_style_sample("hole_edge_split"),
                         ["hole-edge split fallback", "(no pair of chords opens the hole)"]))
        rows += [
            ("line", self._edge_style_sample("duplicate"), ["duplicate / merged branch"]),
            ("line", dict(color=st.edge_primary, lw=4.0, ls="-"), ["path to the chosen variant"]),
            ("ring", dict(ec=st.ring_success, lw=2.2, ls="-"), ["successful state"]),
            ("ring", dict(ec=st.ring_dead_end, lw=2.2, ls="-"), ["terminal dead-end"]),
            ("ring", dict(ec=st.ring_parked, lw=1.6, ls=(0, (2.2, 1.4))), ["overflow / deferred state"]),
            ("ring", dict(ec=st.ring_expanded, lw=1.6, ls="-"), ["expanded state"]),
            # What the thumbnails show (see search_trace.focus).
            ("swatch", dict(fills=[st.base_blue], edge="black", lw=0.6, divider=None),
             ["piece the search cuts next", "from this state"]),
            ("swatch", dict(fills=[st.base_blue, st.pastels[1]], edge="black", lw=0.6, divider=st.chord),
             ["nothing cut from this state: the two", "pieces its cut made (pastel = convex)"]),
            ("swatch", dict(fills=[st.rest_fill], edge=st.rest_line, lw=0.35, divider=st.rest_line),
             ["rest of the state, earlier cuts as lines"]),
            ("line", dict(color=st.chord, lw=1.4, ls="-"), ["the cut that made this state"]),
            ("mark", None, ["vertices the next cuts start from"]),
        ]
        key_w = max(_text_width(k[0], size, "normal", k[1]) for kind, k, _ in rows if kind == "text") + 3.0
        sym_w = max(sym_w, key_w)
        text_w = max(_text_width(t, size) for _, _, lines in rows for t in lines)
        width = sym_w + text_w
        height = th + sum(lh * len(lines) + 0.6 for _, _, lines in rows)

        def draw(ax, rect):
            from matplotlib.patches import Circle, Rectangle

            x, y = rect[0], rect[1]
            ax.text(x, y + th / 2, "Legend", ha="left", va="center", fontsize=st.box_title_size,
                    fontproperties=_font("bold"), zorder=7)
            yy = y + th
            for kind, key, lines in rows:
                mid = yy + lh / 2
                if kind == "text":
                    ax.text(x, mid, key[0], ha="left", va="center", fontsize=size, fontproperties=_font("normal", key[1]), zorder=7)
                elif kind == "line":
                    ax.plot([x, x + 8.0], [mid, mid], color=key["color"], lw=min(key["lw"], 3.0), ls=key["ls"],
                            solid_capstyle="butt", zorder=7)
                elif kind == "swatch":
                    # One or two fills side by side, with a line between them.
                    w = 8.0 / len(key["fills"])
                    for i, fill in enumerate(key["fills"]):
                        ax.add_patch(Rectangle((x + i * w, mid - 1.6), w, 3.2, fc=fill, ec=key["edge"],
                                               lw=key["lw"], zorder=7))
                    if key["divider"]:
                        ax.plot([x + 4.0, x + 4.0], [mid - 1.6, mid + 1.6], color=key["divider"],
                                lw=1.4 if key["divider"] == st.chord else key["lw"], zorder=7.5)
                elif kind == "mark":
                    ax.add_patch(Rectangle((x, mid - 1.6), 8.0, 3.2, fc=st.base_blue, ec="black", lw=0.6, zorder=7))
                    ax.plot([x + 2.0, x + 6.0], [mid, mid], "o", ms=2.4, mfc="white", mec="black", mew=0.5,
                            zorder=7.5)
                else:
                    ax.add_patch(Circle((x + 2.4, mid), 1.9, fill=False, zorder=7, **key))
                for line in lines:
                    ax.text(x + sym_w, yy + lh / 2, line, ha="left", va="center", fontsize=size,
                            fontproperties=_font(), zorder=7)
                    yy += lh
                yy += 0.6

        return {"size": (width, height), "corners": ("bottom-left", "top-left", "bottom-right"), "draw": draw}

    def _edge_style_sample(self, kind: str) -> Dict[str, Any]:
        st = self.style
        if kind == "duplicate":
            return {"color": st.grey, "lw": 1.0, "ls": (0, (4.0, 2.5))}
        colour = {"bisector": st.edge_bisector, "diagonal": st.edge_diagonal,
                  "hole_edge_split": st.edge_hole_edges}.get(kind, st.edge_primary)
        return {"color": colour, "lw": 1.6 if kind != "primary" else 1.0, "ls": "-"}


def _piece_id(piece: Polygon) -> Tuple:
    coords = frozenset((round(x, 3), round(y, 3)) for x, y in piece.exterior.coords)
    return coords, len(piece.interiors)


def _bounds_of(shapes: Sequence[Any]) -> Tuple[float, float, float, float]:
    xs0, ys0, xs1, ys1 = zip(*(s.bounds for s in shapes))
    return min(xs0), min(ys0), max(xs1), max(ys1)


def _place_boxes(boxes, obstacles, bounds, *, margin: float) -> List[Dict[str, Any]]:
    """Put each box into a free corner of the tree, or beside it when no corner is free.

    Boxes go in order and each one placed becomes an obstacle for the next, so
    they never overlap one another either.
    """
    placed = []
    shapes = list(obstacles)
    x0, y0, x1, y1 = bounds
    side_y = y0
    for b in boxes:
        w, h = b["size"]
        tree = STRtree(shapes)
        rect = None
        for corner in b["corners"]:
            bx = x0 if "left" in corner else x1 - w
            by = y0 if "top" in corner else y1 - h
            candidate = shapely_box(bx - 1.5, by - 1.5, bx + w + 1.5, by + h + 1.5)
            if not len(tree.query(candidate, predicate="intersects")):
                rect = (bx, by, bx + w, by + h)
                break
        if rect is None:
            bx = x1 + margin
            rect = (bx, side_y, bx + w, side_y + h)
            side_y += h + margin
        shapes.append(shapely_box(*rect))
        placed.append({"rect": rect, "draw": b["draw"]})
    return placed


def draw_search_tree(
    trace: SearchTrace,
    *,
    attempt: Optional[int] = None,
    max_nodes: Optional[int] = 60,
    max_depth: Optional[int] = None,
    max_children: Optional[int] = 12,
    show_duplicates: bool = True,
    explain_root: bool = True,
    source_note: Optional[str] = None,
    style: Optional[FigureStyle] = None,
):
    """Draw a recorded search as a `matplotlib.figure.Figure`, sized in millimetres.

    `attempt` draws the tree as it stood when that budget attempt ended (the
    search is persistent, so each attempt extends the previous tree); None
    draws the whole run. `max_nodes`, `max_depth` and `max_children` (per
    state) truncate a large tree, replacing what is cut off with a "+N more"
    marker under its parent; the path to the chosen variant is always kept.
    None lifts a limit.
    """
    figure = _TreeFigure(
        trace,
        style=style or FigureStyle(),
        until=trace.snapshot_seq(attempt),
        attempt=attempt,
        max_nodes=max_nodes,
        max_depth=max_depth,
        max_children=max_children,
        show_duplicates=show_duplicates,
        explain_root=explain_root,
        source_note=source_note,
    )
    return figure.draw()


# ---------------------------------------------------------------------------
# Input polygons
# ---------------------------------------------------------------------------


def _polygons_of(geom: Any) -> List[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [p for g in getattr(geom, "geoms", []) for p in _polygons_of(g)]


def load_polygons(source: str) -> List[Polygon]:
    """Every polygon in a WKT string, or in a WKT, GeoJSON or WKB file.

    Multi-part geometries and feature collections are flattened in file order.
    """
    text = source.strip()
    if text.upper().startswith(("POLYGON", "MULTIPOLYGON", "GEOMETRYCOLLECTION")):
        return _polygons_of(shapely_wkt.loads(text))

    path = Path(source).expanduser()
    suffix = path.suffix.lower()
    if suffix in (".geojson", ".json"):
        data = json.loads(path.read_text())
        if data.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in data["features"] if f.get("geometry")]
        elif data.get("type") == "Feature":
            geoms = [shape(data["geometry"])]
        else:
            geoms = [shape(data)]
        return [p for g in geoms for p in _polygons_of(g)]
    if suffix == ".wkb":
        raw = path.read_bytes()
        try:  # hex text, as shapely and PostGIS write it
            geom = shapely_wkb.loads(raw.decode("ascii").strip(), hex=True)
        except Exception:  # binary
            geom = shapely_wkb.loads(raw)
        return _polygons_of(geom)
    return _polygons_of(shapely_wkt.loads(path.read_text()))


def gpkg_polygon(sample_id: str, input_index: int = 0, path: Path | str = DEFAULT_GPKG) -> Polygon:
    """One BEM4AI decomposition input: building `sample_id`, its input `input_index`.

    The GeoPackage holds the polygons BEM4AI notebook 4 hands to the
    decomposer, one row per polygon (columns ``sample_id``, ``input``,
    ``holes``, ``vertices``, ``area_m2``), in local metres.
    """
    import geopandas as gpd

    path = Path(path)
    if not path.exists():
        raise SystemExit(f"{path} is missing; it is local data, not part of the repository")
    table = gpd.read_file(path)
    rows = table[(table["sample_id"].astype(str) == str(sample_id)) & (table["input"] == int(input_index))]
    if rows.empty:
        inputs = sorted(table.loc[table["sample_id"].astype(str) == str(sample_id), "input"].tolist())
        raise SystemExit(f"{sample_id} input {input_index} is not in {path.name}"
                         + (f"; its inputs there are {inputs}" if inputs else ""))
    return rows.geometry.iloc[0]


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _config_from_args(args: argparse.Namespace) -> Any:
    """The settings of `bem4ai_notebook_config()`, with what the command line overrides."""
    base = bem4ai_notebook_config()
    adaptive = base.adaptive_search
    depth = args.depth if args.depth is not None else base.search_depth
    width = args.width if args.width is not None else base.search_width
    if args.no_adaptive:
        # Without initial values the search runs at depth and width throughout.
        adaptive = ENGINE.adaptive_config(enabled=False)
    else:
        adaptive = replace(
            adaptive,
            max_depth=depth,
            max_width=width,
            initial_depth=min(args.initial_depth if args.initial_depth is not None else adaptive.initial_depth, depth),
            initial_width=min(args.initial_width if args.initial_width is not None else adaptive.initial_width, width),
        )
    overrides: Dict[str, Any] = dict(search_depth=depth, search_width=width, adaptive_search=adaptive)
    if args.min_area is not None:
        overrides["min_area_m2"] = args.min_area
    if args.max_variants is not None:
        overrides["max_variants_per_polygon"] = args.max_variants
    if args.max_failed is not None:
        overrides["max_failed_states"] = args.max_failed
    for option, value in (("beam_ranking", args.ranking), ("bisector_always", args.bisector_always),
                          ("min_corner_deg", args.min_corner)):
        if value is None:
            continue
        if not ENGINE.supports(option):
            raise SystemExit(f"this version of the engine has no {option!r} setting")
        overrides[option] = value
    return replace(base, **overrides)


def _input_polygon(args: argparse.Namespace) -> Tuple[Polygon, Optional[str]]:
    if args.sample_id:
        polygon = gpkg_polygon(args.sample_id, args.input_index, args.gpkg)
        return polygon, f"{args.sample_id} #{args.input_index}"
    polygons = load_polygons(args.input)
    if not polygons:
        raise SystemExit("no polygon in the input")
    wkt_text = args.input.strip().upper().startswith(("POLYGON", "MULTIPOLYGON", "GEOMETRYCOLLECTION"))
    note = None if wkt_text else Path(args.input).name
    if args.polygon_index is not None:
        if not 0 <= args.polygon_index < len(polygons):
            raise SystemExit(f"--polygon-index {args.polygon_index}: the input has {len(polygons)} polygons")
        return polygons[args.polygon_index], note
    if len(polygons) > 1:
        print(f"{len(polygons)} polygons in the input; drawing the largest (--polygon-index to choose)")
    return max(polygons, key=lambda p: p.area), note


@contextmanager
def _rc():
    """Embed fonts as TrueType in PDF and keep SVG text live, so the figure stays editable in Affinity."""
    import logging

    import matplotlib as mpl

    # fontTools complains about a table of the TUM font it cannot subset; the glyphs are unaffected.
    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
    with mpl.rc_context({"pdf.fonttype": 42, "svg.fonttype": "none", "font.family": "sans-serif",
                         "font.sans-serif": _FONT_FAMILY}):
        yield


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the beam-search tree of the convex decomposer for one polygon.")
    src = parser.add_argument_group("input polygon (one of)")
    src.add_argument("--input", help="a WKT string, or a .wkt/.txt, .geojson/.json or .wkb file")
    src.add_argument("--polygon-index", type=int, help="which polygon of a multi-polygon input (default: the largest)")
    src.add_argument("--sample-id", help="a building of the BEM4AI decomposition inputs (--gpkg)")
    src.add_argument("--input-index", type=int, default=0, help="which of the building's decomposition inputs (0)")
    src.add_argument("--gpkg", default=str(DEFAULT_GPKG),
                     help="the BEM4AI decomposition inputs (data/bem4ai/decomposition_inputs.gpkg, local only)")

    search = parser.add_argument_group(
        "decomposer settings (default: the budget BEM4AI notebook 4 runs, this engine's defaults for the rest)")
    search.add_argument("--depth", type=int, help="maximum search depth (24)")
    search.add_argument("--width", type=int, help="maximum beam width (4)")
    search.add_argument("--initial-depth", type=int, help="first depth of the adaptive schedule (4)")
    search.add_argument("--initial-width", type=int, help="first width of the adaptive schedule (3)")
    search.add_argument("--no-adaptive", action="store_true", help="search at --depth and --width from the start")
    search.add_argument("--min-area", type=float, help="smallest piece a cut may leave, m2 (2.0)")
    search.add_argument("--max-variants", type=int, help="stop after this many successes (32)")
    search.add_argument("--max-failed", type=int, help="stop after this many dead-ends (500)")
    search.add_argument("--ranking", choices=("reflex_first", "score_first"), help="how the beam orders states")
    search.add_argument("--bisector-always", action=argparse.BooleanOptionalAction,
                        help="offer the mid-angle cut at every reflex vertex, or only where no edge cut works")
    search.add_argument("--min-corner", type=float, help="smallest corner a cut may create, degrees")

    fig = parser.add_argument_group("figure")
    fig.add_argument("-o", "--output", nargs="+", required=True, help="output file(s): .pdf, .svg, .png")
    fig.add_argument("--max-nodes", type=int, default=60, help="draw at most this many states (60; 0: no limit)")
    fig.add_argument("--max-depth", type=int, help="draw states down to this depth only")
    fig.add_argument("--max-children", type=int,
                     help="draw at most this many children per state (default 12, none with --max-nodes 0; 0: no limit)")
    fig.add_argument("--hide-duplicates", action="store_true", help="leave out cuts that led to a known state")
    fig.add_argument("--compact-root", action="store_true", help="caption the root like any other state")
    fig.add_argument("--attempt", type=int, help="draw the tree as it stood after this budget attempt")
    fig.add_argument("--each-attempt", action="store_true", help="also write one figure per attempt (<name>_attemptK)")
    fig.add_argument("--dpi", type=int, default=300, help="resolution of .png output (300)")
    fig.add_argument("--no-check", action="store_true", help="skip the comparison with an untraced run")
    args = parser.parse_args(argv)

    if bool(args.input) == bool(args.sample_id):
        parser.error("give either --input or --sample-id")

    cfg = _config_from_args(args)
    polygon, note = _input_polygon(args)
    print(f"polygon: {polygon.area:.1f} m2, {len(polygon.exterior.coords) - 1} vertices, {len(polygon.interiors)} holes")

    trace = trace_search(polygon, cfg)
    s = trace.stats
    n_duplicates = sum(1 for n in trace.nodes if n.is_duplicate)
    print(
        f"search: {s.get('n_variants')} successes, {s.get('n_failed_terminal_states')} failed, "
        f"{s.get('search_total_states')} states processed, {len(trace.nodes)} tree nodes "
        f"({n_duplicates} duplicates), {len(trace.attempts)} attempts, terminated by {s.get('search_terminated_by')}"
        + (f", chosen {s.get('n_parts')} zones, score {trace.best_score:.3f}" if trace.best_score is not None else "")
        + (f" (fallback {s['fallback_used']})" if s.get("fallback_used") else "")
    )
    for line in trace.notes:
        print(f"warning: {line}")
    if not args.no_check:
        diffs = compare_with_untraced(trace, cfg)
        if diffs:
            print("warning: the traced run differs from an untraced run:\n  " + "\n  ".join(diffs))
        else:
            print("check: the traced run matches an untraced run (same chosen variant and counts)")

    max_nodes = args.max_nodes if args.max_nodes and args.max_nodes > 0 else None
    if args.max_children is None:
        max_children = 12 if max_nodes is not None else None  # "--max-nodes 0" draws everything
    else:
        max_children = args.max_children if args.max_children > 0 else None
    jobs = [(args.attempt, "")]
    if args.each_attempt:
        jobs += [(k, f"_attempt{k}") for k in range(1, len(trace.attempts) + 1)]
    for attempt, suffix in jobs:
        figure = draw_search_tree(
            trace,
            attempt=attempt,
            max_nodes=max_nodes,
            max_depth=args.max_depth,
            max_children=max_children,
            show_duplicates=not args.hide_duplicates,
            explain_root=not args.compact_root,
            source_note=note,
        )
        w_mm, h_mm = (v * 25.4 for v in figure.get_size_inches())
        for out in args.output:
            path = Path(out)
            path = path.with_name(path.stem + suffix + path.suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            dpi = args.dpi
            if path.suffix.lower() == ".png":
                dpi = min(dpi, int(16000 / (max(w_mm, h_mm) / 25.4)))
            with _rc():
                figure.savefig(path, dpi=dpi, facecolor="white")
            print(f"wrote {path} ({w_mm:.0f} x {h_mm:.0f} mm)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
