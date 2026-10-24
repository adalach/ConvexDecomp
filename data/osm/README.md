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

Derived artifacts written here by `notebooks/OSM_1_Preprocessing.ipynb`:

- `subset_buildings_gdf.pkl`: the decomposition working set (`sample_id`, `geometry`, `area_m2`, `n_vertices`, `n_holes`)
- `preprocessing/checks_footprints.csv` and `preprocessing/checks_preprocessed.csv`: one row per footprint with its measurements and failed checks
- `preprocessing/funnel.csv`: one row per footprint with where it left the pipeline and why, and how far preprocessing moved it
- `preprocessing/review_log.csv`: manual review decisions, keyed by footprint fingerprint

The coordinates are local metres (each footprint centred near the origin), although the file's CRS label reads `EPSG:32633`; the notebook drops the label.

To build a different OSM input file in the same minimal public schema, run:

```bash
python -m convexdecomp.osm.sample_buildings --input /path/to/buildings.gpkg
```
