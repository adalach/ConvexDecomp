# Search explorer

A local web app for tuning the convex decomposition. It runs the beam search on one footprint
with the score and search budget set by sliders, and draws the exact tree of states the search
made, as `tools/search_tree_figure.py` does.

```bash
python -m uvicorn app:app --app-dir tools/search_explorer --port 8765
```

Then open http://localhost:8765. The Python environment needs shapely, fastapi and uvicorn; the
app imports `convexdecomp` from this repository's `src/`.

| Part | What it does |
| --- | --- |
| Score sliders | `convexdecomp.core.scores.PartitionScore`: zones + w_C (1 − mean compactness) + w_R (1 − mean rectangularity) + narrow and small-zone penalties. The area weighting sets how much a zone's area counts in the means (1: by area, 0: every zone the same). Moving a slider re-ranks the found variants at once, then re-runs the search, because the score also decides which states the beam keeps. |
| Search sliders | Beam ranking (reflex vertices first, or score first), the mid-angle cut at every reflex vertex, the smallest corner a cut may create (0 = off), the thinnest zone allowed (a hard rule, 0 = off), the area weighting of the beam's score, the starting beam width (the adaptive budget's first width; it grows to the beam width only while nothing is found), beam width, depth, reflex vertices per piece, variants to find, dead ends before giving up, smallest piece a cut may leave, snap tolerance, adaptive budget. A setting the engine does not have yet is greyed out and not passed on. |
| Tree | Each state with the piece the search cuts next in colour, the rest in grey, and the cut that made it in orange. The ring tells what the search did with it. Click a state to see it large; use the small button to fold a branch, and "+N more" to show all children. |
| Variants | Every fully convex state the search found, plus reference decompositions, ranked by the current score with each term's share. |

| File | Contents |
| --- | --- |
| `explorer_core.py` | The logic, without a server: the examples, the settings with their bounds, one traced run (states, pieces with metrics), and `call_json` for the browser. Needs only the standard library, shapely and the engine. |
| `app.py` | FastAPI server around it: `/api/examples`, `/api/engine` (which optional settings the engine has), `/api/run`. |
| `static/` | The page: `index.html`, `app.js` (d3 7.8.5 from cdnjs), `style.css`, `config.js` (where the searches run: the server), `worker.js` (the same searches in the browser). |
| `build_static.py` | The static build: the page with the searches run in the browser by Pyodide. |
| `paper_example.wkt` | The polygon of the EG-ICE 2026 search-tree figure, offered when the BEM4AI examples are missing. |

## Static build

The explorer also runs without a server: Pyodide (Python compiled to WebAssembly) runs
`explorer_core.py` in a web worker, so any static host can serve it.

```bash
python tools/search_explorer/build_static.py          # writes tmp/search_explorer_site
python -m http.server 8766 --directory tmp/search_explorer_site
```

The build writes the page, a `static/config.js` that switches it to the browser, and
`explorer.zip` (about 60 kB): the repository files `explorer_core` imports, found by importing it in
a fresh Python, and `examples.json`. The browser loads Pyodide 314.0.7 (Python
3.14), numpy and shapely 2.1.2 from the jsDelivr CDN, about 16 MB on the first visit and from the
browser's cache afterwards; starting the worker then takes about 2 s. A search gives the same tree and
scores as on the server and takes about 1.5 times as long.

- **Examples:** six footprints of the anonymized OSM set the repository ships
  (`data/osm/osm_building_footprints_openstreetmap_odbl.gpkg`), chosen by hand (`DEMO_EXAMPLES`, or
  `--examples`), in local coordinates and as mapped (the OSM adapter has not prepared them): from 3 to
  6 reflex corners, a courtyard, and a courtyard building, osm_0975, whose search makes some 2,000
  states in about 6 s in the browser. The page credits OpenStreetMap. `--local-examples` adds the
  local BEM4AI examples.
- **Budget:** capped in the browser (starting and largest beam width 8, depth 30, 16 reflex vertices per piece, 64 variants,
  1,000 dead ends); a search still running after 60 s is stopped and the worker started again. One
  search runs at a time; of the searches asked for meanwhile, only the newest waits.

The examples come from `data/bem4ai/explorer_examples.json`: BEM4AI footprints with reference
decompositions (old engine, hand-made mixes, best of all hole openings). That file is local
and ignored by git; without it the app offers only `paper_example.wkt`.
