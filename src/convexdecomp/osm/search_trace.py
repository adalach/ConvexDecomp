"""Record what the convex-decomposition search does for one polygon.

The search (`convexdecomp.core.search.run_polygon_state_search`, driven by
`convexdecomp.osm.decompose`) is a persistent adaptive beam search. A state is
a set of pieces. Expanding it cuts the piece chosen next: at its reflex
vertices, or, for a piece with a hole, along every pair of chords that opens
the hole. The frontier is split three ways - the active beam, an overflow pool
for states the width bound pushed aside, and a deferred pool for states the
depth bound stopped - and when the beam runs dry the bounds are raised and
parked states come back, so the search continues from where it was instead of
restarting. The search's counters cannot show any of that. `trace_search`
records the tree itself, for `search_tree_figure.py` to draw and the search
explorer to show.

Recording leaves the engine alone. The cut functions, `hole_openings`, the
state key and the expansion callback are wrapped where
`convexdecomp.osm.decompose` looks them up, for the length of one call. The
frontier pools are local dictionaries of `run_polygon_state_search` that no
callback can see, so the moves between them are observed with
`sys.monitoring` (Python 3.12+) on the search's own nested helpers,
`_assign_to_pool`, `_enqueue_state` and the rest. That ties the tracer to those
helper names: if they change, it stops with an error rather than recording a
wrong tree. The monitoring only reads frame locals, so the traced run is the
same run; `compare_with_untraced` checks that. It needs Python 3.12 or later.

`convexdecomp.convex_zones.search_tree` turns a trace into plain data for a
page; the search explorer and `search_tree_figure.py` in `tools/` draw it.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence, Tuple

import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from convexdecomp.core import search as _search
from convexdecomp.osm import decompose as _decompose

__all__ = [
    "ENGINE",
    "Engine",
    "NodeStatus",
    "SearchNode",
    "SearchTrace",
    "best_path",
    "bem4ai_notebook_config",
    "compare_with_untraced",
    "focus",
    "incoming_chord",
    "node_status",
    "piece_counts",
    "trace_search",
    "walk",
]


# ---------------------------------------------------------------------------
# The engine and its settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Engine:
    """The decomposer module the tracer wraps, with the search parts it reads.

    `count_reflex` is the search's own count of a piece's reflex vertices -
    the count its priority ranks states by, in which a hole's corners count.
    """

    decomposer: types.ModuleType
    adaptive_config: type
    count_reflex: Callable[[Polygon], int]
    cut_source: str = "ConvexDecomp exact chords"

    def is_convex(self, poly: Polygon) -> bool:
        return bool(self.decomposer.is_convex_polygon(poly))

    def supports(self, option: str) -> bool:
        """Whether the engine's `ConvexDecompositionConfig` has the field `option`."""
        return option in getattr(self.decomposer.ConvexDecompositionConfig, "__dataclass_fields__", {})


ENGINE = Engine(_decompose, _search.AdaptiveSearchConfig, _search._count_reflex_vertices)


def bem4ai_notebook_config(**overrides: Any) -> Any:
    """The search budget of BEM4AI notebook 4 (``BEM4AI_4_ModelTransformations``) on this engine.

    That notebook's ``CONVEX_DECOMP_CFG``, written out because the notebook is
    not importable: depth 24 and width 4 with an adaptive budget starting at
    depth 4 and width 3, 8 reflex vertices tried per piece, up to 32 variants
    and 500 dead ends, 2 m² smallest piece, part and compactness weights 1 and
    20. Everything it does not set keeps this engine's defaults (the beam
    ranked by score first, the mid-angle cut at every reflex vertex, corners
    of at least 30°, zones at least 1.2 m wide); ``PAPER_2026_SEARCH`` as
    overrides gives the search of the EG-ICE 2026 paper. A figure drawn with
    other settings shows another search. Keyword arguments override single
    fields.
    """
    cfg = _decompose.ConvexDecompositionConfig(
        search_depth=24,
        search_width=4,
        max_reflex_per_node=8,
        max_variants_per_polygon=32,
        max_failed_states=500,
        min_area_m2=2.0,
        weight_parts=1.0,
        weight_compactness=20.0,
        adaptive_search=_search.AdaptiveSearchConfig(
            enabled=True,
            initial_depth=4,
            initial_width=3,
            max_depth=24,
            max_width=4,
            depth_step=1,
            width_step=1,
        ),
    )
    return replace(cfg, **overrides) if overrides else cfg


