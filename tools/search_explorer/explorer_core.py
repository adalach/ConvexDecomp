"""The search explorer's logic, without a web server: examples, settings, and one traced run.

`app.py` serves it with FastAPI; the static page runs the same module in the
browser with Pyodide (`static/worker.js`), which is why it needs only the
standard library, shapely and the engine. Requests and replies are plain
dictionaries, as JSON would carry them.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))  # tools/, for search_trace

import search_trace as T  # noqa: E402  (also puts the repository's src/ on sys.path)
from shapely import wkt as shapely_wkt  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.geometry.polygon import orient  # noqa: E402

from convexdecomp.core.scores import PartitionScore, zone_metrics  # noqa: E402

LOCAL_EXAMPLES_FILE = T.REPO_ROOT / "data" / "bem4ai" / "explorer_examples.json"
BUILT_IN_EXAMPLE = HERE / "paper_example.wkt"
# Engine settings the page offers that an older engine may not have yet.
OPTIONAL_SETTINGS = ("bisector_always", "min_corner_deg", "min_zone_width_m", "beam_area_weighting")


class RequestError(ValueError):
    """A request the explorer cannot run; `status` is the HTTP status the server answers with."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def rings(poly: Polygon) -> Dict[str, Any]:
    poly = orient(poly, 1.0)
    return {"exterior": [[round(x, 4), round(y, 4)] for x, y in poly.exterior.coords[:-1]],
            "holes": [[[round(x, 4), round(y, 4)] for x, y in r.coords[:-1]] for r in poly.interiors]}


def polygon(r: Dict[str, Any]) -> Polygon:
    return orient(Polygon(r["exterior"], r["holes"]), 1.0)


def paper_example() -> Dict[str, Any]:
    paper = shapely_wkt.loads(BUILT_IN_EXAMPLE.read_text())
    return {"id": "paper_example", "label": "EG-ICE 2026 figure polygon (39.9 m², 1 hole)",
            "polygon": rings(paper), "references": []}


#: The examples by id. `use_examples` replaces the contents, so the dictionary itself never changes.
EXAMPLES: Dict[str, Dict[str, Any]] = {}
#: Where the examples came from, and the credit they need (shown on the page).
SOURCE: Dict[str, str] = {"examples": "built-in", "attribution": ""}


def use_examples(path: Optional[Path] = None) -> None:
    """Load the examples from `path`, else the local BEM4AI file, else the built-in paper polygon.

    A file holds ``{"examples": [...], "attribution": "..."}``; each example has an
    ``id``, a ``label``, a ``polygon`` (``exterior`` and ``holes``) and ``references``.
    """
    path = Path(path) if path is not None else LOCAL_EXAMPLES_FILE
    if path.exists():
        data = json.loads(path.read_text())
        examples = data["examples"]
        try:
            source = str(path.relative_to(T.REPO_ROOT))
        except ValueError:
            source = path.name
        SOURCE.update(examples=source, attribution=data.get("attribution", ""))
    else:
        examples = [paper_example()]
        SOURCE.update(examples="built-in", attribution="")
    EXAMPLES.clear()
    EXAMPLES.update((e["id"], e) for e in examples)


use_examples()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

# (smallest, largest, smallest excluded) for each number field; None: no bound.
_BOUNDS: Dict[str, tuple] = {
    "weight_compactness": (0, None, False), "area_weighting": (0, 1, False), "weight_rectangularity": (0, None, False),
    "weight_narrow": (0, None, False), "narrow_width_m": (0, None, True), "weight_small": (0, None, False),
    "small_area_m2": (0, None, True),
    "depth": (1, 60, False), "width": (1, 32, False), "start_width": (1, 32, False), "max_reflex": (1, 64, False),
    "max_variants": (1, 512, False),
    "max_failed": (1, 5000, False), "min_area_m2": (0.1, None, False), "snap_m": (0, None, False),
    "min_corner_deg": (0, 45, False), "min_zone_width_m": (0, 10, False), "beam_area_weighting": (0, 1, False),
}
_CHOICES = {"ranking": ("reflex_first", "score_first")}


