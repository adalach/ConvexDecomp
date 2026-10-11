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
    ├── resplan/
    └── zones/
```

## Structure

- `src/convexdecomp/core/`: shared convexity checks, partition scoring, and adaptive search.
- `src/convexdecomp/geometry/`: the exact two-piece cuts both adapters use (`cuts.py`) and the splitting of polygons with holes into hole-less pieces (`holes.py`).
- `src/convexdecomp/resplan/`: ResPlan preprocessing, room operations, and decomposition helpers.
- `src/convexdecomp/osm/`: OSM normalization, axis alignment, perimeter preparation, subdivision, decomposition, and input preparation.
- `src/convexdecomp/zones/`: zone provenance records, boundary-interface classification, and the portable two-layer export.
- `src/convexdecomp/diagnostics/`: dataset and search summaries used by the notebooks.
- `external/resplan/`: created locally on first ResPlan run as a dataset source clone; not shipped in the public repo zip.

## Exact Cuts

Every cut the search makes runs from a reflex vertex, along a continued edge, an axis or the interior bisector, to the first point of the boundary it reaches, or along a diagonal between two vertices. The polygon's own vertex list is then split in two at the cut's ends. A cut therefore always gives exactly two pieces that share the cut exactly, and their areas add up to the polygon's: a decomposition covers its polygon with no gap and no overlap. A cut that would leave a piece below the minimum area is refused rather than accepted without the piece. Polygons with holes are first split into hole-less pieces.

Earlier versions cut with `shapely.ops.split` along a long line through the vertex. That can cross a polygon several times, and GEOS occasionally returns the pieces without one of the faces; the OSM adapter also dropped pieces below 0.5 m² and accepted any cut keeping 90% of the area. On the bundled OSM case study this left 147 of the 2,699 buildings reported as fully convex with parts that did not fill the footprint (8 by more than 10 m²). `run_building_decomposition` now reports `area_error_m2` and `building_area_conserved` per building.

## Zone Provenance And Artificial Splits

Convex decomposition splits a space that is not split in reality. The convex parts alone cannot say which of them used to be one physical space, nor which of their walls exist only because of the split. Both decomposition notebooks therefore write a second, portable artifact next to their usual output:

- `data/resplan/floorplans_zones.gpkg`
- `data/osm/osm_zones.gpkg`

Each GeoPackage holds two layers.

`zones` has one row per convex part and carries the provenance chain `container -> space -> zone -> part`:

| column | meaning |
| --- | --- |
| `container_id` | enclosing entity: one floorplan, one building, or one district tile |
| `space_id` | one physical space instance; two bedrooms in one plan get two different values |
| `zone_id` | the polygon that was handed to the decomposer |
| `part_id` | one convex output part |
| `zone_role` | which preparation stage produced the zone, e.g. `room`, `interior`, `perimeter_trapezoid` |
| `space_part_count`, `space_is_split` | how many parts the space was split into, and whether it was split at all |

`interfaces` has one row per boundary sub-segment of one part, which is one row per prospective wall face. A shared wall appears twice, once from each side, with the same `interface_key`. The `boundary_kind` column is the point of the whole artifact:

| kind | meaning |
| --- | --- |
| `exterior` | on the boundary of the decomposed source zone |
| `artificial_split` | a convex-decomposition cut inside one source zone; no wall exists here in reality |
| `zoning_split` | a deliberate zoning cut between zones of one space, e.g. perimeter against interior |
| `space_boundary` | a real boundary between two different spaces |
| `container_boundary` | a boundary between two different containers, e.g. a party wall between buildings |
| `unmatched` | no neighbour found and not on the source boundary, so the parts do not tile their zone exactly |

`is_virtual` marks `artificial_split` and `zoning_split` together. `same_space_group` marks segments whose two sides belong to one physical space.

This is deliberately dataset-independent. `convexdecomp.zones.adapters.build_part_records` accepts any list of decomposed source zones, so a district-scale pipeline gets the same grouping and the same wall classification per building without touching the case-study adapters.

The repository builds no building-energy models and depends on no simulation library. The metadata is what a downstream tool needs to build one: parts sharing a `space_id` can be assigned to a single thermal zone, and faces whose `boundary_kind` is `artificial_split` are the ones that should become virtual partitions rather than opaque walls.

Segments reported as `unmatched` are worth checking rather than ignoring. The decomposition is not guaranteed to produce a perfect planar tiling, so the classifier reports a segment it could not pair instead of silently calling it an outer wall. Both notebooks print the count and the share of boundary length affected.

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