# ---------------------------------------------------------------------------
# The recorded tree
# ---------------------------------------------------------------------------

_FALLBACK_NAMES = {"bisector_dead_end": "bisector", "diagonal_no_reflex": "diagonal", "hole_edge_split": "hole edges"}
# The edge kind a child gets from the fallback label the expansion gave it.
_FALLBACK_KINDS = {"bisector_dead_end": "bisector", "diagonal_no_reflex": "diagonal", "hole_edge_split": "hole_edge_split"}
_DEAD_END_NAMES = {
    "min_area_dead_end": "min-area",
    "no_reflex_dead_end": "no-reflex",
    "no_split_dead_end": "no-split",
    "depth_limit_dead_end": "depth-limit",
}

Point = Tuple[float, float]


@dataclass(eq=False)
class SearchNode:
    """One state the search generated, or a cut that led back to a known state.

    `number` is the state's place in generation order, drawn as ``S<number>``.
    Duplicates have none: they are cuts whose result the search had already
    seen, either earlier in the same expansion or anywhere before, and the
    search drops them without a trace of its own. `events` is what the search
    did with the state, as ``(seq, kind, detail)`` in the order it happened:
    ``pool`` (moved to the active, overflow or deferred pool), ``popped``,
    ``expanded``, ``success``, ``dead_end`` and ``superseded``.

    `cut` is the edge label, as the engine names the cut: ``r4 prev``,
    ``r4 next``, ``r4 bis``, a diagonal ``r1–r5``, or the opening of a hole
    ``h1 prev + h2 bis``. `cut_kind` is ``primary``, ``opening``, one of the
    fallbacks (``bisector``, ``diagonal``, ``hole_edge_split``), or
    ``duplicate``. `cut_target` and `cut_parts` are the piece the cut into this
    node cut and the pieces it gave; `cut_origins` the vertices it started
    from (none for a diagonal, two hole vertices for an opening), and
    `cut_chords` an opening's chords as (start, end) segments.
    """

    index: int
    pieces: Tuple[Polygon, ...]
    depth: int
    parent: Optional["SearchNode"]
    cut: str = ""
    cut_kind: str = "root"
    number: Optional[int] = None
    duplicate_of: Optional["SearchNode"] = None
    key: Hashable = None
    fallback_labels: Tuple[str, ...] = ()
    created: int = 0
    events: List[Tuple[int, str, Any]] = field(default_factory=list)
    children: List["SearchNode"] = field(default_factory=list)
    cut_target: Optional[Polygon] = None
    cut_parts: Tuple[Polygon, ...] = ()
    cut_origins: Tuple[Point, ...] = ()
    cut_chords: Tuple[Tuple[Point, Point], ...] = ()

    @property
    def is_duplicate(self) -> bool:
        return self.number is None

    @property
    def name(self) -> str:
        return f"S{self.number}" if self.number is not None else "duplicate"

    def kinds(self, until: Optional[int] = None) -> List[Tuple[int, str, Any]]:
        return [e for e in self.events if until is None or e[0] <= until]


@dataclass
class SearchTrace:
    """A recorded search: the tree, the budget attempts and the decomposer's own result.

    `best` holds the node of the chosen variant (empty when the search found
    none), `best_score` the engine's score of it (lower is better).
    """

    polygon: Polygon
    root: SearchNode
    nodes: List[SearchNode]
    attempts: List[Dict[str, Any]]
    stats: Dict[str, Any]
    best: List[SearchNode]
    engine: Engine
    best_score: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    @property
    def root_piece(self) -> Polygon:
        """The polygon as the search started from it (normalised), whose vertex numbers the first cuts use."""
        return self.root.pieces[0]

    def snapshot_seq(self, attempt: Optional[int]) -> Optional[int]:
        """The event horizon of a 1-based budget attempt, or None for the whole run."""
        if attempt is None:
            return None
        if not 1 <= attempt <= len(self.attempts):
            raise ValueError(f"the search made {len(self.attempts)} attempts; attempt {attempt} does not exist")
        return int(self.attempts[attempt - 1]["seq"])


@dataclass
class _Cut:
    label: str
    kind: str
    target: Polygon
    parts: Sequence[Polygon]
    origins: Tuple[Point, ...] = ()
    chords: Tuple[Tuple[Point, Point], ...] = ()
    key: Hashable = None
    registered: int = -1  # when the expansion registered the cut's state; -1 if it never did

    def made(self, parts: Sequence[Polygon]) -> bool:
        """Whether `parts`, as the expansion registered them, are this cut's pieces.

        Usually the very list the cut returned; a fallback may pass on a filtered
        copy, so the pieces themselves are compared too.
        """
        return parts is self.parts or (
            parts is not None and len(parts) == len(self.parts)
            and all(any(p is q for q in self.parts) for p in parts)
        )