@dataclass(frozen=True)
class ScoreParams:
    weight_compactness: float = 20.0
    area_weighting: float = 0.0
    weight_rectangularity: float = 0.0
    weight_narrow: float = 0.0
    narrow_width_m: float = 2.0
    weight_small: float = 0.0
    small_area_m2: float = 10.0


@dataclass(frozen=True)
class SearchParams:
    ranking: str = "score_first"
    depth: int = 24
    width: int = 4
    start_width: int = 3  # the adaptive budget's first width; it grows to `width` only while nothing is found
    max_reflex: int = 8
    max_variants: int = 32
    max_failed: int = 500
    min_area_m2: float = 2.0
    snap_m: float = 0.1
    adaptive: bool = True
    bisector_always: bool = True
    min_corner_deg: float = 30.0
    min_zone_width_m: float = 1.2
    beam_area_weighting: float = 1.0


def _params(cls: type, values: Optional[Dict[str, Any]], limits: Dict[str, float]) -> Any:
    """`cls` from a request's dictionary: unknown keys ignored, types and bounds checked, `limits` applied."""
    values = values or {}
    if not isinstance(values, dict):
        raise RequestError(f"{cls.__name__}: expected an object, got {type(values).__name__}")
    out: Dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in values:
            continue
        v = values[f.name]
        kind = type(f.default)
        if kind is bool:
            if not isinstance(v, bool):
                raise RequestError(f"{f.name}: expected true or false, got {v!r}")
        elif kind is str:
            if v not in _CHOICES[f.name]:
                raise RequestError(f"{f.name}: expected one of {', '.join(_CHOICES[f.name])}, got {v!r}")
        else:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or (kind is int and v != int(v)):
                raise RequestError(f"{f.name}: expected a {kind.__name__}, got {v!r}")
            v = kind(v)
            lo, hi, open_lo = _BOUNDS[f.name]
            if (lo is not None and (v <= lo if open_lo else v < lo)) or (hi is not None and v > hi):
                raise RequestError(f"{f.name}: {v} is out of range")
        out[f.name] = v
    params = cls(**out)
    capped = {k: min(getattr(params, k), cap) for k, cap in limits.items() if hasattr(params, k)}
    return cls(**{**asdict(params), **capped}) if capped else params


def config(search: SearchParams, score: PartitionScore) -> Any:
    """The engine's settings for one run; the optional ones only if this engine has them."""
    s = search
    settings: Dict[str, Any] = dict(
        search_depth=s.depth, search_width=s.width, max_reflex_per_node=s.max_reflex,
        max_variants_per_polygon=s.max_variants, max_failed_states=s.max_failed, min_area_m2=s.min_area_m2,
        weight_parts=1.0, weight_compactness=score.weight_compactness, cut_snap_vertex_tolerance_m=s.snap_m,
        adaptive_search=T.ENGINE.adaptive_config(
            enabled=s.adaptive, initial_depth=min(4, s.depth), initial_width=min(s.start_width, s.width),
            max_depth=s.depth, max_width=s.width, depth_step=1, width_step=1),
        beam_ranking=s.ranking,
    )
    for option in OPTIONAL_SETTINGS:
        if T.ENGINE.supports(option):
            settings[option] = getattr(s, option)
    # The engine's own score unless an extra term is on: its score chooses the variant with
    # area_weighting and guides the beam with beam_area_weighting. A score_fn replaces both.
    extra = score.weight_rectangularity or score.weight_narrow or score.weight_small
    if extra or not T.ENGINE.supports("area_weighting"):
        settings["score_fn"] = score
    else:
        settings["area_weighting"] = score.area_weighting
    return T.ENGINE.decomposer.ConvexDecompositionConfig(**settings)


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------


class _Pieces:
    """Every distinct piece once, with its metrics; states refer to pieces by number."""

    def __init__(self) -> None:
        self.index: Dict[bytes, int] = {}
        self.items: List[Dict[str, Any]] = []

    def id(self, poly: Polygon) -> int:
        key = poly.wkb
        if key not in self.index:
            m = zone_metrics(poly)
            self.index[key] = len(self.items)
            self.items.append({"rings": rings(poly), "area": m.area, "compactness": m.compactness,
                               "rectangularity": m.rectangularity, "width": m.width, "convex": m.convex})
        return self.index[key]


