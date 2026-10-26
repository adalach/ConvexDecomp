# Notebook Pipelines

The public workflow is split across four notebooks because the paper presents two adapters and two main stages: preprocessing and decomposition.

Both adapters work on upstream source data rather than on datasets created in this repository: the ResPlan notebooks build on [m-agour/ResPlan](https://github.com/m-agour/ResPlan), while the OSM notebooks use a sample of 1,000 OpenStreetMap footprints drawn from the [BEM4AI](https://github.com/adalach/BEM4AI) dataset (`data/bem4ai/osm_footprints_bem4ai_sample.gpkg`, ODbL 1.0, © OpenStreetMap contributors).

## Notebook Order

1. `ResPlan_1_Preprocessing.ipynb`
2. `ResPlan_2_Decomposition.ipynb`
3. `OSM_1_Preprocessing.ipynb`
4. `OSM_2_Decomposition.ipynb`

## Roles

- `ResPlan_1_Preprocessing.ipynb`: upstream dataset bootstrap, local dataset-source clone bootstrap of `m-agour/ResPlan` into `external/resplan/`, area-based metric conversion for image-traced plans, room extraction, architectural-axis regularization through `src/convexdecomp/resplan/axes.py`, axis-grid reconstruction through `src/convexdecomp/resplan/axis_grid.py`, and final working-set preparation that intentionally keeps only the more challenging multi-concave cases, with one representative static showcase panel. Small preview grids skip empty split-plan entries instead of failing.
- `ResPlan_2_Decomposition.ipynb`: room-level convex decomposition with the shared search core and ResPlan-specific split rules, a single aligned-vs-convex showcase comparison with room-variant rows for the primary example, dataset-level convex-coverage summaries through `src/convexdecomp/resplan/decompose.py`, and a zone provenance export to `data/resplan/floorplans_zones.gpkg` that records which convex parts belong to one room instance and which of their walls are artificial splits.
- `OSM_1_Preprocessing.ipynb`: the OSM adapter (`convexdecomp.osm.preprocess.run_adapter`) in eight steps: import, local frame, duplicate and collinear vertices, thin parts, architectural axes, normalization to the axes, the checks of `config/checks.json`, and the working set `data/bem4ai/subset_buildings_gdf.pkl`. One cell per step shows what it changed, with the footprints it changed most drawn before and after (`src/convexdecomp/plotting/osm_preprocessing.py`); the checks tables and the funnel of every footprint go to `data/bem4ai/preprocessing/`.
- `OSM_2_Decomposition.ipynb`: the corner-ray perimeter (`convexdecomp.corner_perimeter`, perimeter zones along the facades and core parts), polygon-level convex decomposition of every zone with the merge fallback for zones the search cannot cut (`run_building_decomposition(..., fallback='merge')`), an area check at every stage, failure and small-zone diagnostics, and comparison grids across the vertex-count range, saved as four PDFs under `tmp/osm_2_decomposition_exports/` (`nonperim_original_grid.pdf`, `nonperim_convex_grid.pdf`, `perim_original_grid.pdf`, `perim_convex_grid.pdf`). A closing section exports zone provenance and boundary interfaces to `osm_zones.gpkg` in the data folder, separating deliberate zoning cuts from convex-decomposition artifacts. It runs on the BEM4AI sample (`OSM_DATASET = 'bem4ai_sample'`) or on the anonymized set of the EG-ICE 2026 paper (`'paper'`).

The notebooks are orchestrators. Reusable implementation lives in `src/convexdecomp/`, including shared project/bootstrap helpers in `src/convexdecomp/notebook_utils.py`, while the notebooks handle parameter setup, summaries, figures, and the one-time ResPlan local dataset-source clone plus archive extraction for local runs. All four notebooks draw `inline`, so their saved outputs keep the figures.