# Each describes a cut call as (edge label, edge kind, index of the vertex the cut starts from).


def _describe_edge_cut(args: tuple, kwargs: dict) -> Tuple[str, str, Any]:
    vidx = kwargs.get("vidx", args[1] if len(args) > 1 else None)
    use_prev = kwargs.get("use_prev", args[3] if len(args) > 3 else True)
    return f"r{vidx} {'prev' if use_prev else 'next'}", "primary", vidx


def _describe_bisector_cut(args: tuple, kwargs: dict) -> Tuple[str, str, Any]:
    vidx = kwargs.get("vidx", args[1] if len(args) > 1 else None)
    return f"r{vidx} bis", "primary", vidx


def _describe_diagonal_cut(args: tuple, kwargs: dict) -> Tuple[str, str, Any]:
    i = kwargs.get("start_idx", args[1] if len(args) > 1 else "?")
    j = kwargs.get("end_idx", args[2] if len(args) > 2 else "?")
    return f"r{i}–r{j}", "primary", None  # joins two vertices; no reflex vertex to start from


@contextmanager
def _monitoring(handlers_start: Dict[types.CodeType, Callable], handlers_return: Dict[types.CodeType, Callable]):
    """Call the handlers when the given code objects start or return, and only then.

    `sys.monitoring` instruments just these code objects, so the rest of the
    search, shapely included, runs at full speed - unlike `sys.setprofile`,
    which would interpose on every call.
    """
    mon = getattr(sys, "monitoring", None)
    if mon is None:
        raise RuntimeError("tracing the search needs Python 3.12 or newer (sys.monitoring)")
    tool = next((t for t in (mon.PROFILER_ID, 3, 4) if mon.get_tool(t) is None), None)
    if tool is None:
        raise RuntimeError("no free sys.monitoring tool id; is a profiler running?")

    def on_start(code, offset):
        handler = handlers_start.get(code)
        if handler is not None:
            handler(sys._getframe(1))

    def on_return(code, offset, retval):
        handler = handlers_return.get(code)
        if handler is not None:
            handler(sys._getframe(1))

    codes = set(handlers_start) | set(handlers_return)
    events = mon.events.PY_START | mon.events.PY_RETURN
    mon.use_tool_id(tool, "search_trace")
    try:
        mon.register_callback(tool, mon.events.PY_START, on_start)
        mon.register_callback(tool, mon.events.PY_RETURN, on_return)
        for code in codes:
            mon.set_local_events(tool, code, events)
        yield
    finally:
        for code in codes:
            mon.set_local_events(tool, code, 0)
        mon.register_callback(tool, mon.events.PY_START, None)
        mon.register_callback(tool, mon.events.PY_RETURN, None)
        mon.free_tool_id(tool)


