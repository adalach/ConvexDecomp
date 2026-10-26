"""The search tree of one piece as plain data, for a page that draws it: every state the engine's search generated,
what the search did with it, and the convex variants it found, ranked as the engine ranks them.

:func:`search_tree` runs the engine on the piece with :mod:`convexdecomp.osm.search_trace` watching (the traced run is
the same run: same states, same variants, same choice) and returns lists and dicts of numbers and strings, as JSON
carries them. Geometry is given as rings (``{"exterior": [[x, y], ...], "holes": [...]}``, counter-clockwise, rounded
to 0.1 mm), every distinct piece once in ``pieces``, which the states refer to by number.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from shapely.geometry import Polygon
from shapely.geometry.polygon import orient

from convexdecomp.osm import search_trace as T
from convexdecomp.osm.decompose import ConvexDecompositionConfig

from .settings import ZoneConfig, engine_config
from .variants import _metrics, variant_records


def rings(poly: Polygon) -> dict[str, Any]:
    """A polygon as rings, counter-clockwise, without the closing point, rounded to 0.1 mm."""
    poly = orient(poly, 1.0)
    return {"exterior": [[round(x, 4), round(y, 4)] for x, y in poly.exterior.coords[:-1]],
            "holes": [[[round(x, 4), round(y, 4)] for x, y in r.coords[:-1]] for r in poly.interiors]}


class _Pieces:
    """Every distinct piece once, with its measures; states refer to pieces by number."""

    def __init__(self) -> None:
        self.index: dict[bytes, int] = {}
        self.items: list[dict[str, Any]] = []

    def id(self, poly: Polygon) -> int:
        key = poly.wkb
        if key not in self.index:
            m = _metrics(poly, rectangularity=False)
            self.index[key] = len(self.items)
            self.items.append({"rings": rings(poly), "area": round(m.area, 4), "compactness": round(m.compactness, 4),
                               "width": round(m.width, 4), "convex": m.convex})
        return self.index[key]


def _chord(node: T.SearchNode) -> list[list[list[float]]]:
    chord = T.incoming_chord(node)
    if chord is None:
        return []
    return [[[round(x, 4), round(y, 4)] for x, y in g.coords] for g in getattr(chord, "geoms", [chord]) if g.length > 0]


def search_tree(polygon: Polygon, config: ZoneConfig | ConvexDecompositionConfig | None = None, *,
                max_states: int | None = None, max_seconds: float | None = None) -> dict[str, Any]:
    """The search tree of ``polygon`` with the engine settings of ``config`` (a page's ``ZoneConfig``, or an engine
    config; the package's defaults when None).

    Returns a dict:

    - ``nodes``: the states in depth-first order, children in the order the search generated them. Each has ``id``,
      ``number`` (the state's place in generation order, drawn ``S<number>``; None for a duplicate), ``parent``,
      ``depth``, ``cut`` (the cut that made it, as the engine names it: ``r4 prev``, ``r4 next``, ``r4 bis``, a
      diagonal ``r1–r5``, a hole opening ``h1 prev + h2 bis``), ``cut_kind`` (``root``, ``primary``, ``opening``,
      ``bisector``, ``diagonal``, ``hole_edge_split`` or ``duplicate``), ``created`` (generation order),
      ``duplicate_of`` (the number of the state a duplicate cut led back to), ``status`` (what the search did with
      it, the ring of the explorer: ``success``, ``dead_end``, ``parked``, ``expanded``, ``active``, ``merged`` or
      ``duplicate``) and ``status_lines`` (the same in words), ``fallbacks`` (the engine's fallback cuts on its
      path), ``pieces`` (numbers in ``pieces``), ``coloured`` and ``starts`` (the piece cut next, or the pieces the
      cut into it made, and the vertices the cuts start from: what a thumbnail shows in colour), ``chord`` (the cut's
      lines), ``children`` (how many children it has in the whole tree), ``on_best_path`` and ``variant`` (the
      variant's rank, for a success; else None).
    - ``variants``: the convex variants, best first (:func:`~.variants.variant_records`, the parts as numbers in
      ``pieces``), each with ``node``, the id of its state.
    - ``pieces``: each distinct piece's ``rings``, ``area``, ``compactness``, ``width`` and ``convex``.
    - ``polygon`` (its rings), ``stats`` (the search's counts and why it stopped) and ``truncated`` (how many states
      ``max_states`` left out: the first states in generation order are kept, with the path to every variant).

    ``max_seconds`` stops the search, as ``ConvexDecompositionConfig.max_seconds`` does; without it the tree is the
    one a run of the piece searches. Needs Python 3.12 or later (``sys.monitoring``).
    """
    cfg = config if isinstance(config, ConvexDecompositionConfig) else engine_config(config)
    if max_seconds is not None:
        cfg = replace(cfg, max_seconds=max_seconds)
    trace = T.trace_search(polygon, cfg)
    stats = trace.stats
    decomposer = trace.engine.decomposer

    variants = variant_records(stats, cfg)
    rank_of = {decomposer._state_key(list(v["parts"])): v["rank"] for v in variants}
    on_path = {n.index for n in T.best_path(trace)}
    all_nodes = list(T.walk(trace.root))

    keep = None
    if max_states is not None and len(all_nodes) > max_states:
        by_creation = sorted(all_nodes, key=lambda n: (n.created, n.index))
        keep = {n.index for n in by_creation[:max_states]}
        for n in all_nodes:
            if n.key in rank_of and T.node_status(n).ring == "success":
                while n is not None:
                    keep.add(n.index)
                    n = n.parent

    pieces = _Pieces()
    nodes, node_of_rank = [], {}
    for node in all_nodes:
        if keep is not None and node.index not in keep:
            continue
        status = T.node_status(node)
        coloured, starts = T.focus(node, trace.root)
        rank = rank_of.get(node.key) if status.ring == "success" else None
        if rank is not None:
            node_of_rank.setdefault(rank, node.index)
        nodes.append({
            "id": node.index,
            "number": node.number,
            "parent": node.parent.index if node.parent is not None else None,
            "depth": node.depth,
            "cut": node.cut,
            "cut_kind": node.cut_kind,
            "created": node.created,
            "duplicate_of": node.duplicate_of.number if node.duplicate_of is not None else None,
            "status": status.ring,
            "status_lines": list(status.lines),
            "fallbacks": [f for f in node.fallback_labels if f],
            "pieces": [pieces.id(p) for p in node.pieces],
            "coloured": [pieces.id(p) for p in coloured],
            "starts": [[round(x, 4), round(y, 4)] for x, y in starts],
            "chord": _chord(node),
            "children": len(node.children),
            "on_best_path": node.index in on_path,
            "variant": rank,
        })
    if variants and len(trace.nodes) <= 1:  # convex as it is, no search: the root is the variant
        node_of_rank = {0: trace.root.index}
        nodes[0]["variant"] = 0

    return {
        "polygon": rings(polygon),
        "nodes": nodes,
        "variants": [{**{k: v for k, v in row.items() if k != "parts"},
                      "parts": [pieces.id(p) for p in row["parts"]], "node": node_of_rank.get(row["rank"])}
                     for row in variants],
        "pieces": pieces.items,
        "stats": {
            "success": bool(stats.get("convex_success")),
            "variants": int(stats.get("n_variants", 0)),
            "states_seen": int(stats.get("n_states_seen", 0)),
            "processed": int(stats.get("search_total_states", 0)),
            "dead_ends": int(stats.get("n_failed_terminal_states", 0)),
            "terminated_by": stats.get("search_terminated_by"),
            "depth_used": stats.get("search_depth_used"),
            "width_used": stats.get("search_width_used"),
            "attempts": int(stats.get("search_attempt_count", 0)),
            "notes": list(trace.notes),
        },
        "truncated": 0 if keep is None else len(all_nodes) - len(nodes),
    }


__all__ = ["rings", "search_tree"]