def _chord(node: T.SearchNode) -> List[List[List[float]]]:
    chord = T.incoming_chord(node)
    if chord is None:
        return []
    return [[[round(x, 4), round(y, 4)] for x, y in g.coords] for g in getattr(chord, "geoms", [chord]) if g.length > 0]


def run(request: Dict[str, Any], limits: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Trace the search on one example. `limits` caps search fields (the static page's budget)."""
    if not isinstance(request, dict) or not isinstance(request.get("example"), str):
        raise RequestError("the request needs an example id")
    example = EXAMPLES.get(request["example"])
    if example is None:
        raise RequestError(f"unknown example {request['example']!r}", status=404)
    score_params = _params(ScoreParams, request.get("score"), {})
    search_params = _params(SearchParams, request.get("search"), limits or {})
    poly = polygon(example["polygon"])
    score = PartitionScore(weight_parts=1.0, **asdict(score_params))
    cfg = config(search_params, score)
    t0 = time.perf_counter()
    trace = T.trace_search(poly, cfg)
    seconds = time.perf_counter() - t0

    pieces = _Pieces()
    best_ids = {n.index for n in trace.best}
    on_path = {n.index for n in T.best_path(trace)}
    nodes = []
    for node in T.walk(trace.root):
        status = T.node_status(node)
        coloured, starts = T.focus(node, trace.root)
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
            "starts": [list(p) for p in starts],
            "chord": _chord(node),
            "best": node.index in best_ids,
            "on_best_path": node.index in on_path,
        })
    references = [{"name": r["name"], "pieces": [pieces.id(polygon(p)) for p in r["parts"]]}
                  for r in example["references"]]
    st = trace.stats
    return {
        "example": request["example"],
        "label": example["label"],
        "polygon": rings(poly),
        "pieces": pieces.items,
        "nodes": nodes,
        "references": references,
        "stats": {
            "seconds": round(seconds, 3),
            "success": bool(st.get("convex_success")),
            "variants": int(st.get("n_variants", 0)),
            "states_seen": int(st.get("n_states_seen", 0)),
            "processed": int(st.get("search_total_states", 0)),
            "failed": int(st.get("n_failed_terminal_states", 0)),
            "terminated_by": st.get("search_terminated_by"),
            "depth_used": st.get("search_depth_used"),
            "width_used": st.get("search_width_used"),
            "attempts": int(st.get("search_attempt_count", 0)),
            "chosen_score": score(st["best_variant"]) if st.get("best_variant") else None,
            "notes": trace.notes,
        },
        "request": {"example": request["example"], "score": asdict(score_params), "search": asdict(search_params)},
    }


def examples() -> List[Dict[str, Any]]:
    return [{"id": e["id"], "label": e["label"], "references": len(e["references"])} for e in EXAMPLES.values()]


def engine(limits: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Which optional settings this engine has, where the examples came from, and the budget's caps."""
    return {
        "options": {option: T.ENGINE.supports(option) for option in OPTIONAL_SETTINGS},
        "examples": SOURCE["examples"],
        "attribution": SOURCE["attribution"],
        "limits": dict(limits or {}),
    }


def call_json(kind: str, payload: str = "null", limits_json: str = "null") -> str:
    """One request as JSON in and JSON out, for the browser worker.

    Errors come back as ``{"error": message, "status": code}`` rather than as a raised exception.
    """
    limits = json.loads(limits_json) or {}
    try:
        if kind == "examples":
            reply: Any = examples()
        elif kind == "engine":
            reply = engine(limits)
        elif kind == "run":
            reply = run(json.loads(payload), limits)
        else:
            raise RequestError(f"unknown request {kind!r}", status=404)
    except RequestError as err:
        reply = {"error": str(err), "status": err.status}
    return json.dumps(reply)
