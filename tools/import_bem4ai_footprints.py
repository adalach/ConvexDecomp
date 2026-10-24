#!/usr/bin/env python3
"""Import a random sample of the BEM4AI Stage 3 footprints as an OSM input of ConvexDecomp.

BEM4AI Stage 3 (notebook 3, ``data/interim/buildings_gdf.pkl``) draws its buildings from real OSM
footprints and stores each footprint after its own cleanup (parts under 1 m cut off, thin holes
filled, collinear vertices dropped). ConvexDecomp's OSM preprocessing is to start from the outline
as it is mapped, so this tool reads that outline again from the same city extract BEM4AI drew it
from (``data/city-assets/OSM_PBF/<city>.osm.pbf``) and checks it against the footprint
fingerprint BEM4AI recorded before its cleanup (BEM4AI fingerprints the outline after
``make_geometry_valid``, which leaves a valid outline as it is).

Only the 2D geometry is kept, in WGS84 as BEM4AI stores it, with the metric CRS of the city
(``metric_crs``, the UTM zone of BEM4AI's candidate tables) that the adapter projects it to. The
columns carry BEM4AI's names. Two layers:

- ``footprints``: the OSM outline as mapped (not repaired), read by ``OSM_1_Preprocessing.ipynb``
- ``footprints_bem4ai``: the same footprint after the BEM4AI Stage 3 cleanup, for comparison

The sample leaves out the footprints the review log removes at stage ``footprints``, and by default
also those a review check selected and nobody kept, since BEM4AI is likely to drop them.

The tool imports BEM4AI's own helpers (pyrosm reader, geometry repair, fingerprint), so it runs in
BEM4AI's environment::

    /path/to/BEM4AI/.venv/bin/python tools/import_bem4ai_footprints.py --bem4ai /path/to/BEM4AI --n 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "bem4ai" / "osm_footprints_bem4ai_sample.gpkg"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bem4ai", required=True, type=Path, help="BEM4AI repository root")
    parser.add_argument("--n", type=int, default=1000, help="footprints to draw (0 = all eligible)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--keep-review-selections", action="store_true",
                        help="keep the footprints a review check selected and nobody kept")
    return parser.parse_args()


def last_footprint_decisions(log_path: Path) -> pd.DataFrame:
    """The latest decision of the review log per footprint at stage ``footprints``."""
    log = pd.read_csv(log_path)
    log = log.loc[log["stage"] == "footprints"].sort_values("marked_at")
    return log.drop_duplicates("source_sample_id", keep="last").set_index("source_sample_id")


def eligible_footprints(buildings: pd.DataFrame, decisions: pd.DataFrame, keep_review_selections: bool) -> pd.DataFrame:
    """The Stage 3 sample without the footprints the review removes (or is likely to remove)."""
    decision = buildings["source_sample_id"].map(decisions["decision"])
    by = buildings["source_sample_id"].map(decisions["by"])
    same_outline = buildings["source_sample_id"].map(decisions["fingerprint"]).eq(buildings["footprint_fingerprint"])
    removed = decision.eq("remove") & same_outline
    if keep_review_selections:
        removed &= ~by.eq("check_review")
    return buildings.loc[~removed]


def read_osm_outlines(pbf_path: Path, wanted: pd.DataFrame, helpers) -> dict[str, object]:
    """``source_sample_id`` -> the OSM outline (WGS84, as mapped) of the wanted buildings of one extract."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        city_buildings = helpers.load_city_buildings(pbf_path)
    keys = set(zip(wanted["building_id"].astype("int64"), wanted["osm_type"].astype(str)))
    ids = city_buildings["id"].astype("int64").to_numpy()
    types = city_buildings["osm_type"].astype(str).to_numpy()
    hit = np.array([(i, t) in keys for i, t in zip(ids, types)], dtype=bool)
    by_key = {(int(i), str(t)): g for i, t, g in zip(ids[hit], types[hit], city_buildings.geometry.to_numpy()[hit])}
    return {sid: by_key.get((int(i), str(t)))
            for sid, i, t in zip(wanted["source_sample_id"], wanted["building_id"], wanted["osm_type"])}