class _Recorder:
    """Builds the tree from what one `decompose_polygon_with_stats` call does.

    Search-side events refer to the search's own queued-state objects; they are
    logged against the object and attached to tree nodes at the end, because a
    state is moved between pools before `_enqueue_state` has returned it.
    """

    _HELPERS = (
        "_assign_to_pool",
        "_enqueue_state",
        "_admit_existing_node",
        "_remove_from_current_pool",
        "_record_attempt",
    )
    _WRAPPED = (
        "run_polygon_state_search",
        "_state_key",
        "cut_polygon_at_vertex",
        "cut_polygon_at_vertex_bisector",
        "cut_polygon_at_vertex_pair",
        "hole_openings",
        "holeless_polygons",
    )

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.nodes: List[SearchNode] = []
        self.attempts: List[Dict[str, Any]] = []
        self.root: Optional[SearchNode] = None
        self._searches = 0
        self._seq = 0
        self._next_number = 0
        self._log: List[Tuple[int, int, str, Any]] = []
        self._queued: Dict[int, Any] = {}  # id -> queued state, kept alive so ids stay unique
        self._node_of: Dict[int, SearchNode] = {}
        self._first_by_key: Dict[Hashable, SearchNode] = {}
        self._cuts: Optional[List[_Cut]] = None
        self._cut_depth = 0
        self._pending: Dict[Hashable, SearchNode] = {}
        self._search_code: Optional[types.CodeType] = None

    # -- bookkeeping -------------------------------------------------------

    def _tick(self) -> int:
        self._seq += 1
        return self._seq

    def _event(self, queued: Any, kind: str, detail: Any = None) -> None:
        self._queued[id(queued)] = queued
        self._log.append((self._tick(), id(queued), kind, detail))

    def _new_node(self, pieces, depth, parent, *, cut="", cut_kind="root", fallback_labels=()) -> SearchNode:
        node = SearchNode(
            index=len(self.nodes),
            pieces=tuple(pieces),
            depth=int(depth),
            parent=parent,
            cut=cut,
            cut_kind=cut_kind,
            fallback_labels=tuple(fallback_labels),
            created=self._tick(),
        )
        self.nodes.append(node)
        if parent is not None:
            parent.children.append(node)
        return node

    def _admit(self, node: SearchNode, queued: Any) -> None:
        node.number = self._next_number
        self._next_number += 1
        node.key = queued.key
        node.pieces = tuple(queued.state.pieces)
        node.fallback_labels = tuple(queued.state.fallback_labels)
        self._queued[id(queued)] = queued
        self._node_of[id(queued)] = node
        self._first_by_key.setdefault(queued.key, node)

    def _mark_duplicate(self, node: SearchNode, of: Optional[SearchNode], why: str) -> None:
        node.number = None
        node.duplicate_of = of
        node.events.append((self._tick(), "duplicate", why))

    # -- wrappers installed in convexdecomp.osm.decompose ----------------------

    @contextmanager
    def installed(self):
        module = self.engine.decomposer
        wraps = {
            "run_polygon_state_search": self._wrap_search,
            "_state_key": self._wrap_state_key,
            "cut_polygon_at_vertex": lambda fn: self._wrap_cut(fn, _describe_edge_cut),
            "cut_polygon_at_vertex_bisector": lambda fn: self._wrap_cut(fn, _describe_bisector_cut),
            "cut_polygon_at_vertex_pair": lambda fn: self._wrap_cut(fn, _describe_diagonal_cut),
            "hole_openings": self._wrap_openings,
            "holeless_polygons": self._wrap_hole_edges,
        }
        originals = {name: getattr(module, name) for name in self._WRAPPED if hasattr(module, name)}
        for name, fn in originals.items():
            setattr(module, name, wraps[name](fn))
        try:
            yield
        finally:
            for name, fn in originals.items():
                setattr(module, name, fn)

    def _wrap_cut(self, cut_fn: Callable, describe: Callable) -> Callable:
        def traced(*args, **kwargs):
            self._cut_depth += 1
            try:
                parts = cut_fn(*args, **kwargs)
            finally:
                self._cut_depth -= 1
            # Only the outermost call is a cut the expansion asked for; a cut
            # implementation may well call another one internally.
            if self._cut_depth == 0 and self._cuts is not None and len(parts) == 2:
                label, kind, vidx = describe(args, kwargs)
                target = args[0]
                ring = list(target.exterior.coords)[:-1]
                origins = (tuple(ring[vidx]),) if isinstance(vidx, int) and 0 <= vidx < len(ring) else ()
                self._cuts.append(_Cut(label, kind, target, parts, origins))
            return parts

        traced.__wrapped__ = cut_fn
        return traced

    def _wrap_openings(self, openings_fn: Callable) -> Callable:
        """Every opening of a hole the expansion is offered is a cut, labelled as the engine labels it."""

        def traced(*args, **kwargs):
            self._cut_depth += 1
            try:
                openings = openings_fn(*args, **kwargs)
            finally:
                self._cut_depth -= 1
            if self._cut_depth == 0 and self._cuts is not None:
                target = args[0] if args else kwargs["poly"]
                hole = kwargs.get("hole", args[1] if len(args) > 1 else 0)
                # Chords number the hole's vertices as the engine does, on the oriented polygon.
                ring = list(orient(target, 1.0).interiors[hole].coords)[:-1]
                for opening in openings:
                    starts = tuple(tuple(ring[c.vertex]) for c in opening.chords)
                    chords = tuple((tuple(ring[c.vertex]), tuple(c.point)) for c in opening.chords if c.length > 0)
                    self._cuts.append(_Cut(opening.label, "opening", target, opening.pieces, starts, chords))
            return openings

        traced.__wrapped__ = openings_fn
        return traced

    def _wrap_hole_edges(self, split_fn: Callable) -> Callable:
        """The engine's fallback when no pair of chords opens a hole: split along the hole's edges."""

        def traced(*args, **kwargs):
            self._cut_depth += 1
            try:
                parts = split_fn(*args, **kwargs)
            finally:
                self._cut_depth -= 1
            if self._cut_depth == 0 and self._cuts is not None and len(parts) >= 2:
                self._cuts.append(_Cut("hole edges", "hole_edge_split", args[0], parts))
            return parts

        traced.__wrapped__ = split_fn
        return traced

    def _wrap_state_key(self, state_key: Callable) -> Callable:
        def traced(pieces):
            key = state_key(pieces)
            if self._cuts is not None:
                # `_register_child` keys the state a cut produced; its `parts`
                # is the very list the cut returned, which names the cut.
                caller = sys._getframe(1)
                if caller.f_code.co_name == "_register_child":
                    parts = caller.f_locals.get("parts")
                    for cut in reversed(self._cuts):
                        if cut.made(parts):
                            cut.key = key
                            cut.registered = self._tick()
                            break
            return key

        traced.__wrapped__ = state_key
        return traced

    def _wrap_search(self, search: Callable) -> Callable:
        helpers = {c.co_name: c for c in search.__code__.co_consts if isinstance(c, types.CodeType)}
        missing = [name for name in self._HELPERS if name not in helpers]
        if missing:
            raise RuntimeError(
                "run_polygon_state_search no longer defines " + ", ".join(missing)
                + "; update tools/search_trace.py to the new search"
            )
        handlers_start = {
            helpers["_assign_to_pool"]: self._on_assign,
            helpers["_remove_from_current_pool"]: self._on_remove,
            helpers["_record_attempt"]: self._on_attempt,
        }
        handlers_return = {
            helpers["_enqueue_state"]: self._on_enqueue,
            helpers["_admit_existing_node"]: self._on_admitted,
        }

        def traced(initial_pieces, *, expand_state_fn, **kwargs):
            self._searches += 1
            if self._searches > 1:
                raise RuntimeError("decompose_polygon_with_stats ran more than one search; the tracer records one")
            self._search_code = search.__code__
            with _monitoring(handlers_start, handlers_return):
                return search(initial_pieces, expand_state_fn=self._wrap_expand(expand_state_fn), **kwargs)

        traced.__wrapped__ = search
        return traced

    def _wrap_expand(self, expand: Callable) -> Callable:
        def traced(pieces):
            search_locals = sys._getframe(1).f_locals
            queued = search_locals["node"]
            self._cuts = []
            try:
                result = expand(pieces)
            finally:
                cuts, self._cuts = self._cuts, None
            self._event(
                queued,
                "expanded",
                {"depth_limit": search_locals.get("current_depth_limit"), "width_limit": search_locals.get("current_width_limit")},
            )
            if not result.children:
                reason = next(iter(result.dead_end_counts), "no_split_dead_end")
                self._event(queued, "dead_end", _DEAD_END_NAMES.get(reason, reason))
            self._pending = self._children_of(self._node_of[id(queued)], cuts, result)
            return result

        return traced

    def _children_of(self, parent: SearchNode, cuts: List[_Cut], result) -> Dict[Hashable, SearchNode]:
        """Tree children of an expansion, in the order the expansion registered them.

        That is the order the search enqueues them in, so the state numbers run
        left to right. The expansion keeps one child per resulting state; a
        later cut that produced the same state is drawn as a duplicate beside
        it. Cuts refused for the minimum area never became children and are not
        drawn. A cut is a fallback only when the expansion says so: with
        ``bisector_always`` a mid-angle cut is an ordinary one.
        """
        kept_parts = {id(p) for child in result.children for p in child.pieces}
        pending: Dict[Hashable, SearchNode] = {}
        grouped: Dict[Hashable, List[_Cut]] = {}
        for cut in sorted((c for c in cuts if c.key), key=lambda c: c.registered):
            grouped.setdefault(cut.key, []).append(cut)
        for key, group in grouped.items():
            kept = next((c for c in group if id(c.parts[0]) in kept_parts), None)
            if kept is None:
                continue
            child_state = next(ch for ch in result.children if any(p is kept.parts[0] for p in ch.pieces))
            kind = next((_FALLBACK_KINDS[f] for f in child_state.fallback_labels if f in _FALLBACK_KINDS), kept.kind)
            for cut in group:
                node = self._new_node(
                    child_state.pieces if cut is kept else (),
                    parent.depth + 1,
                    parent,
                    cut=cut.label,
                    cut_kind=kind if cut is kept else "duplicate",
                    fallback_labels=child_state.fallback_labels,
                )
                node.cut_target, node.cut_parts = cut.target, tuple(cut.parts)
                node.cut_origins, node.cut_chords = cut.origins, cut.chords
                if cut is kept:
                    pending[key] = node
                else:
                    self._mark_duplicate(node, pending[key], "same state as a sibling cut")
        # A child whose cut was not seen (an unfamiliar cut function) still belongs in the tree.
        for child in result.children:
            if not any(p is c.parts[0] for c in cuts for p in child.pieces):
                key = self.engine.decomposer._state_key(list(child.pieces))
                if key not in pending:
                    pending[key] = self._new_node(child.pieces, parent.depth + 1, parent, cut="?", cut_kind="primary")
        return pending

    # -- sys.monitoring handlers on the search's nested helpers ---------------

    def _on_assign(self, frame) -> None:
        loc = frame.f_locals
        self._event(loc["node"], "pool", loc["pool"])

    def _on_remove(self, frame) -> None:
        # Called from the main loop it is the pop of the next state to process;
        # called from the other helpers it is part of a move between pools.
        if frame.f_back is not None and frame.f_back.f_code is self._search_code:
            self._event(frame.f_locals["node"], "popped")

    def _on_attempt(self, frame) -> None:
        loc = frame.f_locals
        self.attempts.append(
            {
                "seq": self._tick(),
                "depth": loc.get("current_depth_limit"),
                "width": loc.get("current_width_limit"),
                "stage_reason": loc.get("stage_reason"),
                "n_variants": len(loc.get("variants") or ()),
            }
        )

    def _on_admitted(self, frame) -> None:
        # A state that reaches the depth limit with no deeper budget left is
        # closed here as a failure without ever entering a pool.
        loc = frame.f_locals
        queued = loc["node"]
        if loc["frontier_nodes"].get(queued.key) is not queued and queued.key in loc["closed_states"]:
            self._event(queued, "dead_end", "depth-limit")

    def _on_enqueue(self, frame) -> None:
        loc = frame.f_locals
        key = loc.get("key")
        if not key:
            return
        candidate = loc.get("candidate")
        node = self._pending.pop(key, None)
        if node is None:
            if self.root is not None or candidate is None:
                return
            node = self.root = self._new_node(candidate.state.pieces, 0, None)
        if candidate is None:
            self._mark_duplicate(node, self._first_by_key.get(key), "state already processed")
            return
        existing = loc.get("existing")
        if existing is not None and loc["frontier_nodes"].get(key) is existing:
            self._mark_duplicate(node, self._node_of.get(id(existing)), "state already queued")
            return
        self._admit(node, candidate)
        if existing is not None:
            self._event(existing, "superseded", candidate)

    # -- the finished tree ----------------------------------------------------

    def finish(self) -> None:
        for seq, queued_id, kind, detail in self._log:
            node = self._node_of.get(queued_id)
            if node is None:
                continue
            if kind == "superseded":
                detail = self._node_of.get(id(detail))
            node.events.append((seq, kind, detail))
        for node in self.nodes:
            node.events.sort(key=lambda e: e[0])
            node.events.extend(_pop_outcomes(node, self.engine.is_convex))
            node.events.sort(key=lambda e: e[0])


