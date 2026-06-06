#!/usr/bin/env python3
"""Prepare an anonymized projected OSM footprint GeoPackage for the public pipeline."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

__all__ = ["main", "parse_args", "read_gdf"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input vector file or parquet file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/osm/osm_building_footprints_openstreetmap_odbl.gpkg"),
        help="Output GeoPackage path used by OSM_1_Preprocessing.ipynb",
    )
    parser.add_argument("--layer", help="Optional input layer name for multi-layer vector sources")
    parser.add_argument(
        "--output-layer",
        default="footprints",
        help="Output GeoPackage layer name",
    )
    parser.add_argument("--sample-size", type=int, help="Optional maximum row count after loading")
    parser.add_argument("--seed", type=int, default=42, help="Random seed when sampling")
    parser.add_argument(
        "--id-prefix",
        default="osm",
        help="Prefix for anonymized sample identifiers",
    )
    parser.add_argument(
        "--overwrite-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether to overwrite an existing output GeoPackage",
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

    if gdf.crs.is_geographic:
        target_crs = gdf.estimate_utm_crs()
        if target_crs is None:
            raise SystemExit("Could not estimate a projected CRS from the input.")
        gdf = gdf.to_crs(target_crs)

    # The public sample keeps only anonymized ids plus footprint geometry.
    gdf = gdf[["geometry"]].copy()
    gdf.insert(0, "sample_id", [f"{args.id_prefix}_{i:04d}" for i in range(1, len(gdf) + 1)])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        if not args.overwrite_existing:
            raise SystemExit(f"Output already exists: {args.output}")
        args.output.unlink()
    gdf.to_file(args.output, layer=args.output_layer, driver="GPKG")
    print(f"Saved {len(gdf)} footprints to {args.output} (layer={args.output_layer})")
    print(f"Projected CRS: {gdf.crs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
