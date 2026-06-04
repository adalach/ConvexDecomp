# Data Layout

This repository expects local data files that are not committed here.

## ResPlan

Expected files:

- `external/resplan/ResPlan.pkl`
- `data/ResPlan/floorplans_aligned_quantized.pickle` after running `notebooks/ResPlan_Preprocessing.ipynb`
- `data/ResPlan/floorplans.pickle` after running `notebooks/ResPlan_FloorplanDecomposition.ipynb`

## OSM

Expected files:

- `data/interim/buildings_gdf.pkl` as the projected input table
- `data/interim/subset_buildings_gdf.pkl` after running `notebooks/OSM_Preprocessing.ipynb`

Use `scripts/sample_osm_buildings.py` to create `data/interim/buildings_gdf.pkl` from a local vector file.
