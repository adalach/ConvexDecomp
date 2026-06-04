#!/usr/bin/env python3
"""Prepare a projected building table for the public OSM notebook pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input vector file or parquet file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/interim/buildings_gdf.pkl"),
        help="Output pickle path used by OSM_Preprocessing.ipynb",
    )
    parser.add_argument("--layer", help="Optional layer name for multi-layer vector sources")
    parser.add_argument("--sample-size", type=int, help="Optional maximum row count after loading")
    parser.add_argument("--seed", type=int, default=42, help="Random seed when sampling")
    parser.add_argument(
        "--city-key",
        help="Optional city key metadata. Defaults to the input stem.",
    )
    return parser.parse_args()


def read_gdf(path: Path, layer: str | None) -> gpd.GeoDataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return gpd.read_parquet(path)
    return gpd.read_file(path, layer=layer)


def main() -> int:
    args = parse_args()
    gdf = read_gdf(args.input, args.layer)
    if gdf.empty:
        raise SystemExit("Input contains no features.")
    if gdf.crs is None:
        raise SystemExit("Input must have a CRS.")

    if args.sample_size and len(gdf) > args.sample_size:
        gdf = gdf.sample(n=args.sample_size, random_state=args.seed).copy()
    else:
        gdf = gdf.copy()

    gdf = gdf.reset_index(drop=False).rename(columns={"index": "building_id"})
    gdf["source_geom_type"] = gdf.geometry.geom_type.astype(str)

    if gdf.crs.is_geographic:
        target_crs = gdf.estimate_utm_crs()
        if target_crs is None:
            raise SystemExit("Could not estimate a projected CRS from the input.")
        gdf = gdf.to_crs(target_crs)

    centroids_wgs84 = gdf.to_crs(4326).geometry.centroid
    gdf["lon"] = centroids_wgs84.x
    gdf["lat"] = centroids_wgs84.y
    gdf["offset_x"] = 0.0
    gdf["offset_y"] = 0.0
    gdf["sample_id"] = pd.RangeIndex(len(gdf))
    gdf["city_key"] = args.city_key or args.input.stem
    gdf["osm_pbf"] = args.input.name
    if "building" not in gdf.columns:
        gdf["building"] = None

    keep_cols = [
        "sample_id",
        "city_key",
        "osm_pbf",
        "building_id",
        "building",
        "source_geom_type",
        "lon",
        "lat",
        "offset_x",
        "offset_y",
        "geometry",
    ]
    gdf = gdf[keep_cols].copy()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_pickle(args.output)
    print(f"Saved {len(gdf)} buildings to {args.output}")
    print(f"Projected CRS: {gdf.crs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
