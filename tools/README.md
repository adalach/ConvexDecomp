# Tools

Tools for looking inside the convex-decomposition search: what states it made, which it kept,
parked or dropped, and which cut led where. They run against the source tree they live in
(`search_trace.py` puts `src/` first on `sys.path`) and need Python 3.12 or newer, because the
tracer observes the search with `sys.monitoring`.

| File | What it is |
| --- | --- |
| `search_trace.py` | The tracer. `trace_search(polygon, cfg)` runs `convexdecomp.osm.decompose.decompose_polygon_with_stats` and records every state of the search: the cut that made it (edge labels as the engine names them, `r4 prev`, `r4 bis`, `h1 prev + h2 next` for the opening of a hole), the pools it went through (active, overflow, deferred), and what became of it (expanded, success, dead-end, duplicate). The engine is not changed: its functions are wrapped for the length of one call, and `compare_with_untraced` checks that the traced run chose the same variant with the same counts. `bem4ai_notebook_config()` gives the settings BEM4AI notebook 4 runs. |
| `search_tree_figure.py` | Draws a traced search as the tree of the EG-ICE 2026 search-tree figure: a badge per state with the piece being decomposed in colour, the cut on every edge, legend and statistics. PDF, SVG or PNG. |
| `search_explorer/` | A local web app that runs the traced search with the score and search settings on sliders and shows the tree and the ranked variants. See its README. |
| `import_bem4ai_footprints.py` | Draws a random sample of the BEM4AI Stage 3 footprints and writes it as an input of `OSM_1_Preprocessing.ipynb`: the OSM outline as mapped, read again from BEM4AI's city extract and checked against the fingerprint BEM4AI recorded, plus BEM4AI's cleaned footprint, both in a local frame. Runs in BEM4AI's own environment, because it uses BEM4AI's reader, repair and fingerprint. |
| `osm_adapter_figures.py` | Runs the OSM preprocessing (level L2, the notebook's settings) on the BEM4AI sample, keeps every intermediate geometry, and writes the figures of the OSM adapter note (SVG, TUM style) and `osm_adapter_numbers.json` with the counts it quotes. `--types` takes a CSV of `sample_id` and `building_type` from BEM4AI for the composition figure. |

## Search-tree figure

```bash
# a WKT string or a .wkt, .geojson or .wkb file
python tools/search_tree_figure.py --input footprint.wkt -o tree.pdf tree.png

# a BEM4AI decomposition input: building and input index in data/bem4ai/decomposition_inputs.gpkg
python tools/search_tree_figure.py --sample-id Gorzow_Wielkopolski_0007 --input-index 2 -o gorzow.pdf
```

The settings default to those BEM4AI notebook 4 runs; `--depth`, `--width`, `--min-area`,
`--ranking`, `--bisector-always`, `--min-corner` and others override them. Large trees are cut
down to `--max-nodes` states (default 60, `0` for all) and `--max-children` per state, with the
path to the chosen variant always kept and what is left out shown as "+N more". `--attempt K`
draws the tree as it stood after budget attempt K. Every run compares the traced search with an
untraced one and prints the result.

## BEM4AI footprints

```bash
/path/to/BEM4AI/.venv/bin/python tools/import_bem4ai_footprints.py --bem4ai /path/to/BEM4AI --n 1000
```

Writes `data/bem4ai/osm_footprints_bem4ai_sample.gpkg` (layers `footprints` and `footprints_bem4ai`).
The sample leaves out the footprints BEM4AI's review log removes and, unless
`--keep-review-selections` is given, those a review check selected; `--n 0` takes them all.

## Search explorer

```bash
python -m uvicorn app:app --app-dir tools/search_explorer --port 8765
```

## Data

`data/bem4ai/` (local, ignored by git) holds the polygons BEM4AI notebook 4 decomposes,
`decomposition_inputs.gpkg`, and the explorer's examples, `explorer_examples.json`. The figure
tool reads the first with `--sample-id`; the explorer falls back to a built-in polygon when the
second is missing.

## Tests

`tests/test_search_trace.py` checks that traced and untraced runs agree on an L shape, a
courtyard and a bent band, that every recorded success covers its polygon, and that the
explorer's `/api/run` returns such a tree (with FastAPI's `TestClient`, skipped without it).
