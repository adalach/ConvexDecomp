"""Local metric frames: every footprint in metres, with the centre of its bounding box at (0, 0).

`to_local_frames` projects each footprint from WGS84 to the metric CRS of its city (``metric_crs``,
the UTM zone BEM4AI chose for the city) and moves it so that the centre of its bounding box is the
origin, as BEM4AI Stage 4 does; ``bbox_cx_m`` and ``bbox_cy_m`` keep that centre, so a footprint can
be put back on the map. Nothing else changes: the outline is not repaired or simplified.

A footprint table then carries one geometry column per step of the adapter, and `sync_geometry`
points the plain ``geometry`` column at whichever is current.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.affinity import translate

__all__ = ["local_frame", "sync_geometry", "to_local_frames"]


def local_frame(geometries_metric) -> tuple[list, np.ndarray, np.ndarray]:
    """Footprints in a metric CRS moved so that the centre of each one's bounding box is the origin.

    Returns the moved footprints and the centres (``bbox_cx_m``, ``bbox_cy_m``) they were moved from.
    """
    moved, cx, cy = [], [], []
    for geom in geometries_metric:
        minx, miny, maxx, maxy = geom.bounds
        x, y = (minx + maxx) / 2.0, (miny + maxy) / 2.0
        moved.append(translate(geom, xoff=-x, yoff=-y))
        cx.append(x)
        cy.append(y)
    return moved, np.asarray(cx), np.asarray(cy)


def to_local_frames(gdf: gpd.GeoDataFrame, geometry_cols: dict[str, str]) -> gpd.GeoDataFrame:
    """Every footprint in its local frame.

    `gdf` is in WGS84 with ``metric_crs`` per row, or already in local metres (no CRS, then only
    centred). `geometry_cols` maps each geometry column to the local column it becomes, for example
    ``{"geometry": "geom_local_raw", "geometry_bem4ai": "geom_local_bem4ai"}``; the first is the
    outline whose bounding box sets the frame, and every other column moves with it.
    """
    out = gdf.copy()
    (main, main_local), *others = geometry_cols.items()
    local = {target: pd.Series(index=out.index, dtype=object) for target in geometry_cols.values()}
    cx = pd.Series(np.nan, index=out.index)
    cy = pd.Series(np.nan, index=out.index)
    groups = out.groupby("metric_crs").groups if gdf.crs is not None else {None: out.index}
    for crs, index in groups.items():
        def projected(column):
            series = gpd.GeoSeries(out.loc[index, column], crs=gdf.crs)
            return series.to_crs(crs).to_numpy() if crs is not None else series.to_numpy()
        moved, x, y = local_frame(projected(main))
        local[main_local].loc[index] = moved
        cx.loc[index], cy.loc[index] = x, y
        for column, target in others:
            local[target].loc[index] = [translate(g, -a, -b) for g, a, b in zip(projected(column), x, y)]
    for target, values in local.items():
        out[target] = values
    out["bbox_cx_m"], out["bbox_cy_m"] = cx, cy
    return sync_geometry(pd.DataFrame(out).drop(columns=list(geometry_cols)), main_local)


def sync_geometry(gdf: pd.DataFrame, geom_col: str) -> gpd.GeoDataFrame:
    """Mirror a named geometry column into ``geometry`` and make it active (local frames have no CRS)."""
    gdf = gdf.copy()
    gdf["geometry"] = gdf[geom_col]
    return gpd.GeoDataFrame(gdf, geometry="geometry", crs=None)
