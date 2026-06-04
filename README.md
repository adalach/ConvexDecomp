# ConvexDecomp

Workflow-oriented convex decomposition of heterogeneous 2D building geometry, extracted into a clean public repository from the larger `FloorplanDecompositionPaper` project.

## Scope

This repository packages the reusable convex-decomposition workflow described in the paper draft *Workflow-Oriented Convex Decomposition of 2D Building Geometry*. The core idea is to treat convex decomposition as a pipeline rather than as a single polygon split:

1. Regularize noisy input geometry.
2. Build dataset-specific decomposition units.
3. Run a shared bounded heuristic search to produce compact convex parts.

The public repo currently covers two case-study pipelines:

- `ResPlan`: room-level preprocessing and convex room splitting.
- `OSM`: footprint normalization, optional perimeter/interior subdivision, and polygon-level convex decomposition.

## Notebook Order

Run the notebooks in stage order:

1. `notebooks/ResPlan_Preprocessing.ipynb`
2. `notebooks/ResPlan_FloorplanDecomposition.ipynb`
3. `notebooks/OSM_Preprocessing.ipynb`
4. `notebooks/OSM_FloorplanDecomposition.ipynb`

The notebooks are orchestrators. The actual reusable logic lives in `notebooks/helpers/` and `external/resplan/resplan_utils.py`.

## Data Expectations

This repository does not ship the original large datasets.

- `external/resplan/ResPlan.pkl` is expected for the ResPlan workflow.
- `data/interim/buildings_gdf.pkl` is expected for the OSM workflow.

See [data/README.md](data/README.md) for the exact file layout and preparation notes.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For the OSM pipeline, first prepare a projected building table:

```bash
python scripts/sample_osm_buildings.py --input /path/to/buildings.gpkg
```

Then run the notebooks.

## Repository Layout

```text
ConvexDecomp/
├── README.md
├── requirements.txt
├── data/
├── external/resplan/
├── notebooks/
│   ├── ResPlan_Preprocessing.ipynb
│   ├── ResPlan_FloorplanDecomposition.ipynb
│   ├── OSM_Preprocessing.ipynb
│   ├── OSM_FloorplanDecomposition.ipynb
│   └── helpers/
└── scripts/
```

## Notes

- `notebooks/helpers/` is preserved close to the original project layout so the notebooks stay readable and runnable with minimal path changes.
- `external/resplan/resplan_utils.py` is vendored because the ResPlan notebooks depend on its plotting and geometry helpers.
- The current public cut focuses on the final clear workflow, not on preserving the full exploratory history of the source repository.
