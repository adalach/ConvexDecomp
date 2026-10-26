# Tools

Tools for looking inside the convex-decomposition search: what states it made, which it kept,
parked or dropped, and which cut led where. They run against the source tree they live in
(`search_trace.py` puts `src/` first on `sys.path`) and need Python 3.12 or newer, because the
tracer observes the search with `sys.monitoring`.

| File | What it is |
| --- | --- |
| `search_trace.py` | The tracer, `convexdecomp.osm.search_trace`, with this checkout's `src/` first on `sys.path`. `trace_search(polygon, cfg)` runs `convexdecomp.osm.decompose.decompose_polygon_with_stats` and records every state of the search: the cut that made it (edge labels as the engine names them, `r4 prev`, `r4 bis`, `h1 prev + h2 next` for the opening of a hole), the pools it went through (active, overflow, deferred), and what became of it (expanded, success, dead-end, duplicate). The engine is not changed: its functions are wrapped for the length of one call, and `compare_with_untraced` checks that the traced run chose the same variant with the same counts. `bem4ai_notebook_config()` gives the search budget BEM4AI notebook 4 runs, with this engine's defaults for the rest. |
| `search_tree_figure.py` | Draws a traced search as the tree of the EG-ICE 2026 search-tree figure: a badge per state with the piece being decomposed in colour, the cut on every edge, legend and statistics. PDF, SVG or PNG. |
| `search_explorer/` | A local web app that runs the traced search with the score and search settings on sliders and shows the tree and the ranked variants; `build_static.py` builds it as a static site that runs the search in the browser with Pyodide. See its README. |
| `import_bem4ai_footprints.py` | Draws a random sample of the BEM4AI Stage 3 footprints into `data/bem4ai/osm_footprints_bem4ai_source.gpkg` (local): the OSM outline as mapped (WGS84, not repaired), read again from BEM4AI's city extract and checked against the fingerprint BEM4AI recorded, plus BEM4AI's cleaned footprint, with BEM4AI's ids, the OSM element ids, the city and the metric CRS of each footprint. Runs in BEM4AI's own environment, because it uses BEM4AI's reader and fingerprint. |
| `anonymize_bem4ai_sample.py` | Writes the copy of that sample the repository ships and `OSM_1_Preprocessing.ipynb` reads, `data/bem4ai/osm_footprints_bem4ai_sample.gpkg`: geometry and metric CRS only, with the ids `osm_0001` ... in a random order; the map back to BEM4AI's ids goes to the local `data/bem4ai/sample_ids.csv`. |
| `osm_adapter_figures.py` | Runs the OSM adapter (`run_adapter`, level L2, the notebook's settings) on the BEM4AI sample, and writes the figures of the OSM adapter note (SVG, TUM style), the study of the wall-line tolerance with the depths of the facade steps, and `osm_adapter_numbers.json` with the counts the note quotes. |

## Search-tree figure

```bash
# a WKT string or a .wkt, .geojson or .wkb file
python tools/search_tree_figure.py --input footprint.wkt -o tree.pdf tree.png

# a BEM4AI decomposition input: building and input index in data/bem4ai/decomposition_inputs.gpkg
python tools/search_tree_figure.py --sample-id <sample_id> --input-index 2 -o tree.pdf
```

The settings default to the budget BEM4AI notebook 4 runs, with this engine's defaults for the rest; `--depth`, `--width`, `--min-area`,
`--ranking`, `--bisector-always`, `--min-corner` and others override them. Large trees are cut
down to `--max-nodes` states (default 60, `0` for all) and `--max-children` per state, with the
path to the chosen variant always kept and what is left out shown as "+N more". `--attempt K`
draws the tree as it stood after budget attempt K. Every run compares the traced search with an
untraced one and prints the result.

## BEM4AI footprints

```bash
/path/to/BEM4AI/.venv/bin/python tools/import_bem4ai_footprints.py --bem4ai /path/to/BEM4AI --n 1000
python tools/anonymize_bem4ai_sample.py
```

The first writes `data/bem4ai/osm_footprints_bem4ai_source.gpkg` (layers `footprints` and `footprints_bem4ai`,
local), the second the anonymized `data/bem4ai/osm_footprints_bem4ai_sample.gpkg` the notebooks read.
The sample leaves out the footprints BEM4AI's review log removes and, unless
`--keep-review-selections` is given, those a review check selected; `--n 0` takes them all.

## Corner-ray perimeter

`corner_perimeter/` runs and draws `convexdecomp.corner_perimeter` (see its README) on the BEM4AI footprint
sample, by default the snapshot `temp/perimeter/subset_snapshot.pkl` the method was measured on (`--sample` takes
another pickle or a GeoPackage with `sample_id`). Results go to `temp/corner_perimeter/` unless `--out` says
otherwise.

| File | What it is |
| --- | --- |
| `note_figures.py` | The figures of the package README and of the note "Convex Decomposition Perimeter", one SVG per step (`--out src/convexdecomp/corner_perimeter/figures` or the note's attachments, `--png` small previews). |
| `pipeline_diagram.py` | The pipeline diagram of the package README (`src/convexdecomp/corner_perimeter/figures/perimeter_pipeline.svg`). |
| `run_sample.py` | Every footprint of the sample with the chosen settings or others (`--set wing_end_max_m=0`): `run.csv`, `zones.gpkg`, `run.log` with the totals, `--sheets` for contact sheets of the worst and of random footprints. |
| `compare_variants.py` | The chosen settings against variants with one choice changed, on the 23 footprints with the most reflex corners (`--n 0`: all); `--draw <variant>` draws the footprints where it differs. |
| `overview.py` | Lines and zones of footprints side by side, with how often each step acted (default: the three test footprints). |
| `drawing.py` | The shared drawing: styles, legends, windows, lines and zones of a result, the bare construction of the rays. |

```bash
python tools/corner_perimeter/run_sample.py --sheets
python tools/corner_perimeter/note_figures.py --out "/path/to/Convex Decomposition/attachments"
```

## Search explorer

```bash
python -m uvicorn app:app --app-dir tools/search_explorer --port 8765

# the same page as a static site, the search run in the browser
python tools/search_explorer/build_static.py
python -m http.server 8766 --directory tmp/search_explorer_site
```

## Data

`data/bem4ai/` holds the footprint sample the OSM notebooks run on, `osm_footprints_bem4ai_sample.gpkg`
(in the repository), and two local files ignored by git: the polygons BEM4AI notebook 4 decomposes,
`decomposition_inputs.gpkg`, and the explorer's examples, `explorer_examples.json`. The figure
tool reads the first with `--sample-id`; the explorer offers the demo's footprints
(`search_explorer/demo_examples.json`) when the second is missing.

## Tests

`tests/test_corner_perimeter.py` and `tests/test_corner_perimeter_tidy.py` check the corner-ray perimeter on nine real
footprints, the wing ends, bays, the tidy step, simple shapes, every stage, the settings and the preview;
`tests/test_perimeter_zones.py` its run over a table of footprints for the OSM decomposition notebook.

`tests/test_search_trace.py` checks that traced and untraced runs agree on an L shape, a
courtyard and a bent band, that every recorded success covers its polygon, and that the
explorer's `/api/run` returns such a tree (with FastAPI's `TestClient`, skipped without it). It also runs the explorer's logic without a server, and the static build from its own zip in a fresh Python.
