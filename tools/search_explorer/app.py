"""Search explorer: run the convex-decomposition beam search with your own score and budget, and see its tree.

A local web app. The server runs the traced search (`search_trace.trace_search`) on
one example footprint and returns every state the search made, the variants it
found, and each zone's metrics; the page draws the tree and re-ranks the variants
at once when a score slider moves, then re-runs the search, because the score also
decides which states the beam keeps.

    python -m uvicorn app:app --app-dir tools/search_explorer --port 8765

The examples are read from ``data/bem4ai/explorer_examples.json`` (local, not in
the repository: BEM4AI footprints with reference decompositions). Without that
file the app offers the polygon of the EG-ICE 2026 search-tree figure,
``paper_example.wkt`` next to this file.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Literal

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # tools/, for search_trace

import search_trace as T  # noqa: E402  (also puts the repository's src/ on sys.path)
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from shapely import wkt as shapely_wkt  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402
from shapely.geometry.polygon import orient  # noqa: E402

from convexdecomp.core.scores import PartitionScore, zone_metrics  # noqa: E402

EXAMPLES_FILE = T.REPO_ROOT / "data" / "bem4ai" / "explorer_examples.json"
BUILT_IN_EXAMPLE = HERE / "paper_example.wkt"
# Engine settings the page offers that an older engine may not have yet.
OPTIONAL_SETTINGS = ("bisector_always", "min_corner_deg")


def _rings(poly: Polygon) -> Dict[str, Any]:
    poly = orient(poly, 1.0)
    return {"exterior": [[round(x, 4), round(y, 4)] for x, y in poly.exterior.coords[:-1]],
            "holes": [[[round(x, 4), round(y, 4)] for x, y in r.coords[:-1]] for r in poly.interiors]}


def _polygon(rings: Dict[str, Any]) -> Polygon:
    return orient(Polygon(rings["exterior"], rings["holes"]), 1.0)


def load_examples() -> Dict[str, Dict[str, Any]]:
    """The example footprints by id: the local BEM4AI file, or the built-in paper polygon."""
    if EXAMPLES_FILE.exists():
        examples = json.loads(EXAMPLES_FILE.read_text())["examples"]
    else:
        paper = shapely_wkt.loads(BUILT_IN_EXAMPLE.read_text())
        examples = [{"id": "paper_example", "label": "EG-ICE 2026 figure polygon (39.9 m², 1 hole)",
                     "polygon": _rings(paper), "references": []}]
    return {e["id"]: e for e in examples}


EXAMPLES = load_examples()

app = FastAPI(title="Search explorer")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class ScoreParams(BaseModel):
    weight_compactness: float = Field(20.0, ge=0)
    area_weighting: float = Field(1.0, ge=0, le=1)
    weight_rectangularity: float = Field(0.0, ge=0)
    weight_narrow: float = Field(0.0, ge=0)
    narrow_width_m: float = Field(2.0, gt=0)
    weight_small: float = Field(0.0, ge=0)
    small_area_m2: float = Field(10.0, gt=0)


class SearchParams(BaseModel):
    ranking: Literal["reflex_first", "score_first"] = "reflex_first"
    depth: int = Field(24, ge=1, le=60)
    width: int = Field(4, ge=1, le=32)
    max_reflex: int = Field(8, ge=1, le=64)
    max_variants: int = Field(32, ge=1, le=512)
    max_failed: int = Field(500, ge=1, le=5000)
    min_area_m2: float = Field(2.0, ge=0.1)
    snap_m: float = Field(0.1, ge=0)
    adaptive: bool = True
    bisector_always: bool = False
    min_corner_deg: float = Field(0.0, ge=0, le=45)


class RunRequest(BaseModel):
    example: str
    score: ScoreParams = ScoreParams()
    search: SearchParams = SearchParams()


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
            self.items.append({"rings": _rings(poly), "area": m.area, "compactness": m.compactness,
                               "rectangularity": m.rectangularity, "width": m.width, "convex": m.convex})
        return self.index[key]


def _chord(node: T.SearchNode) -> List[List[List[float]]]:
    chord = T.incoming_chord(node)
    if chord is None:
        return []
    return [[[round(x, 4), round(y, 4)] for x, y in g.coords] for g in getattr(chord, "geoms", [chord]) if g.length > 0]


def config(search: SearchParams, score: PartitionScore) -> Any:
    """The engine's settings for one run; the optional ones only if this engine has them."""
    s = search
    settings: Dict[str, Any] = dict(
        search_depth=s.depth, search_width=s.width, max_reflex_per_node=s.max_reflex,
        max_variants_per_polygon=s.max_variants, max_failed_states=s.max_failed, min_area_m2=s.min_area_m2,
        weight_parts=1.0, weight_compactness=score.weight_compactness, cut_snap_vertex_tolerance_m=s.snap_m,
        adaptive_search=T.ENGINE.adaptive_config(
            enabled=s.adaptive, initial_depth=min(4, s.depth), initial_width=min(3, s.width),
            max_depth=s.depth, max_width=s.width, depth_step=1, width_step=1),
        score_fn=score, beam_ranking=s.ranking,
    )
    for option in OPTIONAL_SETTINGS:
        if T.ENGINE.supports(option):
            settings[option] = getattr(s, option)
    return T.ENGINE.decomposer.ConvexDecompositionConfig(**settings)


def run(req: RunRequest) -> Dict[str, Any]:
    example = EXAMPLES.get(req.example)
    if example is None:
        raise HTTPException(404, f"unknown example {req.example!r}")
    poly = _polygon(example["polygon"])
    score = PartitionScore(weight_parts=1.0, **req.score.model_dump())
    cfg = config(req.search, score)
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
    references = [{"name": r["name"], "pieces": [pieces.id(_polygon(p)) for p in r["parts"]]}
                  for r in example["references"]]
    st = trace.stats
    return {
        "example": req.example,
        "label": example["label"],
        "polygon": _rings(poly),
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
        "request": req.model_dump(),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/examples")
def examples() -> List[Dict[str, Any]]:
    return [{"id": e["id"], "label": e["label"], "references": len(e["references"])} for e in EXAMPLES.values()]


@app.get("/api/engine")
def engine() -> Dict[str, Any]:
    """Which optional settings this engine has, and where the examples came from."""
    return {
        "options": {option: T.ENGINE.supports(option) for option in OPTIONAL_SETTINGS},
        "examples": str(EXAMPLES_FILE.relative_to(T.REPO_ROOT)) if EXAMPLES_FILE.exists() else "built-in",
    }


@app.post("/api/run")
def api_run(req: RunRequest) -> Dict[str, Any]:
    return run(req)