def walk(node: SearchNode):
    """`node` and everything under it, depth first, children in generation order."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _pop_outcomes(node: SearchNode, is_convex: Callable[[Polygon], bool]) -> List[Tuple[int, str, Any]]:
    """What a pop led to when the search left no event of its own for it.

    A popped state is either expanded, parked in the deferred pool, or closed on
    the spot: as a success when all its pieces are convex, which is the search's
    own test, or else as a depth-limit dead-end.
    """
    outcomes = []
    events = node.events
    for i, (seq, kind, _) in enumerate(events):
        if kind != "popped":
            continue
        handled = False
        for _, later, _ in events[i + 1 :]:
            if later == "popped":
                break
            if later in ("expanded", "pool"):
                handled = True
                break
        if not handled:
            if all(is_convex(p) for p in node.pieces):
                outcomes.append((seq, "success", None))
            else:
                outcomes.append((seq, "dead_end", "depth-limit"))
    return outcomes


def _score(engine: Engine, parts: Sequence[Polygon], cfg: Any) -> float:
    """The engine's own score of a list of pieces, under `cfg` (lower is better)."""
    decomposer = engine.decomposer
    if hasattr(decomposer, "_partition_score"):
        return float(decomposer._partition_score(list(parts), cfg))
    return float(decomposer._score_polys(list(parts), cfg.weight_parts, cfg.weight_compactness))


