#!/usr/bin/env python3
"""Import a random sample of the BEM4AI Stage 3 footprints as an OSM input of ConvexDecomp.

BEM4AI Stage 3 (notebook 3, ``data/interim/buildings_gdf.pkl``) draws its buildings from real OSM
footprints and stores each footprint after its own cleanup (parts under 1 m cut off, thin holes
filled, collinear vertices dropped). ConvexDecomp's OSM preprocessing is to start from the outline
as it is mapped, so this tool reads that outline again from the same city extract BEM4AI drew it
from (``data/city-assets/OSM_PBF/<city>.osm.pbf``), repairs it as BEM4AI does, and checks it
against the footprint fingerprint BEM4AI recorded before its cleanup.

Only the 2D geometry is kept. Each footprint is projected to the UTM zone of its city (the metric
CRS of BEM4AI's candidate tables) and moved so that the centre of its bounding box is the origin,
as BEM4AI Stage 4 does (`convexdecomp.osm.local_frames.local_frame`); the file therefore has no
CRS, and ``metric_crs``, ``bbox_cx_m`` and ``bbox_cy_m`` place each footprint back on the map. The
columns carry BEM4AI's names. Both layers share the frame:

- ``footprints``: the OSM outline, read by ``OSM_1_Preprocessing.ipynb``
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
from shapely.affinity import translate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from convexdecomp.osm.local_frames import local_frame  # noqa: E402

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
    """``source_sample_id`` -> the repaired OSM outline (WGS84) of the wanted buildings of one extract."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        city_buildings = helpers.load_city_buildings(pbf_path)
    keys = set(zip(wanted["building_id"].astype("int64"), wanted["osm_type"].astype(str)))
    ids = city_buildings["id"].astype("int64").to_numpy()
    types = city_buildings["osm_type"].astype(str).to_numpy()
    hit = np.array([(i, t) in keys for i, t in zip(ids, types)], dtype=bool)
    by_key = {(int(i), str(t)): helpers.make_geometry_valid(g)
              for i, t, g in zip(ids[hit], types[hit], city_buildings.geometry.to_numpy()[hit])}
    return {sid: by_key.get((int(i), str(t)))
            for sid, i, t in zip(wanted["source_sample_id"], wanted["building_id"], wanted["osm_type"])}


def to_local_frame(raw_wgs84: list, cleaned_wgs84: list, metric_crs: str) -> tuple[list, list, list]:
    """Both geometries in the UTM zone, moved so that the bounding-box centre of the outline is the origin."""
    raw = gpd.GeoSeries(raw_wgs84, crs="EPSG:4326").to_crs(metric_crs)
    cleaned = gpd.GeoSeries(cleaned_wgs84, crs="EPSG:4326").to_crs(metric_crs)
    raw_local, cx, cy = local_frame(raw.to_numpy())
    cleaned_local = [translate(shape, -x, -y) for shape, x, y in zip(cleaned.to_numpy(), cx, cy)]
    return raw_local, cleaned_local, list(zip(cx, cy))


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
    fingerprints = sample["geometry_osm"].map(helpers.footprint_fingerprint)
    differ = fingerprints.ne(sample["footprint_fingerprint"])
    if differ.any():
        raise SystemExit(f"{int(differ.sum())} outlines differ from the fingerprint BEM4AI recorded, "
                         f"e.g. {sample.loc[differ, 'source_sample_id'].tolist()[:10]}; was the extract updated?")
    print("Every outline matches the fingerprint BEM4AI recorded before its cleanup")

    raw_local, cleaned_local = [None] * len(sample), [None] * len(sample)
    origins = [None] * len(sample)
    sample["metric_crs"] = sample["city_key"].map(lambda key: manifest[key]["metric_crs"])
    for metric_crs, rows in sample.groupby("metric_crs"):
        r, c, o = to_local_frame(rows["geometry_osm"].tolist(), rows.geometry.tolist(), metric_crs)
        for k, position in enumerate(rows.index):
            raw_local[position], cleaned_local[position], origins[position] = r[k], c[k], o[k]

    attributes = pd.DataFrame({
        "sample_id": sample["sample_id"],
        "source_sample_id": sample["source_sample_id"],
        "footprint_fingerprint": sample["footprint_fingerprint"],
        "city_key": sample["city_key"],
        "country": sample["country"],
        "osm_type": sample["osm_type"],
        "metric_crs": sample["metric_crs"],
        "bbox_cx_m": [o[0] for o in origins],
        "bbox_cy_m": [o[1] for o in origins],
        "review_check_selected": sample["source_sample_id"].map(decisions["by"]).eq("check_review")
                                 & sample["source_sample_id"].map(decisions["decision"]).eq("remove"),
        "bem4ai_n_vertices": sample["n_vertices"].astype(int),
        "bem4ai_cleanup_cut_m2": sample["cleanup_cut_m2"].astype(float),
        "bem4ai_cleanup_filled_m2": sample["cleanup_filled_m2"].astype(float),
    })
    footprints = gpd.GeoDataFrame(attributes, geometry=raw_local, crs=None)
    footprints_bem4ai = gpd.GeoDataFrame(attributes[["sample_id"]].copy(), geometry=cleaned_local, crs=None)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        args.output.unlink()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="'crs' was not provided")  # local frames have none
        footprints.to_file(args.output, layer="footprints", driver="GPKG")
        footprints_bem4ai.to_file(args.output, layer="footprints_bem4ai", driver="GPKG")
    print(f"Wrote {args.output} (layers footprints, footprints_bem4ai; local metres, no CRS)")
    print("Geometry types of the OSM outlines:", footprints.geom_type.value_counts().to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
