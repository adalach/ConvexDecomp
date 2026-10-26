# Convex zones

`convexdecomp.convex_zones` turns a building's perimeter zones into convex zones. Every zone of the corner-ray
perimeter (`convexdecomp.corner_perimeter`) goes to the engine (`convexdecomp.osm.decompose`) as it is: a convex
zone comes back as one zone, the others are cut. A zone the engine cannot cut (a zone thinner than the narrowest
zone allowed, or one with a narrow arm) is merged with a neighbouring zone and the union decomposed; when merging
gives up, the whole footprint is decomposed instead. The convex zones always tile the footprint exactly.

This is the decomposition step of `notebooks/OSM_2_Decomposition.ipynb` for one building, with its settings as one
object with page metadata, and a preview for a settings page. The notebook's `run_building_decomposition(...,
fallback="merge")` (`convexdecomp.osm.diagnostics`) runs the same code for a table of buildings.

The package needs only numpy, shapely and ConvexDecomp's own engine and perimeter; it does not draw.

## Use

```python
from shapely.geometry import box
from convexdecomp.corner_perimeter import corner_ray_perimeter
from convexdecomp.convex_zones import ZoneConfig, decompose_zones

footprint = box(0, 0, 20, 12)
perimeter = corner_ray_perimeter(footprint)
result = decompose_zones(perimeter.zones, footprint=footprint)                  # the default settings
result = decompose_zones(perimeter.zones, ZoneConfig(min_zone_width_m=1.5), footprint=footprint)

result.ok                    # every piece has a convex decomposition
result.fallback              # None, "merge" or "whole"
for zone, k in result.parts:  # the convex zones, each with its piece in result.pieces
    piece = result.pieces[k]  # kind (zone, thin, core, merged, footprint), sources (the perimeter zones it holds)