def trace_search(polygon: Polygon, cfg: Any = None, *, engine: Engine = ENGINE) -> SearchTrace:
    """Run `decompose_polygon_with_stats` on `polygon` and record its search tree.

    `cfg` defaults to `bem4ai_notebook_config()`. The decomposer's own result
    comes back in `SearchTrace.stats`, and `SearchTrace.best` holds the node of
    the variant it chose. `SearchTrace.notes` stays empty when the trace
    accounts for every state the search processed and every success it counted.
    """
    cfg = cfg or bem4ai_notebook_config()
    recorder = _Recorder(engine)
    with recorder.installed():
        stats = engine.decomposer.decompose_polygon_with_stats(polygon, cfg)
    recorder.finish()

    notes: List[str] = []
    if recorder.root is None:
        # The polygon never reached the search: it is already convex, or it is refused as it is (not valid,
        # or a convex polygon thinner than the zone width).
        root = SearchNode(index=0, pieces=(polygon,), depth=0, parent=None, number=0)
        if stats.get("convex_success"):
            root.events.append((1, "success", None))
        else:
            reason = str(stats.get("search_terminated_by") or "refused").replace("_", " ")
            root.events.append((1, "dead_end", reason))
        recorder.nodes.append(root)
        recorder.root = root
    root = recorder.root

    best: List[SearchNode] = []
    if stats.get("best_variant") and recorder.nodes[1:]:
        best_key = engine.decomposer._state_key(list(stats["best_variant"]))
        found = next((n for n in recorder.nodes
                      if n.key == best_key and any(k == "success" for _, k, _ in n.events)), None)
        if found is None:
            notes.append("the chosen variant is not among the traced states")
        else:
            best.append(found)
    elif stats.get("best_variant"):
        best.append(root)

    if recorder.nodes[1:]:
        n_pops = sum(1 for n in recorder.nodes for _, k, _ in n.events if k == "popped")
        if n_pops != stats.get("search_total_states", n_pops):
            notes.append(f"traced {n_pops} processed states, the search counted {stats['search_total_states']}")
        n_success = sum(1 for n in recorder.nodes if any(k == "success" for _, k, _ in n.events))
        if n_success != stats.get("n_variants", n_success):
            notes.append(f"traced {n_success} successes, the search counted {stats['n_variants']}")

    best_score = _score(engine, stats["best_variant"], cfg) if stats.get("best_variant") else None
    return SearchTrace(
        polygon=polygon,
        root=root,
        nodes=recorder.nodes,
        attempts=recorder.attempts,
        stats=stats,
        best=best,
        engine=engine,
        best_score=best_score,
        notes=notes,
    )


