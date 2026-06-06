# OSM Data

This folder stores the bundled OSM footprint case-study input and the preprocessed subset used by the decomposition notebook.

Bundled input:

- `osm_building_footprints_openstreetmap_odbl.gpkg`
  - layer: `footprints`
  - columns: `sample_id`, `geometry`
  - CRS: `EPSG:32633`
  - attribution: OpenStreetMap contributors
  - license: ODbL 1.0
  - note: original OSM identifiers are intentionally removed and replaced with anonymized identifiers such as `osm_0001`

Derived artifact written here:

- `subset_buildings_gdf.pkl` from `notebooks/OSM_1_Preprocessing.ipynb`

To build a different OSM input file in the same minimal public schema, run:

```bash
python -m convexdecomp.osm.sample_buildings --input /path/to/buildings.gpkg
```
