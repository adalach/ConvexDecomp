#!/usr/bin/env python3
"""Write the public copy of the BEM4AI footprint sample: geometry only, with anonymous ids.

`tools/import_bem4ai_footprints.py` writes the sample with BEM4AI's ids, OSM element ids and the
city and country of every footprint (``data/bem4ai/osm_footprints_bem4ai_source.gpkg``, local).
This tool writes the copy the repository ships and the OSM notebooks read
(``data/bem4ai/osm_footprints_bem4ai_sample.gpkg``):

- layer ``footprints``: ``sample_id`` (``osm_0001`` ...), ``metric_crs`` (the UTM zone the adapter
  projects the footprint to) and the OSM outline as mapped, in WGS84;
- layer ``footprints_bem4ai``: ``sample_id`` and BEM4AI's cleaned version of the same footprint.

The ids are given in a random order (``--seed``), so they say nothing about the city. The map from
the anonymous ids back to BEM4AI's ``sample_id`` and ``source_sample_id`` goes to
``data/bem4ai/sample_ids.csv``, which stays local::

    python tools/anonymize_bem4ai_sample.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "bem4ai"
SOURCE = DATA / "osm_footprints_bem4ai_source.gpkg"
OUTPUT = DATA / "osm_footprints_bem4ai_sample.gpkg"
ID_MAP = DATA / "sample_ids.csv"
PUBLIC_COLUMNS = ("sample_id", "metric_crs", "geometry")


def anonymize(source: Path, output: Path, id_map: Path, seed: int) -> pd.DataFrame:
    footprints = gpd.read_file(source, layer="footprints")
    bem4ai = gpd.read_file(source, layer="footprints_bem4ai")
    order = np.random.default_rng(seed).permutation(len(footprints))
    new_ids = pd.Series([f"osm_{k + 1:04d}" for k in range(len(footprints))], index=footprints.index[order])
    ids = pd.DataFrame({"sample_id": new_ids.sort_index(), "bem4ai_sample_id": footprints["sample_id"],
                        "source_sample_id": footprints.get("source_sample_id", footprints["sample_id"])})
    lookup = dict(zip(ids["bem4ai_sample_id"], ids["sample_id"]))

    public = footprints.assign(sample_id=footprints["sample_id"].map(lookup))[list(PUBLIC_COLUMNS)]
    public = public.sort_values("sample_id").reset_index(drop=True)
    public_bem4ai = bem4ai.assign(sample_id=bem4ai["sample_id"].map(lookup))[["sample_id", "geometry"]]
    public_bem4ai = public_bem4ai.sort_values("sample_id").reset_index(drop=True)
    if public["sample_id"].isna().any() or public_bem4ai["sample_id"].isna().any():
        raise ValueError("a footprint of one layer is missing from the other")

    if output.exists():
        output.unlink()
    public.to_file(output, layer="footprints", driver="GPKG")
    public_bem4ai.to_file(output, layer="footprints_bem4ai", driver="GPKG")
    ids.sort_values("sample_id").to_csv(id_map, index=False)
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--id-map", type=Path, default=ID_MAP)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    ids = anonymize(args.source, args.output, args.id_map, args.seed)
    print(f"{args.output.relative_to(ROOT)}: {len(ids)} footprints; id map in {args.id_map.relative_to(ROOT)} (local)")


if __name__ == "__main__":
    main()
