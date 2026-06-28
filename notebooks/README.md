# Notebook Pipelines

The public workflow is split across four notebooks because the paper presents two adapters and two main stages: preprocessing and decomposition.

Both adapters work on upstream source data rather than on datasets created in this repository: the ResPlan notebooks build on [m-agour/ResPlan](https://github.com/m-agour/ResPlan), while the OSM notebooks use an anonymized reduced derivative of OpenStreetMap contributors data under ODbL 1.0.

## Notebook Order

1. `ResPlan_1_Preprocessing.ipynb`
2. `ResPlan_2_Decomposition.ipynb`
3. `OSM_1_Preprocessing.ipynb`
4. `OSM_2_Decomposition.ipynb`

## Roles

- `ResPlan_1_Preprocessing.ipynb`: upstream dataset bootstrap, local dataset-source clone bootstrap of `m-agour/ResPlan` into `external/resplan/`, area-based metric conversion for image-traced plans, room extraction, architectural-axis regularization through `src/convexdecomp/resplan/axes.py`, axis-grid reconstruction through `src/convexdecomp/resplan/axis_grid.py`, and final working-set preparation that intentionally keeps only the more challenging multi-concave cases, with one representative static showcase panel. Small preview grids skip empty split-plan entries instead of failing.
- `ResPlan_2_Decomposition.ipynb`: room-level convex decomposition with the shared search core and ResPlan-specific split rules, a single aligned-vs-convex showcase comparison with room-variant rows for the primary example, and dataset-level convex-coverage summaries through `src/convexdecomp/resplan/decompose.py`.
- `OSM_1_Preprocessing.ipynb`: footprint filtering, review-case inspection, raw-input and final-working-set vertex and direct edge-direction summaries with zero angular grouping tolerance, a dedicated helper-based matched original-vs-final working-set comparison figure from `src/convexdecomp/plotting/osm_working_set.py` with stacked count bars and transition heatmaps, integrated normalization plus conservative edge regularization, readable representative stage plots whose aligned-family panel is rendered through the shared `convexdecomp.osm.diagnostics` helper while reusing the aligned geometry with a different axis overlay, and final export of the OSM decomposition working set of still non-convex footprints with 6 to 32 exterior vertices.
- `OSM_2_Decomposition.ipynb`: optional perimeter preparation, polygon-level convex decomposition, polygon-failure diagnostics, and case-study comparison grids sampled across the vertex-count range through `src/convexdecomp/osm/diagnostics.py`. The final comparison-grid cell now also saves four PDF exports under `tmp/osm_2_decomposition_exports/` when rerun.

The notebooks are orchestrators. Reusable implementation lives in `src/convexdecomp/`, including shared project/bootstrap helpers in `src/convexdecomp/notebook_utils.py`, while the notebooks handle parameter setup, summaries, figures, and the one-time ResPlan local dataset-source clone plus archive extraction for local runs. The ResPlan notebooks force simple `inline` plotting, while the OSM notebooks try Matplotlib's Jupyter `widget` backend first and fall back to `inline` when interactive widgets are unavailable.
