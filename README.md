# ConvexDecomp

Workflow-oriented convex decomposition of heterogeneous 2D building geometry.

This repository accompanies the paper *Workflow-Oriented Convex Decomposition of 2D Building Geometry*. It frames convex decomposition as a reusable workflow that couples dataset-specific preprocessing with a shared bounded heuristic search, rather than as a standalone polygon split.

## Source Credit

This repository is a workflow layer around two upstream data sources that are not created by `ConvexDecomp`:

- `ResPlan` data comes from [m-agour/ResPlan](https://github.com/m-agour/ResPlan) and retains its upstream attribution and licensing terms.
- `OSM` case-study footprints are derived from OpenStreetMap contributors data, redistributed here in a reduced anonymized form under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).

`ConvexDecomp` contributes the workflow design, preprocessing logic, decomposition code, and the paper-specific orchestration around those sources.

## Scope

The repository exposes two case-study adapters built around one shared decomposition core:

- `ResPlan`: residential floorplans with semantic room structure and room-level convex decomposition.
- `OSM`: OpenStreetMap building footprints with artifact filtering, integrated normalization plus conservative edge regularization, optional perimeter preparation, and polygon-level convex decomposition.

The public notebooks are stage-level orchestrators. Reusable implementation lives in `src/convexdecomp/`, while the ResPlan notebook clones the upstream repository into a local `external/resplan/` runtime folder only when the dataset archive source is missing.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

The repository already ships the anonymized OSM case-study dataset used in the paper, so you can run the notebooks directly. On the first ResPlan run, the preprocessing notebook will clone the upstream `m-agour/ResPlan` repository into `external/resplan/` if that archive source is still missing, then extract `ResPlan.pkl` from `ResPlan.zip`. It requires `git` and network access:

1. `notebooks/ResPlan_1_Preprocessing.ipynb`
2. `notebooks/ResPlan_2_Decomposition.ipynb`
3. `notebooks/OSM_1_Preprocessing.ipynb`
4. `notebooks/OSM_2_Decomposition.ipynb`

If you want to prepare an alternative OSM footprint input in the same public format, use:

```bash
python -m convexdecomp.osm.sample_buildings --input /path/to/buildings.gpkg
```

Use `--no-overwrite-existing` if you want the converter to refuse replacing an existing output GeoPackage.

The notebook bootstrap locates the project root from `pyproject.toml`, `notebooks/`, and `src/convexdecomp/`, so extracted archive copies work without git metadata.

## Data Contract

The OSM case-study input is included directly. The ResPlan raw dataset is fetched on demand from the upstream public repository, but the public workflow now uses only local `ConvexDecomp` helper code.

- `notebooks/ResPlan_1_Preprocessing.ipynb` clones `https://github.com/m-agour/ResPlan.git` into `external/resplan/` only when that local archive source is missing, then extracts `ResPlan.zip` into `external/resplan/ResPlan.pkl`.
- The OSM notebook pipeline reads the bundled GeoPackage `data/osm/osm_building_footprints_openstreetmap_odbl.gpkg`, which is an anonymized reduced derivative of OpenStreetMap contributors data rather than an original dataset created in this repository.
- Derived notebook outputs are written under `data/resplan/` and `data/osm/`. For the bundled OSM case study, the preprocessing notebook matches the paper by exporting the still non-convex cleaned footprints with 6 to 32 exterior vertices as the downstream decomposition working set, while rendering the matched original-vs-final working-set comparison through the dedicated plotting module `src/convexdecomp/plotting/osm_working_set.py`.

See `data/README.md` for the expected local layout.

## Repository Layout

```text
ConvexDecomp/
├── README.md
├── pyproject.toml
├── requirements.txt
├── data/
│   ├── osm/
│   └── resplan/
├── notebooks/
│   ├── ResPlan_1_Preprocessing.ipynb
│   ├── ResPlan_2_Decomposition.ipynb
│   ├── OSM_1_Preprocessing.ipynb
│   └── OSM_2_Decomposition.ipynb
└── src/convexdecomp/
    ├── core/
    ├── diagnostics/
    ├── geometry/
    ├── io/
    ├── osm/
    ├── plotting/
    └── resplan/
```

## Structure

- `src/convexdecomp/core/`: shared convexity checks, partition scoring, and adaptive search.
- `src/convexdecomp/resplan/`: ResPlan preprocessing, room operations, and decomposition helpers.
- `src/convexdecomp/osm/`: OSM normalization, axis alignment, perimeter preparation, subdivision, decomposition, and input preparation.
- `src/convexdecomp/diagnostics/`: dataset and search summaries used by the notebooks.
- `external/resplan/`: created locally on first ResPlan run as a dataset source clone; not shipped in the public repo zip.

## Citation And License

Repository citation metadata is provided in `CITATION.cff`. The repository text, code, and original notebook content are released under `CC BY 4.0` in `LICENSE`.

The associated paper is:

- Dalach, Agata; Chen, Xia; Borrmann, André. *Workflow-Oriented Convex Decomposition of 2D Building Geometry*. Proc. of the 33rd International Workshop on Intelligent Computing in Engineering, 2026. Landing page: [mediatum.ub.tum.de/node?id=1855368](https://mediatum.ub.tum.de/node?id=1855368), PDF: [mediatum.ub.tum.de/doc/1855368/1855368.pdf](https://mediatum.ub.tum.de/doc/1855368/1855368.pdf)

Third-party source assets keep their own licensing terms:

- The ResPlan notebook preserves the upstream attribution path by cloning the public repository only when the local dataset archive source is missing.
- `data/osm/osm_building_footprints_openstreetmap_odbl.gpkg` is derived from OpenStreetMap data, contains only anonymized `sample_id` values plus footprint geometry, and is redistributed with attribution to OpenStreetMap contributors under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).

## Plotting Backends

- `ResPlan_1_Preprocessing.ipynb` and `ResPlan_2_Decomposition.ipynb` force Matplotlib `inline` output so the figures stay simple and static.
- `OSM_1_Preprocessing.ipynb` and `OSM_2_Decomposition.ipynb` try the Jupyter `widget` backend first and fall back to `inline` when interactive widgets are unavailable.
- The repository now includes `ipympl` so the OSM notebooks can use the widget backend in environments that support it.
