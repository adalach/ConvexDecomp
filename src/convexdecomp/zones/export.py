"""Turn zone provenance records into portable two-layer artifacts."""

from __future__ import annotations

import warnings
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

import geopandas as gpd
import pandas as pd

from convexdecomp.zones.records import InterfaceRecord, PartRecord

__all__ = [
    "ZONES_LAYER",
    "INTERFACES_LAYER",
    "parts_to_geodataframe",
    "interfaces_to_geodataframe",
    "write_zone_artifacts",
]

ZONES_LAYER = "zones"
INTERFACES_LAYER = "interfaces"


def parts_to_geodataframe(
    parts: Sequence[PartRecord],
    *,
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """One row per convex part, carrying the full provenance chain.

    ``space_part_count`` and ``space_is_split`` are derived here so a consumer
    can select the spaces that were artificially split without a second pass.
    """
    counts: Dict[str, int] = {}
    for part in parts:
        counts[part.space_id] = counts.get(part.space_id, 0) + 1

    rows: List[Dict[str, Any]] = []
    for part in parts:
        geometry = part.geometry
        row = {
            "part_id": part.part_id,
            "container_id": part.container_id,
            "space_id": part.space_id,
            "zone_id": part.zone_id,
            "part_index": int(part.part_index),
            "space_label": part.space_label,
            "zone_role": part.zone_role,
            "area": float(geometry.area),
            "n_vertices": int(len(geometry.exterior.coords) - 1),
            "space_part_count": int(counts.get(part.space_id, 0)),
            "space_is_split": bool(counts.get(part.space_id, 0) > 1),
        }
        for key, value in (part.attributes or {}).items():
            row.setdefault(key, value)
        row["geometry"] = geometry
        rows.append(row)

    if not rows:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=crs)


def interfaces_to_geodataframe(
    interfaces: Sequence[InterfaceRecord],
    *,
    crs: Any = None,
) -> gpd.GeoDataFrame:
    """One row per boundary sub-segment, i.e. one row per prospective wall face."""
    if not interfaces:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs=crs)
    rows = [asdict(record) for record in interfaces]
    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=crs)


def write_zone_artifacts(
    zones_gdf: gpd.GeoDataFrame,
    interfaces_gdf: gpd.GeoDataFrame,
    output_path: Path | str,
    *,
    fmt: str = "gpkg",
) -> Dict[str, Path]:
    """Write the two layers next to each other and return the written paths.

    The default writes one GeoPackage holding a ``zones`` and an ``interfaces``
    layer. ResPlan floorplans use a local metric frame with no CRS, which the
    GeoPackage driver accepts with an undefined spatial reference. Pass
    ``fmt="parquet"`` to write two GeoParquet files instead, which needs
    ``pyarrow``.
    """
    output_path = Path(output_path)
    if fmt not in {"gpkg", "parquet"}:
        raise ValueError(f"Unsupported format {fmt!r}; use 'gpkg' or 'parquet'.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "gpkg":
        target = output_path.with_suffix(".gpkg")
        if target.exists():
            target.unlink()
        with warnings.catch_warnings():
            # A missing CRS is expected for local floorplan frames and is documented above.
            warnings.filterwarnings("ignore", message=".*'crs' was not provided.*")
            zones_gdf.to_file(target, layer=ZONES_LAYER, driver="GPKG")
            interfaces_gdf.to_file(target, layer=INTERFACES_LAYER, driver="GPKG")
        return {ZONES_LAYER: target, INTERFACES_LAYER: target}

    stem = output_path.with_suffix("")
    zones_target = Path(f"{stem}_{ZONES_LAYER}.parquet")
    interfaces_target = Path(f"{stem}_{INTERFACES_LAYER}.parquet")
    zones_gdf.to_parquet(zones_target)
    interfaces_gdf.to_parquet(interfaces_target)
    return {ZONES_LAYER: zones_target, INTERFACES_LAYER: interfaces_target}