def main() -> int:
    args = parse_args()
    bem4ai = args.bem4ai.resolve()
    sys.path.insert(0, str(bem4ai / "notebooks"))
    try:
        from helpers import building_sampling, review_log, utils
    except ImportError as error:
        raise SystemExit(f"Run this tool with BEM4AI's own Python; could not import its helpers: {error}")

    class helpers:  # the three BEM4AI functions this tool relies on
        load_city_buildings = staticmethod(building_sampling.load_city_buildings)
        make_geometry_valid = staticmethod(utils.make_geometry_valid)
        footprint_fingerprint = staticmethod(review_log.footprint_fingerprint)

    interim = bem4ai / "data" / "interim"
    buildings = pd.read_pickle(interim / "buildings_gdf.pkl")
    decisions = last_footprint_decisions(bem4ai / "data" / "review" / "review_log.csv")
    manifest = json.loads((interim / "footprint_candidates" / "manifest.json").read_text())["cities"]

    eligible = eligible_footprints(buildings, decisions, args.keep_review_selections)
    print(f"BEM4AI Stage 3 sample: {len(buildings)} footprints; eligible after the review log: {len(eligible)}")
    sample = eligible if not args.n or args.n >= len(eligible) else eligible.sample(n=args.n, random_state=args.seed)
    sample = sample.sort_values("sample_id").reset_index(drop=True)
    print(f"Drawn: {len(sample)} footprints from {sample['city_key'].nunique()} cities (seed {args.seed})")

    start = time.perf_counter()
    raw_wgs84: dict[str, object] = {}
    for city_key, city_rows in sample.groupby("city_key", sort=True):
        raw_wgs84.update(read_osm_outlines(bem4ai / "data" / "city-assets" / "OSM_PBF" / city_rows["osm_pbf"].iloc[0],
                                           city_rows, helpers))
    print(f"Read the OSM outlines from {sample['city_key'].nunique()} city extracts in {time.perf_counter() - start:.0f} s")

    sample["geometry_osm"] = sample["source_sample_id"].map(raw_wgs84)
    missing = sample["geometry_osm"].isna()
    if missing.any():
        raise SystemExit(f"{int(missing.sum())} footprints not found in their extracts: "
                         f"{sample.loc[missing, 'source_sample_id'].tolist()[:10]}")
    invalid = ~sample["geometry_osm"].map(lambda g: g.is_valid)
    print(f"Outlines not valid as mapped: {int(invalid.sum())}")
    fingerprints = sample["geometry_osm"].map(lambda g: helpers.footprint_fingerprint(helpers.make_geometry_valid(g)))
    differ = fingerprints.ne(sample["footprint_fingerprint"])
    if differ.any():
        raise SystemExit(f"{int(differ.sum())} outlines differ from the fingerprint BEM4AI recorded, "
                         f"e.g. {sample.loc[differ, 'source_sample_id'].tolist()[:10]}; was the extract updated?")
    print("Every outline matches the fingerprint BEM4AI recorded before its cleanup")

    sample["metric_crs"] = sample["city_key"].map(lambda key: manifest[key]["metric_crs"])

    attributes = pd.DataFrame({
        "sample_id": sample["sample_id"],
        "source_sample_id": sample["source_sample_id"],
        "footprint_fingerprint": sample["footprint_fingerprint"],
        "city_key": sample["city_key"],
        "country": sample["country"],
        "osm_type": sample["osm_type"],
        "metric_crs": sample["metric_crs"],
        "review_check_selected": sample["source_sample_id"].map(decisions["by"]).eq("check_review")
                                 & sample["source_sample_id"].map(decisions["decision"]).eq("remove"),
        "bem4ai_n_vertices": sample["n_vertices"].astype(int),
        "bem4ai_cleanup_cut_m2": sample["cleanup_cut_m2"].astype(float),
        "bem4ai_cleanup_filled_m2": sample["cleanup_filled_m2"].astype(float),
    })
    footprints = gpd.GeoDataFrame(attributes, geometry=sample["geometry_osm"].to_numpy(), crs="EPSG:4326")
    footprints_bem4ai = gpd.GeoDataFrame(attributes[["sample_id"]].copy(), geometry=sample.geometry.to_numpy(),
                                         crs="EPSG:4326")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    footprints.to_file(args.output, layer="footprints", driver="GPKG")
    footprints_bem4ai.to_file(args.output, layer="footprints_bem4ai", driver="GPKG")
    print(f"Wrote {args.output} (layers footprints, footprints_bem4ai; WGS84)")
    print("Geometry types of the OSM outlines:", footprints.geom_type.value_counts().to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