def compare_with_untraced(trace: SearchTrace, cfg: Any = None) -> List[str]:
    """Differences between the traced run and a plain run on the same polygon.

    Empty when the trace saw the same search an untraced call runs: same chosen
    variant, same counts, same stopping reason.
    """
    decomposer = trace.engine.decomposer
    cfg = cfg or bem4ai_notebook_config()
    plain = decomposer.decompose_polygon_with_stats(trace.polygon, cfg)
    traced = trace.stats
    diffs = []
    for name in (
        "n_variants",
        "n_parts",
        "n_states_seen",
        "search_total_states",
        "n_failed_terminal_states",
        "search_terminated_by",
        "search_depth_used",
        "search_width_used",
        "search_attempt_count",
        "fallback_used",
    ):
        if plain.get(name) != traced.get(name):
            diffs.append(f"{name}: traced {traced.get(name)!r}, untraced {plain.get(name)!r}")
    key_plain = decomposer._state_key(list(plain["best_variant"])) if plain.get("best_variant") else ()
    key_traced = decomposer._state_key(list(traced["best_variant"])) if traced.get("best_variant") else ()
    if key_plain != key_traced:
        diffs.append("best_variant: traced and untraced runs chose different zonings")
    return diffs


# ---------------------------------------------------------------------------
# Reading the tree: status, counts, what a state is about
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeStatus:
    """What a node's ring and caption say."""

    ring: str  # success | dead_end | merged | parked | expanded | active | duplicate
    lines: Tuple[str, ...]


