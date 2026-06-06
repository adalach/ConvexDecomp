# Data Layout

This repository expects one local upstream dataset and ships one public case-study input directly.

## ResPlan

Bootstrap-managed local input:

- `external/resplan/ResPlan.pkl`

On first run, `notebooks/ResPlan_1_Preprocessing.ipynb` will:

- clone `https://github.com/m-agour/ResPlan.git` into `external/resplan/` when that local dataset source is missing
- read `ResPlan.zip` from the local upstream clone
- extract `ResPlan.pkl` into `external/resplan/`

This one-time bootstrap requires `git` and network access.

The ResPlan dataset is not created by `ConvexDecomp`. It comes from the upstream [m-agour/ResPlan](https://github.com/m-agour/ResPlan) project and keeps its original attribution and licensing terms.

Derived notebook outputs:

- `data/resplan/floorplans_aligned_quantized.pickle` from `notebooks/ResPlan_1_Preprocessing.ipynb`
- `data/resplan/floorplans.pickle` from `notebooks/ResPlan_2_Decomposition.ipynb`

These derived files are local runtime artifacts and are not shipped in the public release zip.

## OSM

Bundled public input:

- `data/osm/osm_building_footprints_openstreetmap_odbl.gpkg`
  - layer name: `footprints`
  - columns: `sample_id`, `geometry`
  - CRS: `EPSG:32633`
  - attribution: OpenStreetMap contributors
  - license: ODbL 1.0
  - note: this is a reduced derived dataset, not an original `ConvexDecomp` creation; original OSM identifiers are not included, and the file uses anonymized identifiers `osm_0001`, `osm_0002`, ...

Derived notebook outputs:

- `data/osm/subset_buildings_gdf.pkl` from `notebooks/OSM_1_Preprocessing.ipynb`

This derived file is a local runtime artifact and is not shipped in the public release zip.

If you want to create an alternative OSM input table in the same public format, use:

```bash
python -m convexdecomp.osm.sample_buildings --input /path/to/buildings.gpkg
```
