"""Search explorer: run the convex-decomposition beam search with your own score and budget, and see its tree.

A local web app. The server runs the traced search (`search_trace.trace_search`) on
one example footprint and returns every state the search made, the variants it
found, and each zone's metrics; the page draws the tree and re-ranks the variants
at once when a score slider moves, then re-runs the search, because the score also
decides which states the beam keeps.

    python -m uvicorn app:app --app-dir tools/search_explorer --port 8765

The logic is in `explorer_core.py`, which the static build (`build_static.py`)
runs in the browser instead. The examples are read from
``data/bem4ai/explorer_examples.json`` (local, not in the repository: BEM4AI
footprints with reference decompositions). Without that file the app offers the
polygon of the EG-ICE 2026 search-tree figure, ``paper_example.wkt`` next to
this file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import explorer_core as core  # noqa: E402
from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

EXAMPLES = core.EXAMPLES  # the same dictionary: examples added here are runnable
_rings = core.rings

app = FastAPI(title="Search explorer")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/examples")
def examples() -> List[Dict[str, Any]]:
    return core.examples()


@app.get("/api/engine")
def engine() -> Dict[str, Any]:
    """Which optional settings this engine has, and where the examples came from."""
    return core.engine()


@app.post("/api/run")
def api_run(req: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return core.run(req)
    except core.RequestError as err:
        raise HTTPException(err.status, str(err)) from err