def node_status(node: SearchNode, until: Optional[int] = None) -> NodeStatus:
    """The caption words and ring style of a node, as of event `until` (None: the whole run).

    Follows the EG-ICE 2026 search-tree figure: a state that was ever parked
    is labelled by the pools it was parked in, even if it was expanded later
    (its children show that); "expanded" is kept for states expanded straight
    from the beam.
    """
    if node.is_duplicate:
        return NodeStatus("duplicate", ("duplicate",))
    events = node.kinds(until)
    kinds = {kind for _, kind, _ in events}
    parked: List[str] = []
    for _, kind, detail in events:
        if kind == "pool" and detail in ("overflow", "deferred") and detail not in parked:
            parked.append(detail)
    lines = [word + ("," if i < len(parked) - 1 else "") for i, word in enumerate(parked)]

    if "success" in kinds:
        lines.append("success")
        fallbacks = sorted({_FALLBACK_NAMES.get(f, f) for f in node.fallback_labels if f})
        if fallbacks:
            lines.append("fallback: " + ", ".join(fallbacks))
        return NodeStatus("success", tuple(lines))
    if "dead_end" in kinds:
        reason = next(detail for _, kind, detail in events if kind == "dead_end")
        lines += [reason, "dead-end"]
        return NodeStatus("dead_end", tuple(lines))
    if "superseded" in kinds:
        other = next(detail for _, kind, detail in events if kind == "superseded")
        lines.append(f"merged into {other.name}" if other is not None else "merged")
        return NodeStatus("merged", tuple(lines))
    if parked:
        return NodeStatus("parked", tuple(lines))
    if "expanded" in kinds:
        return NodeStatus("expanded", ("expanded",))
    return NodeStatus("active", ("in beam",))


def piece_counts(pieces: Sequence[Polygon], engine: Engine = ENGINE) -> Tuple[int, int, int]:
    """Parts, non-convex parts, and reflex vertices over the non-convex parts.

    Reflex vertices are counted by the search itself, the count its priority
    ranks states by, so a hole's corners count.
    """
    nonconvex = [p for p in pieces if not engine.is_convex(p)]
    return len(pieces), len(nonconvex), sum(int(engine.count_reflex(p)) for p in nonconvex)


def best_path(trace: SearchTrace, until: Optional[int] = None) -> List[SearchNode]:
    """The chosen variant's node and all its ancestors, once the variant exists."""
    path: List[SearchNode] = []
    for node in trace.best:
        if not any(k == "success" for _, k, _ in node.kinds(until)):
            continue
        while node is not None and node not in path:
            path.append(node)
            node = node.parent
    return path


def focus(node: SearchNode, root: SearchNode, until: Optional[int] = None) -> Tuple[List[Polygon], List[Point]]:
    """The pieces a picture of `node` shows in colour, and the vertices to mark in them.

    The root shows the whole input. A state the search cut from shows the
    piece it cut - every cut of one expansion cuts the same piece, the largest
    non-convex one - and the vertices those cuts started from: reflex
    vertices, or the hole vertices of the chords that open a hole. A state
    nothing was cut from (success, dead-end, parked, still in the beam) shows
    instead the pieces of the cut that made it.
    """
    outgoing = [c for c in node.children
                if c.cut_target is not None and (until is None or c.created <= until)]
    starts = list(dict.fromkeys(p for c in outgoing for p in c.cut_origins))
    if node is root:
        return list(node.pieces), starts
    if outgoing:
        target = outgoing[0].cut_target
        return [p for p in node.pieces if p is target] or [target], starts
    if node.cut_parts:
        return [p for p in node.pieces if any(p is q for q in node.cut_parts)] or list(node.cut_parts), []
    return list(node.pieces), []


def incoming_chord(node: SearchNode):
    """The line(s) the cut into `node` drew, or None.

    An opening's chords are known exactly. For any other cut they are the
    pieces' boundaries minus the cut piece's: one segment for a chord that
    stops at the first boundary point it reaches.
    """
    if node.cut_chords:
        return shapely.MultiLineString([list(chord) for chord in node.cut_chords])
    if node.cut_target is None or len(node.cut_parts) < 2:
        return None
    minx, miny, maxx, maxy = node.cut_target.bounds
    edge = node.cut_target.boundary.buffer(1e-7 * max(maxx - minx, maxy - miny, 1e-9))
    drawn = shapely.union_all([p.boundary.difference(edge) for p in node.cut_parts])
    return None if drawn.is_empty else drawn
