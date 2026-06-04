# Helper Modules

This folder contains the reusable geometry logic behind the SVG and OSM floorplan notebooks.

## Stable Entry Points

- `svg_footprint_loader.py`
  - Generic SVG footprint loader.
  - Use `load_svg_footprints(...)` for arbitrary SVG files.
  - Supports straight-segment `path`, `rect`, nested `matrix(...)` transforms, optional fill/stroke filtering, and custom element predicates.

- `svg_notebook_support.py`
  - Notebook support only.
  - Provides `find_project_root(...)` and `plot_local_geometry_grid(...)`.
  - Keeps plotting/layout code out of notebooks.

- `osm_perimeter_builder.py`
  - Builds the first perimeter/interior split from a footprint polygon.
  - Main entry points:
    - `PerimeterConfig`
    - `build_perimeter_zones_for_gdf(...)`
    - `summarize_perimeter_results(...)`
  - Stores offset correspondence metadata used later for trapezoid construction.

- `osm_perimeter_subdivider.py`
  - Splits perimeter shells into trapezoids, collapsed-corner triangles, and polygonal remainders.
  - Main entry points:
    - `PerimeterSubdivisionConfig`
    - `subdivide_perimeter_gdf(...)`
  - Uses only existing ring vertices in the active pipeline.

- `polygon_convexity.py`
  - Shared strict convexity checker for single polygons and batches.
  - Main entry points:
    - `is_convex_polygon(...)`
    - `convexity_mask(...)`

- `zone_piece_pipeline.py`
  - Post-perimeter zone-piece helpers.
  - Main entry points:
    - `build_zone_convexity_tables(...)`
    - `decompose_zone_pieces_to_convex_parts(...)`
  - This is the preferred place for piece-level convexity bookkeeping and final convex decomposition assembly.
  - Uses only `osm_convex_decomposer.py` for active convex decomposition.

- `osm_convex_decomposer.py`
  - Primary polygon-level convex decomposition for ordinary polygons.
  - Main entry points:
    - `ConvexDecompositionConfig`
    - `decompose_polygon_with_stats(...)`
    - `decompose_polygon_best(...)`

- `hole_splitter.py`
  - Converts polygons with holes into holeless polygon sets while preserving area.
  - Important preprocessing step before convex decomposition of hole-bearing interiors.

## Pipeline Order

For the current SVG notebook, the recommended order is:

1. `load_svg_footprints(...)`
2. `build_perimeter_zones_for_gdf(...)`
3. `subdivide_perimeter_gdf(...)`
4. `build_zone_convexity_tables(...)`
5. `decompose_zone_pieces_to_convex_parts(...)`

The notebook should mostly do:
- path/config setup,
- function calls,
- result inspection.

The geometry logic itself should stay in the helper modules above.

## Starting From Any SVG

To use a different SVG file, prefer:

```python
from notebooks.helpers.svg_footprint_loader import load_svg_footprints

gdf = load_svg_footprints(
    svg_path,
    meters_per_svg_unit=1.0,
    flip_y=True,
    fill="rgb(235,235,235)",
    stroke="black",
    sample_prefix="shape",
)
```

Or provide a custom `element_filter` if fill/stroke filtering is not enough.

The main thing to change should be the loader call and its filter arguments, not the downstream perimeter/convexity/decomposition code.

## Notes

- `osm_perimeter_builder.py` still falls back to `buffer(0)` geometry repair when `make_valid(...)` is unavailable or fails. This is an active robustness fallback, not dead code.