```

`zones` are `(polygon, kind)` pairs as `PerimeterResult.zones` holds them (kinds `zone`, `thin`, `core`), or zones
edited by hand in the same form; a plain polygon counts as a zone. `footprint` is the outline the whole-footprint
fallback decomposes (the union of the zones when it is not given). From the command line:

```bash
python -m convexdecomp.convex_zones settings                           # the settings table as JSON
python -m convexdecomp.convex_zones zones "POLYGON ((...))" min_zone_width_m=1.5
```

## How a building is decomposed

1. **Every zone on its own.** The zones are searched in their order with the core parts last. The engine's search
   for one zone is a beam search over the ways to cut it: a cut starts at a reflex corner and runs along one of its
   two walls carried on, or along the bisector of its angle, to the first wall it meets. No cut may leave a zone
   thinner than 1.2 m or smaller than 2 m², and no cut may make a corner under 30° unless no cut of the piece passes
   that rule. Of the convex variants found, the one with the lowest score is kept: the number of zones plus 20 times
   one minus their mean compactness.
2. **Merge a zone that cannot be cut.** The first zone without a decomposition is tried with each neighbour (a zone
   sharing at least 0.05 m of edge), longest shared edge first; the first union that decomposes replaces the two.
   When none does, the zone is merged with the neighbour it shares most edge with and tried again from there, as
   long as the merged piece holds at most half the building's floor area. This repeats until every piece is
   decomposed.
3. **The whole footprint.** When merging gives up, the whole footprint is decomposed instead of the zones. When that
   fails too, the building keeps its zones as they were and has no decomposition.

A footprint the perimeter cannot zone is decomposed whole as one piece of kind `footprint`, with no fallback.
`decompose_pieces(zones, decompose, ...)` is the same with the engine call given as a function (a polygon in, the
engine's result dict out) and the fallback's settings as arguments: a caller that stores every piece's search, or
stops a search after a time limit, passes its own.

## Settings

`ZoneConfig` holds every setting; the defaults are those of `OSM_2_Decomposition.ipynb`. `engine_config(config)`
gives the engine's `ConvexDecompositionConfig`; `engine_config(config, **PAPER_2026_SEARCH)` searches as in the
EG-ICE 2026 paper. `settings_table()` gives the five sections below as plain data, each field with its kind
(`switch`, `integer` or `number`), default, title, help, unit, the values a run accepts (`ge`, `le`), a slider range
that holds the default (`lo`, `hi`, `step`), the switch it `needs` and whether it is `advanced`.

**Zone rules.** What every convex zone must satisfy. A cut that would leave a zone thinner than the narrowest zone
or smaller than the smallest zone is never made. The corner rule is relaxed where no cut of a piece passes it.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `min_zone_width_m` | 1.2 m | 0 to 3 | **Narrowest zone.** No zone may be thinner than this: its smallest width in any direction. A hard rule: a piece that cannot be cut into zones this wide, a thin convex piece included, has no decomposition of its own. 0 turns the rule off. |
| `min_corner_deg` | 30 deg | 0 to 60 | **Sharpest new corner.** No cut may make a corner sharper than this; the outline's own sharp corners never block a cut. Relaxed where no cut of a piece passes it. 0 turns the rule off. |
| `min_area_m2` | 2 m² | 0 to 10 | **Smallest zone.** No cut may leave a zone smaller than this. A convex piece that is smaller stays as it is (one under 0.5 m² has no decomposition). |

**Score.** Of the convex variants the search finds for a piece, the one with the lowest score is kept: the number of
zones plus the compactness weight times one minus the zones' mean compactness.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `weight_compactness` | 20 | 0 to 50 | **Compactness weight.** How much the zones' mean compactness (4 pi area / perimeter squared: 0.785 for a square) counts against their number: at 20, zones that are 0.05 more compact on average are worth one zone more. |
| `weight_parts`<br>(advanced) | 1 | 0 to 5 | **Zone count weight.** What each zone costs in the score. |
| `area_weighting`<br>(advanced) | 0 | 0 to 1 | **Area weighting of the compactness.** 0 lets every zone count the same in the mean compactness; 1 weights each zone by its area, as the EG-ICE 2026 paper did, so a small or thin zone hardly counts. |
| `weight_narrow`<br>(advanced) | 0 | 0 to 10 | **Narrow zone cost.** Cost of a zone narrower than the narrow width, in proportion to how much narrower it is: at 5 and 2 m, a 1 m zone costs as much as 2.5 zones more. 0 turns the cost off. |
| `narrow_width_m`<br>(advanced) | 2 m | 0.5 to 4 | **Narrow below.** The width below which a zone costs the narrow zone cost. |

**Cuts.** A cut starts at a reflex corner and runs along one of the corner's two walls carried on, or along the
bisector of its angle, to the first wall it meets. Every cut splits a piece into two.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `bisector_always` | on | switch | **Bisector cuts.** Every reflex corner also offers the cut along the bisector of its angle, next to its two walls carried on. Otherwise the bisector is tried only where neither wall gives a cut. |
| `min_concavity_deg`<br>(advanced) | 2 deg | 0 to 10 | **Reflex from.** A corner that turns inwards by less than this counts as straight and starts no cut. |
| `cut_snap_vertex_tolerance_m`<br>(advanced) | 0.1 m | 0 to 0.5 | **Snap a cut to a corner within.** A cut that passes this close to a corner before reaching a wall ends at the corner, so no hairline sliver is left beside it. |

**Search.** A beam search over the ways to cut a piece: it starts shallow and narrow and searches deeper, then
wider, only while it has found no convex variant. It stops after enough convex variants or dead ends.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `adaptive` | on | switch | **Search deeper only where needed.** Start with the first depth and width; while no convex variant is found, search one cut deeper, then one state wider, up to the deepest search and the widest beam. Off: search at the deepest and widest at once. |
| `start_depth`<br>(needs `adaptive`) | 4 | 1 to 24 | **First depth.** How many cuts in a row the first search makes at most. |
| `start_width`<br>(needs `adaptive`) | 3 | 1 to 16 | **First width.** How many open states the first search keeps. |
| `depth` | 24 | 1 to 40 | **Deepest search.** The most cuts in a row the search makes. |
| `width` | 4 | 1 to 16 | **Widest beam.** The most open states the search keeps. |
| `max_variants` | 32 | 1 to 128 | **Enough convex variants.** The search of a piece stops once it has found this many convex variants. |
| `max_dead_ends` | 500 | 50 to 5000 | **Dead ends before giving up.** The search of a piece gives up after this many dead ends (states it cannot cut further, or at the depth limit, without being convex). It also bounds the search's time. |
| `max_reflex_per_node`<br>(advanced) | 8 | 1 to 24 | **Reflex corners tried per state.** How many reflex corners of a piece offer cuts in one state, the most promising first. |
| `depth_step`<br>(needs `adaptive`, advanced) | 1 | 1 to 8 | **Depth added per retry.** How many cuts deeper each new search goes. |
| `width_step`<br>(needs `adaptive`, advanced) | 1 | 1 to 8 | **Width added per retry.** How many states wider each new search goes once it is at the deepest search. |
| `beam_area_weighting`<br>(advanced) | 1 | 0 to 1 | **Area weighting of the beam.** The area weighting of the score that ranks the open states while the search runs. 1 weights each piece by its area, so a state that has cut one neat block off a big remainder is not dropped. |

**When a piece cannot be cut.** A piece the search cannot make convex is merged with a neighbouring piece and the
union decomposed. When merging gives up, the whole footprint is decomposed instead. A building where neither works
has no decomposition.

| Setting | Default | Page range | What it does |
| --- | --- | --- | --- |
| `merge_failed` | on | switch | **Merge a piece that cannot be cut.** A piece the search cannot make convex is merged with a neighbour (sharing the longest edge first) and the union decomposed; the first union that decomposes replaces the two pieces. When none does, the piece is merged with the neighbour it shares most edge with and tried again from there. |
| `merge_max_share`<br>(needs `merge_failed`) | 0.5 | 0.1 to 1 | **Largest merged piece.** Merging gives up when a merged piece would hold more than this share of the building's floor area. |
| `min_shared_edge_m`<br>(needs `merge_failed`, advanced) | 0.05 m | 0 to 1 | **Neighbours share at least.** Two pieces are neighbours when they share at least this much edge. |
| `whole_footprint` | on | switch | **Whole footprint as the last resort.** When a piece cannot be cut (and merging is off or gives up), the whole footprint is decomposed instead of the perimeter's zones; the building keeps its zones when that fails too. |

## Previews for a settings page

`preview(footprint, zones, config)` decomposes one building and never raises (without `zones` it runs the corner-ray
perimeter first, with `perimeter=CornerRayConfig(...)`):

- `layers`: shapely geometries in the footprint's frame, `before` the decomposition (`footprint`,
  `perimeter_zones`, `thin_zones`, `core`, `not_convex` as outlines: the perimeter's zones as its own page draws
  them) and `after` it (`perimeter_parts`, `core_parts`, `merged_parts`, `footprint_parts`: the convex zones by the
  piece they come from; `failed_pieces`; `zone_lines`, the boundaries between pieces; `removed_lines`, the zone
  boundaries a merge or the whole footprint removed; `cut_lines`, the engine's cuts), in drawing order;
  `LAYER_TITLES` names them.
- `numbers`: `NUMBERS` in order, `(key, label, unit)`: the zones from the perimeter and how many were convex
  already, the zones the engine could not cut, merges and the floor area they cover, whether the whole footprint was
  decomposed, the pieces left without a decomposition, the convex zones and cuts, the narrowest and smallest zone,
  the zones narrower than 2 m and under 5 m², their mean compactness, the pieces cut with the corner rule relaxed or
  searched deeper or wider, and the search time with the part spent on attempts not kept.
- `fate`: `{"status": "kept", "fallback": None | "merge" | "whole"}`, or `{"status": "dropped", "reason":
  "decomposition_failed", "detail": {...}}` for a building with a piece left without a decomposition, or the reason
  `decomposition_error` when the engine raised.
- `seconds` and `result` (the `BuildingZones`, or `None` after an error).

`zone_records(result)` gives the convex zones as rows (`zone`, `piece`, `kind`, `sources`, `area_m2`,
`min_width_m`, `compactness`, `geometry`), `piece_records(result)` the pieces (`piece`, `kind`, `sources`,
`decomposed`, `parts`, `fallback_used`, `attempts`, `dead_ends`, `terminated_by`, `seconds`, `geometry`).

## How it was checked

On the 990 footprints of the BEM4AI sample's working set (`data/bem4ai/subset_buildings_gdf.pkl`, written by
`OSM_1_Preprocessing.ipynb`), with the defaults, `decompose_zones` on each footprint's corner-ray zones gives the
zones `OSM_2_Decomposition.ipynb` saved in `osm_zones.gpkg`, zone for zone, and so does
`run_building_decomposition(..., fallback="merge")`, which runs this package:

| | Buildings |
| --- | --- |
| Fully convex | 988 |
| Without a fallback | 923 |
| After merging (76 merges) | 67 |
| After decomposing the whole footprint | 0 |
| Without a decomposition | 2 (osm_0517, osm_0719) |

They have 9,048 convex zones: a median of 7 per building and at most 54 (208 buildings have more than 12, 41 more
than 24); none narrower than 1.2 m, 319 narrower than 2 m, 232 under 5 m² and 10 under 2 m² (convex zones of the
perimeter that small, which no cut made).

**Time.** The perimeter and the zones of one building together, on three processes at a load average of about 7:
a median of 0.03 s, 90 % within 0.24 s and 99 % within 2.5 s; the slowest building that decomposes took 9.3 s
(osm_0798, after one merge; 2.8 s on a quiet machine). A building that fails takes the longest: on a quiet machine
osm_0719 took 462 s, of which 437 s went to 42 searches of merged pieces (each stops after 500 dead ends, the
slowest after 20 s) and 26 s to the whole footprint; osm_0517 took 1.2 s.

`tests/test_convex_zones.py` checks the settings table, that the default engine settings are those of
`OSM_2_Decomposition.ipynb`, a rectangle's zones and lines, the merge, the whole footprint, a footprint that cannot be
cut, the order in which the pieces are searched, an engine that raises, and eight of the perimeter's test footprints
(the zones tile them and none is narrower than 1.2 m); `tests/test_decomposition_fallback.py` the same fallback
through `run_building_decomposition`.
