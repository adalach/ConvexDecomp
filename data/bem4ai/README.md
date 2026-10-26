# BEM4AI footprint sample

The input of the OSM notebooks: 1,000 building footprints from 528 cities in 27 European countries, drawn from the
[BEM4AI](https://github.com/adalach/BEM4AI) dataset. The footprints are derived from OpenStreetMap
contributors data and are redistributed here under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/):
© OpenStreetMap contributors.

## Shipped with the repository

`osm_footprints_bem4ai_sample.gpkg`, read by `notebooks/OSM_1_Preprocessing.ipynb`: 1,000 footprints drawn at
random (seed 42) from BEM4AI's Stage 3 footprints, without the footprints BEM4AI's review log removes or a
review check selects. It holds geometry only: no place names and no OpenStreetMap or BEM4AI identifiers.

| Layer | Contents |
| --- | --- |
| `footprints` | The OSM outline as mapped (WGS84, `EPSG:4326`, not repaired), one row per footprint: `sample_id` (`osm_0001` ... `osm_1000`, in a random order) and `metric_crs` (the UTM zone the adapter projects the footprint to in its local-frame step) |
| `footprints_bem4ai` | The same footprints as BEM4AI stores them after its Stage 3 cleanup (WGS84), `sample_id` and geometry; the adapter compares them with the OSM outlines |

The ids are this file's own: `osm_0001` here and `osm_0001` in `data/osm/` are different buildings.

`tools/import_bem4ai_footprints.py` draws the sample from a BEM4AI checkout into
`osm_footprints_bem4ai_source.gpkg`, and `tools/anonymize_bem4ai_sample.py` writes this file from it.

## Written by the notebooks and tools (local, ignored by git)

| File | Contents |
| --- | --- |
| `osm_footprints_bem4ai_source.gpkg` | The sample as `tools/import_bem4ai_footprints.py` writes it, with BEM4AI's ids, the OpenStreetMap element ids, the city and country of every footprint and BEM4AI's cleanup measures |
| `sample_ids.csv` | The map from `osm_0001` ... to BEM4AI's `sample_id` and `source_sample_id`, written by `tools/anonymize_bem4ai_sample.py` |
| `subset_buildings_gdf.pkl` | The decomposition working set of the sample, written by `OSM_1_Preprocessing.ipynb` and read by `OSM_2_Decomposition.ipynb` (`OSM_DATASET = 'bem4ai_sample'`) |
| `preprocessing/` | The checks tables of steps 4 and 7 and the funnel of the sample (`OSM_1_Preprocessing.ipynb`) |
| `osm_zones.gpkg` | The zone provenance and interfaces of the sample (`OSM_2_Decomposition.ipynb`) |
| `decomposition_inputs.gpkg`, layer `inputs` | The polygons BEM4AI's decomposition stage hands to the decomposer, in local metres (`sample_id`, `input`, `holes`, `vertices`, `area_m2`); read by `tools/search_tree_figure.py --sample-id` |
| `explorer_examples.json` | The search explorer's local examples, with reference decompositions |
